import asyncio
import subprocess
import json
import logging
from google.cloud import compute_v1
from google.cloud import recommender_v1
from google.api_core.client_options import ClientOptions

# Configuration
PROJECT_ID = "autonomous-agent-479317"
ZONE = "us-central1-a"
REGION = "us-central1"
# SERVICE_ACCOUNT is used for impersonation in CLI, might not be needed for native client on Cloud Run
# if the Cloud Run SA has permissions.
SERVICE_ACCOUNT = "mcp-manager@autonomous-agent-479317.iam.gserviceaccount.com"

# Initialize Logging
logger = logging.getLogger(__name__)

# --- Clients ---
# We initialize these lazily or globally. Global is fine for Cloud Run (warm instances).
# Note: On Cloud Run, default credentials are used.
def get_instances_client():
    return compute_v1.InstancesClient()

def get_zone_operations_client():
    return compute_v1.ZoneOperationsClient()

def get_recommender_client():
    # Recommender requires regional endpoint usually
    opts = ClientOptions(api_endpoint=f"recommender.{REGION}.rep.googleapis.com")
    # Actually global endpoint is fine for some, but let's stick to default unless issues.
    return recommender_v1.RecommenderClient()

# --- Tools exposed to the Agent ---

async def list_instances():
    """Lists all GCE instances in the configured zone."""
    try:
        client = get_instances_client()
        # List instances request
        request = compute_v1.ListInstancesRequest(
            project=PROJECT_ID,
            zone=ZONE
        )
        
        # This is a sync call, wrap in asyncio.to_thread to avoid blocking event loop
        page_result = await asyncio.to_thread(client.list, request)
        
        summary = []
        for inst in page_result:
            # inst is an Instance object
            ip = "N/A"
            if inst.network_interfaces:
                ip = inst.network_interfaces[0].network_i_p
            
            summary.append(f"Name: {inst.name}, Status: {inst.status}, IP: {ip}")
            
        return "\n".join(summary) if summary else "No instances found."
    except Exception as e:
        logger.error(f"Error listing instances: {e}")
        return f"Error listing instances: {e}"

async def start_instance(instance_name):
    """Starts a specific GCE instance."""
    if instance_name == "all":
        # We could implement 'all' easily now, but sticking to single for safety first
        # Or maybe implementing 'all' since it's requested?
        # Let's support 'all' for "Wow" factor if easy.
        # But wait, starting ALL might be dangerous. Let's keep it safe.
        return "Please specify an instance name. Bulk actions are restricted for safety."

    try:
        client = get_instances_client()
        op_client = get_zone_operations_client()

        request = compute_v1.StartInstanceRequest(
            project=PROJECT_ID,
            zone=ZONE,
            instance=instance_name
        )

        operation = await asyncio.to_thread(client.start, request)
        
        # Wait for operation (optional, but good for feedback)
        # This might take time, so maybe we just return "Starting..."
        # But user likes "Completed" feedback.
        # Let's wait up to 5 seconds, else return "In Progress".
        
        # Actually, let's wait for result using the operation client
        # await asyncio.to_thread(operation.result, timeout=60) # This blocks thread
        
        return f"Instance '{instance_name}' start triggered successfully. Status: {operation.status}"
    except Exception as e:
        logger.error(f"Error starting instance {instance_name}: {e}")
        return f"Error starting instance '{instance_name}': {e}"

async def stop_instance(instance_name):
    """Stops a specific GCE instance."""
    if instance_name == "all":
         return "Please specify an instance name. Bulk actions are restricted for safety."

    try:
        client = get_instances_client()
        
        request = compute_v1.StopInstanceRequest(
            project=PROJECT_ID,
            zone=ZONE,
            instance=instance_name
        )

        operation = await asyncio.to_thread(client.stop, request)
        return f"Instance '{instance_name}' stop triggered successfully. Status: {operation.status}"
    except Exception as e:
        logger.error(f"Error stopping instance {instance_name}: {e}")
        return f"Error stopping instance '{instance_name}': {e}"

