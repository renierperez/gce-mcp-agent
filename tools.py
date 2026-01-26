import asyncio
import re
import subprocess
import json
import logging
import os
import firebase_admin
from firebase_admin import firestore
from google.cloud import compute_v1
from google.cloud import recommender_v1
from google.cloud import billing_v1
from google.api_core.client_options import ClientOptions
from typing import List, Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from google.api_core.exceptions import GoogleAPICallError, RetryError

# Configuration
ZONE = "us-central1-a"
REGION = "us-central1"

# Initialize Logging
logger = logging.getLogger(__name__)

# --- Helper Functions ---
def _ensure_firebase():
    """Ensures Firebase Admin is initialized."""
    try:
        firebase_admin.get_app()
    except ValueError:
        # Fallback for standalone script usage (local)
        # Assuming GOOGLE_APPLICATION_CREDENTIALS or Cloud Run enc is set
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "autonomous-agent-479317")
        firebase_admin.initialize_app(options={'projectId': project_id})

def _get_firestore_client():
    _ensure_firebase()
    return firestore.client()

def get_managed_projects() -> List[str]:
    """Fetches list of managed project IDs from Firestore."""
    try:
        db = _get_firestore_client()
        docs = db.collection('managed_projects').stream()
        return [doc.id for doc in docs]
    except Exception as e:
        logger.error(f"Error fetching managed projects: {e}")
        return []

def resolve_project_id(project_id: Optional[str] = None) -> str:
    """
    Resolves the project ID to use.
    If project_id is provided, checks if it's managed.
    If not provided, and only one managed project exists, returns it.
    Otherwise raises ValueError.
    """
    managed = get_managed_projects()
    
    if not managed:
        raise ValueError("No managed projects found in configuration.")

    if project_id:
        if project_id not in managed:
            raise ValueError(f"Project '{project_id}' is not managed by this agent. Allowed: {', '.join(managed)}")
        return project_id
    
    if len(managed) == 1:
        return managed[0]
    
    raise ValueError(f"Multiple managed projects found. Please specify one of: {', '.join(managed)}")

# --- Clients ---
_clients = {}

def get_instances_client():
    return compute_v1.InstancesClient()

def get_zone_operations_client():
    return compute_v1.ZoneOperationsClient()

def get_recommender_client():
    if "recommender" not in _clients:
        # Use default global endpoint to support all regions
        _clients["recommender"] = recommender_v1.RecommenderClient()
    return _clients["recommender"]

def get_disks_client():
    return compute_v1.DisksClient()

# --- Tools exposed to the Agent ---

async def list_managed_projects():
    """Lists all Google Cloud Projects managed by this agent."""
    try:
        projects = await asyncio.to_thread(get_managed_projects)
        if not projects:
            return "No managed projects configured."
        return "Managed Projects:\n" + "\n".join([f"- {p}" for p in projects])
    except Exception as e:
        return f"Error listing projects: {e}"

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type((GoogleAPICallError, RetryError, IOError)))
async def list_instances(project_id: str = None):
    """
    Lists all GCE instances across ALL zones in the project.
    Args:
        project_id: The Project ID to list instances from. 
                    If 'all', lists from ALL managed projects.
                    If None, tries to infer if single managed project exists.
    """
    managed_projects = []
    if project_id == "all":
        managed_projects = await asyncio.to_thread(get_managed_projects)
    else:
        try:
            pid = await asyncio.to_thread(resolve_project_id, project_id)
            managed_projects = [pid]
        except ValueError as e:
            return str(e)

    all_summaries = []
    for pid in managed_projects:
        try:
            # We use AggregatedList to get instances from ALL zones
            client = get_instances_client()
            request = compute_v1.AggregatedListInstancesRequest(project=pid)
            # Use max_results to avoid huge pages if possible/needed, though default is usually fine
            
            # Using asyncio.to_thread for the blocking gRPC call
            agg_list = await asyncio.to_thread(client.aggregated_list, request)

            instance_list = []
            for zone, response in agg_list:
                if response.instances:
                    for instance in response.instances:
                        instance_list.append(f"- {instance.name} ({instance.status}) | Zone: {zone.split('/')[-1]} | {instance.machine_type.split('/')[-1]}")
            
            if instance_list:
                all_summaries.append(f"### Project: `{pid}`\n" + "\n".join(instance_list))
            else:
                 all_summaries.append(f"### Project: `{pid}`\nNo instances found.")

        except Exception as e:
            all_summaries.append(f"Error listing instances for project {pid}: {e}")

    return "\n\n".join(all_summaries)

