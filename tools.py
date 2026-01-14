import asyncio
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
        opts = ClientOptions(api_endpoint=f"recommender.{REGION}.rep.googleapis.com")
        _clients["recommender"] = recommender_v1.RecommenderClient(client_options=opts)
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

async def start_instance(instance_name: str, project_id: str = None, zone: str = None):
    """Starts a specific GCE instance. Auto-detects zone if not provided."""
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

async def stop_instance(instance_name: str, project_id: str = None, zone: str = None):
    """Stops a specific GCE instance. Auto-detects zone if not provided."""
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
    
    # Check for custom type First
    import re
    match = re.search(r".*-custom-(\d+)-(\d+)", short_type)
    if match:
        vcpu = match.group(1)
        memory_mb = int(match.group(2))
        return vcpu, f"{memory_mb / 1024:.1f}"

    try:
        client = compute_v1.MachineTypesClient()
        request = compute_v1.GetMachineTypeRequest(
            project=project_id,
            zone=zone,
            machine_type=short_type
        )
        mt = client.get(request=request)
        return str(mt.guest_cpus), f"{mt.memory_mb / 1024:.1f}"
    except Exception:
        return "?", "?"

async def get_instance_recommendations(project, zone, instance_name):
    rec_text = "None"
    savings = "0.00"
    try:
        client = get_recommender_client()
        parent = f"projects/{project}/locations/{zone}/recommenders/google.compute.instance.MachineTypeRecommender"
        request = recommender_v1.ListRecommendationsRequest(parent=parent)
        
        # This call can be slow if Rec API is cold or busy
        page_result = await asyncio.to_thread(client.list_recommendations, request)
        
        for r in page_result:
            if f"/instances/{instance_name}" in r.content.operation_groups[0].operations[0].resource:
                rec_text = r.description
                cost = r.primary_impact.cost_projection.cost
                if cost.currency_code == "USD":
                     units = cost.units
                     nanos = cost.nanos
                     total = abs(units + (nanos / 1e9))
                     savings = f"{total:.2f}"
                break
    except Exception as e:
        # Log as debug to avoid user alarm unless debugging is needed
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