def get_machine_type_details_sync(machine_type_url, zone, project_id):
    # machine_type_url e.g. https://www.googleapis.com/compute/v1/projects/.../zones/.../machineTypes/e2-micro
    # or just 'e2-micro'
    # We need to parse or describe.
    
    short_type = machine_type_url.split("/")[-1]
    
    # Check for custom type First
    import re
    match = re.search(r".*-custom-(\d+)-(\d+)", short_type)
    if match:
        vcpu = match.group(1)
        memory_mb = int(match.group(2))
        return vcpu, f"{memory_mb / 1024:.1f}"

    try:
        # Use MachinesClient? Or just hardcode common ones? 
        # API call is safer.
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
    """
    Fetches sizing recommendations.
    """
    rec_text = "None"
    savings = "0.00"
    
    try:
        client = get_recommender_client()
        parent = f"projects/{project}/locations/{zone}/recommenders/google.compute.instance.MachineTypeRecommender"
        
        # List recommendations
        request = recommender_v1.ListRecommendationsRequest(parent=parent)
        
        # Wrap sync call
        page_result = await asyncio.to_thread(client.list_recommendations, request)
        
        for r in page_result:
            # Target resource: //compute.googleapis.com/projects/.../zones/.../instances/NAME
            if f"/instances/{instance_name}" in r.content.operation_groups[0].operations[0].resource:
                rec_text = r.description
                
                # Calculate savings
                cost = r.primary_impact.cost_projection.cost
                if cost.currency_code == "USD":
                     units = cost.units
                     nanos = cost.nanos
                     total = abs(units + (nanos / 1e9))
                     savings = f"{total:.2f}"
                break
    except Exception as e:
        logger.warning(f"Error fetching recommendations: {e}")
        pass
        
    return rec_text, savings

async def get_instance_report(instance_name="all"):
    """
    Generates a detailed report using native Python client.
    """
    try:
        client = get_instances_client()
        request = compute_v1.ListInstancesRequest(project=PROJECT_ID, zone=ZONE)
        
        # Fetch all efficiently
        all_instances = await asyncio.to_thread(client.list, request)
        
        # Filter if single
        target_list = []
        for inst in all_instances:
            if instance_name == "all" or inst.name == instance_name:
                target_list.append(inst)
        
        if not target_list and instance_name != "all":
            return f"Instance '{instance_name}' not found."

        report_lines = []
        report_lines.append("=" * 50)
        report_lines.append(f" PROJECT: {PROJECT_ID}")
        report_lines.append(f" TOTAL INSTANCES: {len(target_list)}")
        report_lines.append("=" * 50)
        report_lines.append("")

        for inst in target_list:
            # Basic Info
            name = inst.name
            status = inst.status
            creation_ts = inst.creation_timestamp
            
            # Machine Type
            mt_url = inst.machine_type
            short_mt = mt_url.split("/")[-1]
            
            # Async fetch details (or sync in thread)? 
            # We are already in async, but `get_machine_type_details_sync` uses a client.
            # Doing it sequentially for now inside this thread wrapper? 
            # Actually we can't await inside the list comprehension easily if we use `await asyncio.to_thread` for the whole block.
            # Warning: calling sync API here might block the loop if not careful.
            # But we are inside `async def`, so we should use `await asyncio.to_thread`.
            
            vcpu, ram_gb = await asyncio.to_thread(get_machine_type_details_sync, short_mt, ZONE, PROJECT_ID)
            
            # Networking
            priv_ip = "N/A"
            pub_ip = "N/A"
            if inst.network_interfaces:
                nic0 = inst.network_interfaces[0]
                priv_ip = nic0.network_i_p
                if nic0.access_configs:
                    pub_ip = nic0.access_configs[0].nat_i_p
            
            # Disks
            disks = inst.disks
            total_disk_gb = 0
            disk_details = []
            os_name = "Unknown"
            
            for d in disks:
                sz = d.disk_size_gb
                total_disk_gb += sz
                kind = "Boot" if d.boot else "Data"
                disk_details.append(f"{kind} {sz}GB")
                
                if d.boot and d.licenses:
                    for lic in d.licenses:
                        # License URL
                        parts = lic.split("/")
                        license_name = parts[-1]
                        if any(x in lic for x in ["debian", "rhel", "centos", "ubuntu", "windows", "sles"]):
                             os_name = license_name.replace("-", " ").title()
                             break
            
            # Recommendations
            rec_text, savings = await get_instance_recommendations(PROJECT_ID, ZONE, name)
            
            report_lines.append(f"Instance Name:           {name}")
            report_lines.append(f"Project ID:              {PROJECT_ID}")
            report_lines.append(f"Instance Status:         {status}")
            report_lines.append(f"Creation Timestamp:      {creation_ts}")
            report_lines.append(f"Machine Type:            {short_mt}")
            report_lines.append(f"Number of vCPUs:         {vcpu}")
            report_lines.append(f"RAM (GB):                {ram_gb}")
            report_lines.append(f"Total Disk Size (GB):    {total_disk_gb} ({len(disks)} disks: {', '.join(disk_details)})")
            report_lines.append(f"IP Address:              {priv_ip} (Internal) / {pub_ip} (External)")
            report_lines.append(f"Zone:                    {ZONE}")
            report_lines.append(f"Operating System:        {os_name}")
            report_lines.append(f"Sizing Recommendations:  {rec_text}")
            report_lines.append(f"Estimated Monthly Savings (USD): ${savings}")
            report_lines.append("-" * 50)
            report_lines.append("")

        return "\n".join(report_lines)

    except Exception as e:
        logger.error(f"Error generating report: {e}")
        return f"Error generating report: {e}"