async def find_instance_zone(project_id: str, instance_name: str) -> Optional[str]:
    """Finds the zone of a GCE instance using AggregatedList."""
    try:
        client = get_instances_client()
        # Filter strictly by name to get fast result
        request = compute_v1.AggregatedListInstancesRequest(
            project=project_id,
            filter=f"name eq {instance_name}"
        )
        agg_list = await asyncio.to_thread(client.aggregated_list, request)
        
        for zone_path, response in agg_list:
            if response.instances:
                # zone_path format: 'projects/PROJECT/zones/ZONE'
                return zone_path.split("/")[-1]
    except Exception as e:
        logger.error(f"Error finding zone for {instance_name}: {e}")
    return None

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type((GoogleAPICallError, RetryError, IOError)))
async def start_instance(instance_name: str, project_id: str = None, zone: str = None):
    """
    Starts a specific GCE instance. Auto-detects zone if not provided.
    REQUIRES 'admin' ROLE.
    """
    # RBAC Guard
    try:
        import user_context
        user_context.require_admin()
    except PermissionError as e:
        return f"⛔ {e}"

    if instance_name == "all":
        return "Please specify an instance name. Bulk actions are restricted for safety."

    try:
        resolved_project = await asyncio.to_thread(resolve_project_id, project_id)
    except ValueError as e:
        return str(e)

    target_zone = zone
    if not target_zone:
        target_zone = await find_instance_zone(resolved_project, instance_name)
        if not target_zone:
             # Fallback to default if not found (though likely won't work if it's not there)
             # Or better, fail fast.
             return f"Instance '{instance_name}' not found in project '{resolved_project}' (checked all zones)."
    
    try:
        client = get_instances_client()
        request = compute_v1.StartInstanceRequest(
            project=resolved_project,
            zone=target_zone,
            instance=instance_name
        )
        operation = await asyncio.to_thread(client.start, request)
        return f"Instance '{instance_name}' (Project: {resolved_project}, Zone: {target_zone}) start triggered. Status: {operation.status}"
    except Exception as e:
        logger.error(f"Error starting instance {instance_name}: {e}")
        return f"Error starting instance '{instance_name}': {e}"

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type((GoogleAPICallError, RetryError, IOError)))
async def stop_instance(instance_name: str, project_id: str = None, zone: str = None):
    """
    Stops a specific GCE instance. Auto-detects zone if not provided.
    REQUIRES 'admin' ROLE.
    """
    # RBAC Guard
    try:
        import user_context
        user_context.require_admin()
    except PermissionError as e:
        return f"⛔ {e}"

    if instance_name == "all":
         return "Please specify an instance name. Bulk actions are restricted for safety."

    try:
        resolved_project = await asyncio.to_thread(resolve_project_id, project_id)
    except ValueError as e:
        return str(e)

    target_zone = zone
    if not target_zone:
        target_zone = await find_instance_zone(resolved_project, instance_name)
        if not target_zone:
             return f"Instance '{instance_name}' not found in project '{resolved_project}' (checked all zones)."

    try:
        client = get_instances_client()
        request = compute_v1.StopInstanceRequest(
            project=resolved_project,
            zone=target_zone,
            instance=instance_name
        )
        operation = await asyncio.to_thread(client.stop, request)
        return f"Instance '{instance_name}' (Project: {resolved_project}, Zone: {target_zone}) stop triggered. Status: {operation.status}"
    except Exception as e:
        logger.error(f"Error stopping instance {instance_name}: {e}")
        return f"Error stopping instance '{instance_name}': {e}"

def get_machine_type_details_sync(machine_type_url, zone, project_id):
    short_type = machine_type_url.split("/")[-1]
    
    # 1. Check for custom type
    import re
    match = re.search(r".*-custom-(\d+)-(\d+)", short_type)
    if match:
        vcpu = match.group(1)
        memory_mb = int(match.group(2))
        return vcpu, f"{memory_mb / 1024:.1f}"

    # 2. Heuristic for Standard/HighMem/HighCpu types (e.g. n2-standard-4, e2-highcpu-32)
    # Pattern: [family]-[class]-[vcpus]
    match_std = re.search(r".*-[a-z]+-(\d+)$", short_type)
    if match_std:
        vcpu = match_std.group(1)
        # Memory is harder to guess without API, but vCPU is enough for uptime.
        # We can return "?" for memory if we don't know it, or try to guess.
        # For valid uptime, we just need vCPU.
        # Let's try to fetch via API for memory, but use Heuristic vCPU as fallback or primary?
        # If API fails, we want at least vCPU to work.
        pass

    try:
        client = compute_v1.MachineTypesClient()
        request = compute_v1.GetMachineTypeRequest(
            project=project_id,
            zone=zone,
            machine_type=short_type
        )
        mt = client.get(request=request)
        return str(mt.guest_cpus), f"{mt.memory_mb / 1024:.1f}"
    except Exception as e:
        logger.warning(f"Error fetching machine type {short_type}: {e}")
        # 3. Fallback Heuristic if API failed
        if match_std:
             return match_std.group(1), "?"
        
        # 4. Fallback for shared core
        if short_type in ["e2-medium", "e2-small", "e2-micro"]: return "2", "?"
        if short_type in ["f1-micro", "g1-small"]: return "1", "?"
        
        return "?", "?"