async def get_instance_report(project_id: str = None, instance_name: str = "all"):
    """
    Generates a detailed Markdown Table report for GCE instances.
    Includes cost estimation and sizing recommendations.
    Uses concurrency with semaphore to avoid timeouts.
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
    
    # Semaphore to limit concurrent heavy tasks (Rec API, etc)
    sem = asyncio.Semaphore(10)

    async def fetch_details_safe(inst, pid):
        mt_url = inst.machine_type
        short_mt = mt_url.split("/")[-1]
        inst_zone_url = inst.zone
        inst_zone = inst_zone_url.split("/")[-1]
        
        async with sem:
            return await asyncio.gather(
                asyncio.to_thread(get_machine_type_details_sync, short_mt, inst_zone, pid),
                get_instance_recommendations(pid, inst_zone, inst.name),
                estimate_monthly_cost(inst, pid, inst_zone)
            )

    for pid in managed_projects:
        try:
            client = get_instances_client()
            
            # Fetch all instances first
            agg_list = await asyncio.to_thread(client.aggregated_list, request=compute_v1.AggregatedListInstancesRequest(project=pid))
            
            target_list = []
            for zone, response in agg_list:
                if response.instances:
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
                # Assuming 'db' is available globally or need to re-init if not.
                # list_managed_projects uses 'db', so it should be available.
                doc = db.collection("managed_projects").document(pid).get()
                if doc.exists:
                     project_desc = doc.to_dict().get("description", "")
            except Exception:
                pass # Fail silently on desc fetch

            # 2. Prepare tasks for details
            tasks = [fetch_details_safe(inst, pid) for inst in target_list]
            results = await asyncio.gather(*tasks)

            # 3. Process and Collect Data
            processed_instances = []
            
            # Aggregates
            total_cost = 0.0
            total_savings = 0.0
            total_vcpu = 0
            total_ram = 0.0

            for i, inst in enumerate(target_list):
                # Unpack details
                (vcpu_str, ram_gb_str), (rec_text, savings), estimated_cost = results[i]
                
                # Parse specific attributes
                inst_cost = 0.0
                inst_sav = 0.0
                inst_vcpu = 0
                inst_ram = 0.0
                
                try: inst_cost = float(estimated_cost)
                except: pass
                
                try: inst_sav = float(savings)
                except: pass

                try: inst_vcpu = int(vcpu_str)
                except: pass
                
                try: inst_ram = float(ram_gb_str)
                except: pass
                
                # Update Totals
                total_cost += inst_cost
                total_savings += inst_sav
                total_vcpu += inst_vcpu
                total_ram += inst_ram

                # Parse other fields
                name = inst.name
                status = "🟢 RUNNING" if inst.status == "RUNNING" else "🔴 TERMINATED" if inst.status == "TERMINATED" else inst.status
                
                created_str = "?"
                if inst.creation_timestamp:
                    try: created_str = inst.creation_timestamp.split("T")[0]
                    except: created_str = inst.creation_timestamp[:10]

                mt_short = inst.machine_type.split("/")[-1]
                zone_short = inst.zone.split("/")[-1]
                
                # IPs
                priv_ip = "-"
                pub_ip = "-"
                if inst.network_interfaces:
                    nic0 = inst.network_interfaces[0]
                    priv_ip = nic0.network_i_p
                    if nic0.access_configs:
                        pub_ip = nic0.access_configs[0].nat_i_p
                
                # Disk & OS
                total_disk_gb = 0
                disk_details = []
                os_name = "?"
                
                disks = inst.disks
                for d in disks:
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
                    "cost": inst_cost,
                    "savings": inst_sav,
                    "rec_text": rec_text
                })

            # 4. Sort by Cost Descending
            processed_instances.sort(key=lambda x: x["cost"], reverse=True)

            # 5. Build Report
            project_report = []
            
            # Header (Small uniform headers)
            project_report.append(f"**📊 Project: `{pid}`**")
            if project_desc:
                project_report.append(f"_{project_desc}_")
            
            project_report.append("**📈 Project Summary**")
            project_report.append(f"• **Instances:** {len(target_list)}")
            project_report.append(f"• **Total vCPU:** {total_vcpu}")
            project_report.append(f"• **Total RAM:** {total_ram:.1f} GB")
            project_report.append(f"• **Monthly Cost:** `${total_cost:.2f}`")
            project_report.append(f"• **Potential Savings:** `${total_savings:.2f}`")
            project_report.append("---")

            # Cards - Uniform small font
            # Enumeration start at 1
            for idx, inst in enumerate(processed_instances, 1):
                rec_icon = "⚠️" if inst["rec_text"] != "None" else "✅"
                rec_str = ""
                if inst["rec_text"] != "None":
                     rec_str = f" | 💡 Tip: {inst['rec_text']}"

                # Line 1: Enumeration + Name
                line1 = f"**{idx}. 🖥️ `{inst['name']}`**"
                
                # Line 2: Status | Zone | Created
                line2 = f"**Status:** {inst['status']} | **Zone:** {inst['zone']} | **Created:** {inst['created']}"
                
                # Line 3: Machine Type | vCPU | RAM | Total Disk Size
                line3 = f"**Machine Type:** {inst['machine_type']} | **vCPU:** {inst['vcpu']} | **RAM:** {inst['ram']} GB | **Total Disk Size:** {inst['storage']}"
                
                # Line 4: OS | Int IP | Ext IP
                ext_ip_display = inst['ext_ip'] if inst['ext_ip'] != "-" else "None"
                line4 = f"**OS:** {inst['os']} | **Int IP:** {inst['int_ip']} | **Ext IP:** {ext_ip_display}"
                
                # Line 5: Cost | Savings
                line5 = f"**💰 Cost:** ${inst['cost']:.2f}/mo | **💸 Savings:** ${inst['savings']:.2f}{rec_str}"

                project_report.append(f"{line1}\n{line2}\n{line3}\n{line4}\n{line5}")
                project_report.append("---")

            final_report.append("\n".join(project_report))

        except Exception as e:
            final_report.append(f"Error generating report for {pid}: {e}")

    return "\n\n".join(final_report)

async def create_custom_instance(name, project_id=None, machine_type="n2-custom-2-4096", image_family="rhel-9", boot_disk_size="10", extra_disk_size="0"):
    """Creates a new custom instance."""
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