# Keeping legacy create_custom_instance using gcloud for now (Complex parameters)
async def create_custom_instance(name, machine_type="n2-custom-2-4096", image_family="rhel-9", boot_disk_size="10", extra_disk_size="0"):
    """
    Creates a new custom instance.
    Args:
        name: Name of the new instance.
        machine_type: Machine type (default: n2-custom-2-4096).
        image_family: Image family (default: rhel-9).
        boot_disk_size: Size of boot disk in GB (default: 10).
        extra_disk_size: Size of additional data disk in GB (default: 0).
    """
    # Sanitize name to comply with GCE regex (no underscores, lowercase)
    final_name = name.lower().replace("_", "-")
    
    # Determine image project based on family
    image_project = "rhel-cloud"
    if "debian" in image_family:
        image_project = "debian-cloud"
    elif "ubuntu" in image_family:
        image_project = "ubuntu-os-cloud"
    elif "centos" in image_family:
        image_project = "centos-cloud"

    cmd = [
        "gcloud", "compute", "instances", "create", final_name,
        f"--project={PROJECT_ID}",
        f"--zone={ZONE}",
        f"--machine-type={machine_type}",
        "--network-interface=network-tier=PREMIUM,stack-type=IPV4_ONLY,subnet=default,no-address",
        "--maintenance-policy=MIGRATE",
        "--provisioning-model=STANDARD",
        "--service-account=30162433848-compute@developer.gserviceaccount.com",
        "--scopes=https://www.googleapis.com/auth/devstorage.read_only,https://www.googleapis.com/auth/logging.write,https://www.googleapis.com/auth/monitoring.write,https://www.googleapis.com/auth/servicecontrol,https://www.googleapis.com/auth/service.management.readonly,https://www.googleapis.com/auth/trace.append",
        f"--create-disk=auto-delete=yes,boot=yes,device-name={final_name},image=projects/{image_project}/global/images/family/{image_family},mode=rw,size={boot_disk_size},type=projects/{PROJECT_ID}/zones/{ZONE}/diskTypes/pd-balanced",
        "--labels=goog-ec-src=vm_add-gcloud",
        "--format=json"
    ]

    # Add extra disk only if requested
    if extra_disk_size and int(extra_disk_size) > 0:
        cmd.insert(-2, f"--create-disk=device-name={final_name}-data,mode=rw,name={final_name}-data,size={extra_disk_size},type=projects/{PROJECT_ID}/zones/{ZONE}/diskTypes/pd-balanced,auto-delete=yes")
    
    try:
        output = await asyncio.to_thread(
            lambda: subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
        )
        return f"Instance '{name}' created successfully.\nOutput: {output[:200]}..."
    except subprocess.CalledProcessError as e:
        return f"Error creating instance: {e.output}"