async def get_instance_recommendations(project, zone, instance_name):
    rec_text = "None"
    savings = "0.00"
    
    recommenders = [
        "google.compute.instance.MachineTypeRecommender",
        "google.compute.instance.IdleResourceRecommender"
    ]

    try:
        client = get_recommender_client()
        
        for rec_id in recommenders:
            try:
                parent = f"projects/{project}/locations/{zone}/recommenders/{rec_id}"
                request = recommender_v1.ListRecommendationsRequest(parent=parent)
                
                # specific check to avoid long waits? usually fast enough.
                page_result = await asyncio.to_thread(client.list_recommendations, request)
                
                for r in page_result:
                    # Check if this recommendation is for our instance
                    # We check operation resources 
                    is_match = False
                    if r.content.operation_groups:
                        for op_group in r.content.operation_groups:
                            for op in op_group.operations:
                                if f"/instances/{instance_name}" in op.resource:
                                    is_match = True
                                    break
                            if is_match: break
                    
                    if is_match:
                        # Found a recommendation!
                        # If we already have one, maybe we append? For now, let's take the first significant one or arguably the largest savings.
                        # Usually simple view: show the first one found.
                        if rec_text == "None":
                            rec_text = r.description
                        else:
                            rec_text += f" | {r.description}"

                        cost = r.primary_impact.cost_projection.cost
                        if cost.currency_code == "USD":
                             units = cost.units
                             nanos = cost.nanos
                             total = abs(units + (nanos / 1e9))
                             current_savings = float(savings)
                             new_savings = current_savings + total
                             savings = f"{new_savings:.2f}"
            except Exception as e:
                # specific recommender might fail or be empty, continue to next
                logger.debug(f"Req failed for {rec_id}: {e}")
                continue

    except Exception as e:
        logger.debug(f"Error fetching recommendations for {instance_name}: {e}")
        pass
    return rec_text, savings

async def estimate_monthly_cost(instance, project_id, zone):
    """
    Rough estimation based on machine type.
    """
    cost = "0.00"
    try:
        mt = instance.machine_type.split("/")[-1]
        # Very basic map for estimation demo
        # A real implementation would query Cloud Billing Catalog API
        base_costs = {
            "e2-micro": 7.11,
            "e2-small": 14.22,
            "e2-medium": 28.44,
            "e2-standard-2": 56.88,
            "e2-standard-4": 113.76,
            "e2-standard-8": 227.52,
            "n1-standard-1": 26.50,
            "n2-standard-2": 66.00
        }
        
        val = 0.0
        if mt in base_costs:
            val = base_costs[mt]
        elif "custom" in mt:
             # Rough heuristic
             parts = mt.split("-")
             if len(parts) >= 4:
                 vcpu = int(parts[2])
                 mem = int(parts[3]) / 1024
                 val = (vcpu * 25.0) + (mem * 3.0) # Dummy formula
        
        # Add disk cost
        for d in instance.disks:
            val += (d.disk_size_gb * 0.04) # Avg $0.04/GB

        # If preemptible/spot?
        if instance.scheduling.provisioning_model == "SPOT":
            val *= 0.4 # ~60% discount

        cost = f"{val:.2f}"
    except Exception as e:
        logger.warning(f"Cost estimation error: {e}")
    return cost


# Pre-compile regex for performance
RESOURCE_PATTERN = re.compile(r"zones/([^/]+)/instances/([^/]+)")

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type((GoogleAPICallError, RetryError, IOError)))
async def fetch_zone_recommendations(project_id, zone, client, rec_map):
    """Fetches recommendations for a specific zone and populates rec_map."""
    recommenders = [
        "google.compute.instance.MachineTypeRecommender",
        "google.compute.instance.IdleResourceRecommender"
    ]
    
    for rec_id in recommenders:
        try:
            parent = f"projects/{project_id}/locations/{zone}/recommenders/{rec_id}"
            request = recommender_v1.ListRecommendationsRequest(parent=parent)
            # Use asyncio to run the sync client method
            page_result = await asyncio.to_thread(client.list_recommendations, request)
            
            for r in page_result:
                # Calculate savings
                savings = 0.0
                if r.primary_impact.cost_projection.cost:
                    cost = r.primary_impact.cost_projection.cost
                    if cost.currency_code == "USD":
                        units = cost.units
                        nanos = cost.nanos
                        # Savings are usually negative cost, but we want the absolute magnitude
                        savings = abs(units + (nanos / 1e9))

                rec_entry = {
                    "description": r.description,
                    "savings": savings,
                    "recommender": rec_id.split(".")[-1]
                }

                # Map using the unique resource ID
                # Recommender returns resources like: //compute.googleapis.com/projects/p/zones/z/instances/name
                # Reference script uses targetResources, which is more reliable than operation_groups for some types.
                resources_found = []
                if hasattr(r, "target_resources"):
                    resources_found.extend(r.target_resources)
                
                if not resources_found and r.content.operation_groups:
                     # Fallback to operation groups if target_resources is empty
                     for op_group in r.content.operation_groups:
                            for op in op_group.operations:
                                resources_found.append(op.resource)
                
                # Deduplicate resources to prevent double counting
                resources_found = list(set(resources_found))

                for resource in resources_found:
                    # Robust Match: Parse zone and name from URL string using Regex
                    # Matches "zones/{zone}/instances/{name}" ignoring prefix
                    match = RESOURCE_PATTERN.search(resource)
                    if match:
                        key = f"{match.group(1)}/{match.group(2)}"
                        logger.info(f"FOUND REC: {rec_id} for {key} -> ${savings}")
                        if key not in rec_map:
                            rec_map[key] = []
                        rec_map[key].append(rec_entry)
                    else:
                        logger.warning(f"Failed to parse resource string: {resource}")

        except Exception as e:
            logger.warning(f"Failed to list recommendations for {zone}/{rec_id}: {e}")

from billing import BillingService

# ... (global)
_billing_service = None
def get_billing_service():
    global _billing_service
    if not _billing_service:
        _billing_service = BillingService()
    return _billing_service

async def get_instance_report(project_id: str = None, instance_name: str = "all"):
    """
    Generates a detailed Markdown Table report for GCE instances.
    Includes True Cost (BigQuery) and sizing recommendations.
    """
    managed_projects = []
    if project_id == "all":
        managed_projects = await asyncio.to_thread(get_managed_projects)
    else:
        try:
            pid = await asyncio.to_thread(resolve_project_id, project_id)
            managed_projects = [pid]
        except ValueError as e:
            return str(e)

    final_report = []
    
    # Semaphore for instance details
    sem = asyncio.Semaphore(10)
    billing_svc = get_billing_service()

    async def fetch_instances_details_excluding_rec(inst, pid, zone_short):
        mt_url = inst.machine_type
        short_mt = mt_url.split("/")[-1]
        
        async with sem:
             # Extract extra resources (Disks)
             extra_resources = []
             if inst.disks:
                 for d in inst.disks:
                     if d.source:
                         disk_name = d.source.split("/")[-1]
                         if disk_name != inst.name:
                             extra_resources.append(disk_name)

             # Fetch Machine Type, Estimate Cost, AND True Cost
             return await asyncio.gather(
                 asyncio.to_thread(get_machine_type_details_sync, short_mt, zone_short, pid),
                 estimate_monthly_cost(inst, pid, zone_short),
                 billing_svc.get_instance_cost(pid, inst.name, extra_resource_names=extra_resources)
             )

    for pid in managed_projects:
        try:
            client = get_instances_client()
            
            # Fetch all instances first
            agg_list = await asyncio.to_thread(client.aggregated_list, request=compute_v1.AggregatedListInstancesRequest(project=pid))
            
            target_list = []
            unique_zones = set()

            for zone_path, response in agg_list:
                if response.instances:
                    zone_short = zone_path.split("/")[-1]
                    unique_zones.add(zone_short)
                    for instance in response.instances:
                         if instance_name == "all" or instance.name == instance_name:
                            target_list.append(instance)

            if not target_list:
                if instance_name != "all":
                     final_report.append(f"Project `{pid}`: Instance '{instance_name}' not found.")
                else:
                     final_report.append(f"### 📊 Project `{pid}`\nNo instances found.")
                continue

            # 1. Fetch Project Description
            project_desc = ""
            try:
                doc = _get_firestore_client().collection("managed_projects").document(pid).get()
                if doc.exists:
                     project_desc = doc.to_dict().get("description", "")
            except Exception:
                pass 

            # 2. Batch Fetch Recommendations
            rec_map = {} 
            rec_client = get_recommender_client()
            rec_tasks = [fetch_zone_recommendations(pid, z, rec_client, rec_map) for z in unique_zones]
            await asyncio.gather(*rec_tasks)

            # 3. Fetch Instance Details (Tech + Billing)
            tasks = []
            for inst in target_list:
                z_short = inst.zone.split("/")[-1]
                tasks.append(fetch_instances_details_excluding_rec(inst, pid, z_short))
            
            results = await asyncio.gather(*tasks)

            # 4. Process and Collect Data
            processed_instances = []
            
            total_estimated_cost = 0.0
            total_true_cost = 0.0
            total_savings = 0.0
            total_vcpu = 0
            total_ram = 0.0
            project_total_disk_gb = 0

            for i, inst in enumerate(target_list):
                (vcpu_str, ram_gb_str), estimated_cost, true_cost_data = results[i]
                
                # Recommendations
                zone_short = inst.zone.split("/")[-1]
                match_key = f"{zone_short}/{inst.name}"
                my_recs = rec_map.get(match_key, [])
                
                inst_rec_text = "None"
                inst_savings = 0.0
                if my_recs:
                    descriptions = [r["description"] for r in my_recs]
                    inst_rec_text = " | ".join(descriptions)
                    inst_savings = sum(r["savings"] for r in my_recs)

                # Costs
                inst_est_cost = 0.0
                try: inst_est_cost = float(estimated_cost)
                except: pass
                
                inst_true_cost = 0.0
                has_true_cost = False
                if true_cost_data:
                    inst_true_cost = true_cost_data.get("total_net_cost", 0.0)
                    has_true_cost = True

                # Hardware
                inst_vcpu = 0
                try: inst_vcpu = int(vcpu_str)
                except: pass
                
                inst_ram = 0.0
                try: inst_ram = float(ram_gb_str)
                except: pass
                
                # Update Totals
                total_estimated_cost += inst_est_cost
                total_true_cost += inst_true_cost
                total_savings += inst_savings
                total_vcpu += inst_vcpu
                total_ram += inst_ram

                # Metadata
                name = inst.name
                status = "🟢 RUNNING" if inst.status == "RUNNING" else "🔴 TERMINATED" if inst.status == "TERMINATED" else inst.status
                
                created_str = "?"
                if inst.creation_timestamp:
                    try: created_str = inst.creation_timestamp.split("T")[0]
                    except: created_str = inst.creation_timestamp[:10]

                mt_short = inst.machine_type.split("/")[-1]
                
                # Network
                priv_ip = "-"
                pub_ip = "-"
                if inst.network_interfaces:
                    nic0 = inst.network_interfaces[0]
                    priv_ip = nic0.network_i_p
                    if nic0.access_configs:
                        pub_ip = nic0.access_configs[0].nat_i_p
                
                # Disks
                total_disk_gb = 0
                disk_details = []
                os_name = "?"
                for d in inst.disks:
                    sz = d.disk_size_gb
                    total_disk_gb += sz
                    dtype = "Std"
                    if "pd-ssd" in d.type: dtype = "SSD"
                    elif "pd-balanced" in d.type: dtype = "Bal"
                    disk_details.append(f"{sz}G {dtype}")
                    
                    if d.boot and d.licenses:
                        for lic in d.licenses:
                            lower_lic = lic.lower()
                            if "debian" in lower_lic:
                                parts = lic.split("/")[-1].split("-")
                                ver = next((p for p in parts if p.isdigit()), "")
                                os_name = f"Debian {ver}"
                                break
                            elif "ubuntu" in lower_lic:
                                parts = lic.split("/")[-1].split("-")
                                ver = next((p for p in parts if p.isdigit() and len(p)>=2), "")
                                if len(ver)==4: ver = f"{ver[:2]}.{ver[2:]}"
                                os_name = f"Ubuntu {ver}"
                                break
                            elif "windows" in lower_lic:
                                parts = lic.split("/")[-1].split("-")
                                ver = next((p for p in parts if p.isdigit() and len(p)==4), "")
                                os_name = f"Windows {ver}"
                                break
                            elif "rhel" in lower_lic:
                                parts = lic.split("/")[-1].split("-")
                                ver = next((p for p in parts if p.isdigit()), "")
                                os_name = f"RHEL {ver}"
                                break
                            elif "centos" in lower_lic:
                                parts = lic.split("/")[-1].split("-")
                                ver = next((p for p in parts if p.isdigit()), "")
                                os_name = f"CentOS {ver}"
                                break

                storage_str = f"{total_disk_gb}G"
                if disk_details:
                    storage_str += f" ({', '.join(disk_details)})"
                
                project_total_disk_gb += total_disk_gb

                processed_instances.append({
                    "name": name,
                    "status": status,
                    "zone": zone_short,
                    "created": created_str,
                    "machine_type": mt_short,
                    "vcpu": inst_vcpu,
                    "ram": inst_ram,
                    "storage": storage_str,
                    "os": os_name,
                    "int_ip": priv_ip,
                    "ext_ip": pub_ip,
                    "est_cost": inst_est_cost,
                    "true_cost": inst_true_cost,
                    "has_true_cost": has_true_cost,
                    "savings": inst_savings,
                    "rec_text": inst_rec_text
                })

            # 5. Sort by True Cost (if avail) else Est Cost
            processed_instances.sort(key=lambda x: x["true_cost"] if x["has_true_cost"] else x["est_cost"], reverse=True)

            # 6. Build Report
            project_report = []
            
            # Header
            project_report.append(f"**📊 Project: `{pid}`**")
            if project_desc:
                project_report.append(f"_{project_desc}_")
            
            project_report.append("**📈 Project Summary**")
            project_report.append(f"• **Instances:** {len(target_list)}")
            project_report.append(f"• **Available vCPU:** {total_vcpu}")
            project_report.append(f"• **Total RAM:** {total_ram:.1f} GB")
            project_report.append(f"• **Total Disk:** {project_total_disk_gb} GB")
            
            # Show both estimated and true cost in summary
            # 'Monthly Cost' usually implies run rate. 'True Cost (30d)' is historical.
            # We'll show True Cost as primary if non-zero.
            if total_true_cost > 0:
                project_report.append(f"• **True Cost (30d):** `${total_true_cost:.2f}`")
            else:
                 project_report.append(f"• **Est. Monthly Cost:** `${total_estimated_cost:.2f}`")
            
            if total_savings > 0:
                project_report.append(f"• **Potential Savings:** `${total_savings:.2f}`")
            project_report.append("---")

            # Cards
            for idx, inst in enumerate(processed_instances, 1):
                rec_str = ""
                if inst["rec_text"] != "None":
                     rec_str = f" | 💡 Tip: {inst['rec_text']}"

                line1 = f"**{idx}. 🖥️ `{inst['name']}`**"
                line2 = f"**Status:** {inst['status']} | **Zone:** {inst['zone']} | **Created:** {inst['created']}"
                line3 = f"**Type:** {inst['machine_type']} | **vCPU:** {inst['vcpu']} | **RAM:** {inst['ram']} GB | **Disk:** {inst['storage']}"
                
                ext_ip_display = inst['ext_ip'] if inst['ext_ip'] != "-" else "None"
                line4 = f"**OS:** {inst['os']} | **Int IP:** {inst['int_ip']} | **Ext IP:** {ext_ip_display}"
                
                savings_display = f"${inst['savings']:.2f}"
                if inst['savings'] > 0:
                     savings_display = f"**${inst['savings']:.2f}**"
                
                # Cost Line: Prioritize True Cost
                cost_str = f"${inst['est_cost']:.2f}/mo (Est)"
                if inst['has_true_cost']:
                     cost_str = f"**${inst['true_cost']:.2f}** (30d)"
                
                line5 = f"**💰 Cost:** {cost_str} | **💸 Savings:** {savings_display}{rec_str}"

                project_report.append(f"{line1}\n{line2}\n{line3}\n{line4}\n{line5}")
                project_report.append("---")

            final_report.append("\n".join(project_report))

        except Exception as e:
            final_report.append(f"Error generating report for {pid}: {e}")
            logger.error(f"Generate Report Error: {e}", exc_info=True)

    return "\n\n".join(final_report)

async def get_instance_sku_report(project_id: str = None, instance_name: str = None):
    """
    Generates a detailed detailed SKU breakdown report for a specific instance.
    Includes Net Cost, Gross Cost, and Usage details from Billing.
    If project_id is not specified, searches across all managed projects.
    """
    if not instance_name:
        return "⚠️ Error: Please specify an instance name for the detailed SKU report."

    # Determine which projects to search
    target_projects = []
    if project_id:
        try:
            resolved = await asyncio.to_thread(resolve_project_id, project_id)
            target_projects.append(resolved)
        except ValueError as e:
            return str(e)
    else:
        # Search all managed projects
        target_projects = await asyncio.to_thread(get_managed_projects)

    billing_svc = get_billing_service()
    
    sku_details = None
    found_project = None

    # Iterate/Search for Instance First using API to get attached SKUs (Disks)
    # We prioritize API finding to capture dynamic resource names like external disks
    instance_obj = None
    found_zone = None
    extra_resources = []
    
    # Try api find first
    for pid in target_projects:
        try:
            zone = await find_instance_zone(pid, instance_name)
            if zone:
                found_project = pid
                found_zone = zone
                # Fetch full instance object
                client = get_instances_client()
                instance_obj = await asyncio.to_thread(client.get, project=pid, zone=zone, instance=instance_name)
                
                # Extract extra resources (Disks)
                if instance_obj and instance_obj.disks:
                    for d in instance_obj.disks:
                        # d.source is like projects/{proj}/zones/{zone}/disks/{disk_name}
                        if d.source:
                             disk_name = d.source.split("/")[-1]
                             # Only add if it's NOT the instance name (which is already covered)
                             if disk_name != instance_name:
                                 extra_resources.append(disk_name)
                break
        except Exception as e:
            # Not found in this project or other error
            continue

    # Query Billing
    # If we found it via API, use that project. If not, fallback to loop search in billing.
    if found_project:
        # Single targeted query
        try:
            sku_details = await billing_svc.get_instance_sku_details(found_project, instance_name, extra_resource_names=extra_resources)
        except Exception as e:
            logger.error(f"Billing query failed for {instance_name} in {found_project}: {e}")
    else:
        # Fallback: We didn't find it in API (maybe deleted?), so we blindly search billing
        # Note: We won't have extra_resources here since we couldn't read the instance config
        for pid in target_projects:
            try:
                details = await billing_svc.get_instance_sku_details(pid, instance_name)
                if details:
                    sku_details = details
                    found_project = pid
                    break
            except Exception as e:
                pass
    
    if not sku_details:
        projects_checked = ", ".join(target_projects)
        return f"### 📊 Instance SKU Report: `{instance_name}`\nNo billing data found for the last 30 days in projects: `{projects_checked}`.\nVerify the instance name is correct (CASE SENSITIVE for database, though we use LIKE)."
    
    # Calculate Totals First
    total_net = sum(row.get("net_cost", 0.0) for row in sku_details)
    total_gross = sum(row.get("gross_cost", 0.0) for row in sku_details)

    # 1. Fetch Rich Instance Details (Status, Hardware, etc.)
    header_details = ""
    try:
        # If we already have the object, use it
        if instance_obj and found_zone and found_project:
             header_details = await _get_instance_details_string(found_project, found_zone, instance_obj, total_net, sku_details)
        # If we found it via billing (fallback) but not API, we might try to find it now?
        # But if we failed before, we likely fail again.
    except Exception as e:
        logger.warning(f"Failed to fetch detailed instance info for header: {e}")

    # Format as Markdown Table
    lines = []
    
    # Use rich header if available, else fallback
    if header_details:
        lines.append(header_details)
    else:
        lines.append(f"### 📊 Instance SKU Report: `{instance_name}`")
        lines.append(f"**Project:** `{found_project}` | **Period:** Last 30 Days")
        lines.append(f"**💰 Total Net Cost:** `${total_net:,.2f}` (Gross: ${total_gross:,.2f})")
    
    lines.append("")
    lines.append("| # | SKU ID | SKU Description | Usage | Gross Cost | Net Cost |")
    lines.append("| :--- | :--- | :--- | :--- | :---: | :---: |")
    
    for idx, row in enumerate(sku_details, 1):
        sku_id = row.get("sku_id", "N/A")
        desc = row.get("sku_description", "Unknown SKU")
        unit = row.get("usage_unit", "")
        amount = row.get("total_usage_amount", 0.0)
        gross = row.get("gross_cost", 0.0)
        net = row.get("net_cost", 0.0)
        
        # Filter handled by SQL HAVING clause now.
        
        # Format usage
        usage_str = f"{amount:,.2f} {unit}"
        
        lines.append(f"| {idx} | {sku_id} | {desc} | {usage_str} | ${gross:,.2f} | **${net:,.2f}** |")
        
    lines.append("")
    
    return "\n".join(lines)

async def _get_instance_details_string(project_id, zone, instance_obj, true_cost=0.0, sku_details=None):
    """
    Helper to fetch and format instance details for the SKU report header.
    Accepts an existing instance_obj to avoid re-fetching.
    """
    try:
        inst = instance_obj
        
        # Machine Type
        mt_short = inst.machine_type.split("/")[-1]
        vcpu, ram = get_machine_type_details_sync(mt_short, zone, project_id)
        
        # Status
        status_icon = "🟢" if inst.status == "RUNNING" else "🔴" if inst.status == "TERMINATED" else "❓"
        status_str = f"{status_icon} {inst.status}"
        
        # Creation
        created = "?"
        if inst.creation_timestamp:
            created = inst.creation_timestamp.split("T")[0]
            
        # Uptime Calculation (if SKU details provided)
        uptime_str = ""
        # Convert vcpu to simple number
        try:
            vcpu_num = int(float(vcpu))
        except:
            vcpu_num = 0
            
        if sku_details and vcpu_num > 0:
            total_usage_seconds = 0.0
            for row in sku_details:
                # Look for "Instance Core" usage
                desc = row.get("sku_description", "").lower()
                usage_unit = row.get("usage_unit", "").lower()
                
                # "instance core" usually covers the vCPU usage
                # We need to ensure we are summing seconds.
                if "instance core" in desc and "second" in usage_unit:
                    try:
                        # Fixed key: total_usage_amount (was usage)
                        usage_val = float(row.get("total_usage_amount", 0))
                        total_usage_seconds += usage_val
                    except: pass
            
            if total_usage_seconds > 0:
                # Formula: Usage (seconds) / (vCPU * 3600)
                uptime_hours = total_usage_seconds / (vcpu_num * 3600)
                uptime_str = f" | ⏰ **Uptime**: {uptime_hours:.2f}h"

        # OS & Disk
        total_disk_gb = 0
        disk_details = []
        os_name = "?"
        
        # Initialize Disks Client
        disks_client = get_disks_client()

        for d in inst.disks:
            sz = d.disk_size_gb
            total_disk_gb += sz
            
            # Fetch Disk Resource to get accurate type (Std/SSD/Bal)
            dtype = "Std" # Default
            try:
                if d.source:
                    # d.source format: projects/{project}/zones/{zone}/disks/{disk_name}
                    disk_name = d.source.split("/")[-1]
                    # We can use the sync client here as it's inside an async function loop but it might block slightly
                    # For a few disks it's negligible.
                    disk_obj = disks_client.get(project=project_id, zone=zone, disk=disk_name)
                    
                    if "pd-ssd" in disk_obj.type: dtype = "SSD"
                    elif "pd-balanced" in disk_obj.type: dtype = "Bal"
                    elif "pd-standard" in disk_obj.type: dtype = "Std"
                    else: dtype = " Unk" 
            except Exception as e:
                logger.warning(f"Failed to fetch disk details for {d.source}: {e}")
                
            disk_details.append(f"{sz}G {dtype}")

            # OS Detection from Boot Disk
            if d.boot and d.licenses:
                for lic in d.licenses:
                    lower_lic = lic.lower()
                    if "windows" in lower_lic:
                        parts = lic.split("/")[-1].split("-")
                        ver = next((p for p in parts if p.isdigit() and len(p)==4), "Server")
                        os_name = f"Windows {ver}"
                    elif "debian" in lower_lic: os_name = "Debian"
                    elif "ubuntu" in lower_lic: os_name = "Ubuntu"
                    elif "rhel" in lower_lic: os_name = "RHEL"
                    elif "centos" in lower_lic: os_name = "CentOS"
                    elif "sles" in lower_lic: os_name = "SLES"
                    elif "rocky" in lower_lic: os_name = "Rocky"

        storage_str = f"{total_disk_gb}G"
        if disk_details:
             storage_str += f" ({', '.join(disk_details)})"

        # IPs
        int_ip = inst.network_interfaces[0].network_i_p if inst.network_interfaces else "?"
        ext_ip = "None"
        if inst.network_interfaces and inst.network_interfaces[0].access_configs:
            ext_ip = inst.network_interfaces[0].access_configs[0].nat_i_p

        # Cost & Savings
        cost_str = f"💰 Cost: ${true_cost:,.2f} (30d)"
        
        # Calculate Savings
        savings = 0.0
        try:
            # 1. Initialize Recommender Client
            rec_client = get_recommender_client()
            
            # 2. Fetch recommendations for this zone (reuse existing helper)
            rec_map = {}
            # We await the helper which populates rec_map by reference
            await fetch_zone_recommendations(project_id, zone, rec_client, rec_map)
            
            # 3. Lookup this instance in the map
            # Key format used in helper: "zone_short/instance_name"
            # We need to construct the key carefully
            zone_short = zone.split("/")[-1]
            match_key = f"{zone_short}/{inst.name}"
            
            my_recs = rec_map.get(match_key, [])
            if my_recs:
                savings = sum(r["savings"] for r in my_recs)
                logger.info(f"Adding savings for {inst.name}: ${savings}")
                
        except Exception as e:
            logger.warning(f"Failed to fetch savings for {inst.name}: {e}")

        savings_str = f"💸 Savings: ${savings:,.2f}"

        # Format Lines
        line1 = f"### 📊 Instance SKU Report: `{inst.name}`"
        line2 = f"**Project**: {project_id} **Status**: {status_str} | **Zone**: {zone} | **Created**: {created}{uptime_str}"
        line3 = f"**Type**: {mt_short} | **vCPU**: {vcpu} | **RAM**: {ram} | **Disk**: {storage_str}"
        line4 = f"**OS**: {os_name} | **Int IP**: {int_ip} | **Ext IP**: {ext_ip} | {cost_str} | {savings_str}"

        return f"{line1}\n{line2}\n{line3}\n{line4}\n"

    except Exception as e:
        logger.error(f"Error formatting instance details: {e}")
        return f"### Instance SKU Report: {instance_obj.name} (Error fetching details)"

async def create_custom_instance(name, project_id=None, machine_type="n2-custom-2-4096", image_family="rhel-9", boot_disk_size="10", extra_disk_size="0"):
    """
    Creates a new custom instance.
    REQUIRES 'admin' ROLE.
    """
    # RBAC Guard
    try:
        import user_context
        user_context.require_admin()
    except PermissionError as e:
        return f"⛔ {e}"

    final_name = name.lower().replace("_", "-")
    
    try:
        resolved_project = await asyncio.to_thread(resolve_project_id, project_id)
    except ValueError as e:
        return str(e)

    try:
        instances_client = get_instances_client()
        image_project = "rhel-cloud"
        if "debian" in image_family: image_project = "debian-cloud"
        elif "ubuntu" in image_family: image_project = "ubuntu-os-cloud"
        elif "centos" in image_family: image_project = "centos-cloud"
        
        source_image = f"projects/{image_project}/global/images/family/{image_family}"

        disks = []
        # Boot Disk
        boot_disk = compute_v1.AttachedDisk()
        boot_disk.initialize_params = compute_v1.AttachedDiskInitializeParams()
        boot_disk.initialize_params.disk_size_gb = int(boot_disk_size)
        boot_disk.initialize_params.source_image = source_image
        boot_disk.initialize_params.disk_type = f"projects/{resolved_project}/zones/{ZONE}/diskTypes/pd-balanced"
        boot_disk.auto_delete = True
        boot_disk.boot = True
        boot_disk.type_ = compute_v1.AttachedDisk.Type.PERSISTENT.name
        disks.append(boot_disk)

        if extra_disk_size and int(extra_disk_size) > 0:
            data_disk = compute_v1.AttachedDisk()
            data_disk.initialize_params = compute_v1.AttachedDiskInitializeParams()
            data_disk.initialize_params.disk_size_gb = int(extra_disk_size)
            data_disk.initialize_params.disk_type = f"projects/{resolved_project}/zones/{ZONE}/diskTypes/pd-balanced"
            data_disk.initialize_params.disk_name = f"{final_name}-data"
            data_disk.auto_delete = True
            data_disk.boot = False
            data_disk.type_ = compute_v1.AttachedDisk.Type.PERSISTENT.name
            disks.append(data_disk)

        network_interface = compute_v1.NetworkInterface()
        network_interface.name = "global/networks/default"
        
        service_account = compute_v1.ServiceAccount()
        # Use default compute SA logic or specific one. 
        # Using default compute SA for the target project is usually 'PROJECT_NUMBER-compute@...'
        # But we don't know the project number easily here without looking it up.
        # It's better to NOT specify email to let GCE pick the default, 
        # OR fetch it. If we omit email, it uses default.
        service_account.scopes = ["https://www.googleapis.com/auth/cloud-platform"]

        instance = compute_v1.Instance()
        instance.name = final_name
        instance.machine_type = f"zones/{ZONE}/machineTypes/{machine_type}"
        instance.disks = disks
        instance.network_interfaces = [network_interface]
        instance.service_accounts = [service_account]
        instance.labels = {"created-by": "gce-manager-agent"}
        instance.scheduling = compute_v1.Scheduling()
        instance.scheduling.on_host_maintenance = "MIGRATE"
        instance.scheduling.provisioning_model = "STANDARD"

        request = compute_v1.InsertInstanceRequest(
            project=resolved_project,
            zone=ZONE,
            instance_resource=instance
        )

        operation = await asyncio.to_thread(instances_client.insert, request)
        return f"Instance '{final_name}' creation triggered in project '{resolved_project}'. \nOperation: {operation.name}"

    except Exception as e:
        logger.error(f"Error creating instance: {e}")
        return f"Error creating instance in {resolved_project}: {e}"

