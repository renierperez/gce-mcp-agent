import asyncio
import subprocess
import json
import httpx

# Configuration
PROJECT_ID = "autonomous-agent-479317"
ZONE = "us-central1-a"
INSTANCE_NAME = "mcp-test-instance-v1"
SERVICE_ACCOUNT = "mcp-manager@autonomous-agent-479317.iam.gserviceaccount.com"
MCP_URL = "https://compute.googleapis.com/mcp"

async def get_authenticated_headers():
    # Helper to get headers (Internal use)
    try:
        cmd = [
            "gcloud", "auth", "print-access-token",
            f"--impersonate-service-account={SERVICE_ACCOUNT}",
            "--format=value(token)"
        ]
        token = await asyncio.to_thread(
            lambda: subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
        )
        return {
            "Authorization": f"Bearer {token}",
        }
    except Exception as e:
        print(f"Error getting token: {e}")
        return {}

async def call_mcp_tool(tool_name, arguments):
    # Helper to call MCP (Internal use)
    headers = await get_authenticated_headers()
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        },
        "id": 1
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(MCP_URL, headers=headers, json=payload, timeout=120.0)
            if response.status_code == 200:
                return response.json()
            else:
                return {"error": response.text}
        except Exception as e:
            return {"error": str(e)}

# --- Tools exposed to the Agent ---

def list_instances():
    """Lists all GCE instances in the configured zone."""
    # Using gcloud for consistent reporting structure as used in main
    cmd = [
        "gcloud", "compute", "instances", "list",
        f"--filter=zone:({ZONE})",
        f"--project={PROJECT_ID}",
        "--format=json"
    ]
    try:
        output = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
        data = json.loads(output)
        # Simplify output for LLM
        summary = []
        for inst in data:
            summary.append(f"Name: {inst['name']}, Status: {inst['status']}, IP: {inst['networkInterfaces'][0].get('networkIP', 'N/A')}")
        return "\n".join(summary)
    except Exception as e:
        return f"Error listing instances: {e}"

# Helper for Machine Type details (vCPU/RAM)
def get_machine_type_details(machine_type, zone, project_id):
    # Try to describe standard type
    vcpu = "?"
    ram_gb = "?"
    try:
        cmd_mt = [
            "gcloud", "compute", "machine-types", "describe", machine_type,
            f"--zone={zone}",
            f"--project={project_id}",
            "--format=json"
        ]
        mt_json = subprocess.check_output(cmd_mt, text=True, stderr=subprocess.DEVNULL).strip()
        mt_data = json.loads(mt_json)
        vcpu = mt_data.get("guestCpus", "?")
        memory_mb = mt_data.get("memoryMb", 0)
        ram_gb = f"{memory_mb / 1024:.1f}" if memory_mb else "?"
    except Exception:
        # Fallback for custom types (e.g. n2-custom-2-4096)
        # Format: <family>-custom-<vcpus>-<mem_mb>
        import re
        match = re.search(r".*-custom-(\d+)-(\d+)", machine_type)
        if match:
             vcpu = match.group(1)
             memory_mb = int(match.group(2))
             ram_gb = f"{memory_mb / 1024:.1f}"
    
    return vcpu, ram_gb

def get_instance_recommendations(project, zone, instance_name):
    """
    Fetches sizing recommendations and estimated savings.
    Returns: (recommendation_text, savings_usd)
    """
    rec_text = "None"
    savings = "0.00"
    
    try:
        # Check MachineTypeRecommender
        cmd = [
            "gcloud", "recommender", "recommendations", "list",
            f"--project={project}",
            f"--location={zone}",
            f"--recommender=google.compute.instance.MachineTypeRecommender",
            "--format=json"
        ]
        output = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
        recs = json.loads(output)
        
        for r in recs:
            # Filter for specific instance (target resource contains instance name)
            if f"/instances/{instance_name}" in r.get("content", {}).get("operationGroups", [{}])[0].get("operations", [{}])[0].get("resource", ""):
                description = r.get("description", "")
                rec_text = description
                
                # Calculate savings
                cost = r.get("primaryImpact", {}).get("costProjection", {}).get("cost", {})
                if cost.get("currencyCode") == "USD":
                    units = int(cost.get("units", "0"))
                    nanos = cost.get("nanos", 0)
                    total = abs(units + (nanos / 1e9)) # Savings are usually negative
                    savings = f"{total:.2f}"
                break
                
    except Exception:
        pass
        
    return rec_text, savings

def get_instance_report(instance_name="all"):
    """
    Generates a detailed report for a specific instance or all instances.
    Args:
        instance_name: Name of the instance, or 'all' for all instances.
    """
    target_name = instance_name if instance_name and instance_name != "all" else None
    
    cmd = [
        "gcloud", "compute", "instances", "describe" if target_name else "list",
        f"--zone={ZONE}" if target_name else f"--filter=zone:({ZONE})",
        f"--project={PROJECT_ID}",
        "--format=json"
    ]
    
    if target_name:
        cmd.insert(4, target_name) # Insert name after 'describe'

    try:
        output = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
        data = json.loads(output)
        
        # If describe, wrap in list to reuse logic
        if isinstance(data, dict):
            data = [data]
        
        report_lines = []
        # Add Header Summary
        report_lines.append("=" * 50)
        report_lines.append(f" PROJECT: {PROJECT_ID}")
        report_lines.append(f" TOTAL INSTANCES: {len(data)}")
        report_lines.append("=" * 50)
        report_lines.append("") # Spacing

        for info in data:
            name = info.get("name", "Unknown")
            status = info.get("status", "Turned Off/Unknown")
            creation_ts = info.get("creationTimestamp", "Unknown")
            machine_type_url = info.get("machineType", "")
            machine_type = machine_type_url.split("/")[-1] if "/" in machine_type_url else machine_type_url
            
            # Fetch VCPU/RAM details
            vcpu, ram_gb = get_machine_type_details(machine_type, ZONE, PROJECT_ID)
            
            # Networking
            priv_ip = "N/A"
            pub_ip = "N/A"
            network_interfaces = info.get("networkInterfaces", [])
            if network_interfaces:
                nic0 = network_interfaces[0]
                priv_ip = nic0.get("networkIP", "N/A")
                if nic0.get("accessConfigs"):
                     pub_ip = nic0["accessConfigs"][0].get("natIP", "N/A")
            
            # Disks
            disks = info.get("disks", [])
            total_disk_gb = 0
            disk_details = []
            os_name = "Unknown"
            
            for d in disks:
                 sz = int(d.get("diskSizeGb", "0"))
                 total_disk_gb += sz
                 kind = "Boot" if d.get("boot") else "Data"
                 disk_details.append(f"{kind} {sz}GB")
                 
                 # Try to guess OS from licenses on boot disk
                 if d.get("boot") and d.get("licenses"):
                     for lic in d.get("licenses"):
                         # License URL format: .../global/licenses/<license-name>
                         # We want to extract <license-name> and format it nicely
                         if "debian" in lic or "rhel" in lic or "centos" in lic or "ubuntu" in lic or "windows" in lic or "sles" in lic:
                             parts = lic.split("/")
                             license_name = parts[-1]
                             # Formatting: debian-11-bullseye -> Debian 11 Bullseye
                             os_name = license_name.replace("-", " ").title()
                             # Shorten common prefixes if redundant
                             if os_name.startswith("Debian ") or os_name.startswith("Rhel ") or os_name.startswith("Ubuntu ") or os_name.startswith("Centos "):
                                 pass # Already good
                             elif "Windows" in os_name:
                                 pass # Windows serv...
                             break

            # Recommendations
            rec_text, savings = get_instance_recommendations(PROJECT_ID, ZONE, name)

            report_lines.append(f"Instance Name:           {name}")
            report_lines.append(f"Project ID:              {PROJECT_ID}")
            report_lines.append(f"Instance Status:         {status}")
            report_lines.append(f"Creation Timestamp:      {creation_ts}")
            report_lines.append(f"Machine Type:            {machine_type}")
            report_lines.append(f"Number of vCPUs:         {vcpu}")
            report_lines.append(f"RAM (GB):                {ram_gb}")
            report_lines.append(f"Total Disk Size (GB):    {total_disk_gb} ({len(disks)} disks: {', '.join(disk_details)})")
            report_lines.append(f"IP Address:              {priv_ip} (Internal) / {pub_ip} (External)")
            report_lines.append(f"Zone:                    {ZONE}")
            report_lines.append(f"Operating System:        {os_name}")
            report_lines.append(f"Sizing Recommendations:  {rec_text}")
            report_lines.append(f"Estimated Monthly Savings (USD): ${savings}")
            report_lines.append("-" * 50)
            report_lines.append("") # Empty line between intances

        return "\n".join(report_lines)

    except Exception as e:
        return f"Error generating report: {e}"

async def start_instance(instance_name):
    """Starts a specific GCE instance."""
    if instance_name == "all":
         # TODO: Handle 'all' logic if needed, for now restrict or iterate
         return "Updating 'all' instances is not yet supported in this function."

    cmd = [
        "gcloud", "compute", "instances", "start", instance_name,
        f"--zone={ZONE}",
        f"--project={PROJECT_ID}",
        "--format=json"
    ]
    try:
        output = await asyncio.to_thread(
            lambda: subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()
        )
        # Parse simplified output?
        return f"Instance '{instance_name}' started successfully.\nDetails: {output[:200]}..."
    except subprocess.CalledProcessError as e:
        return f"Error starting instance '{instance_name}': {e.output}"
    except Exception as e:
        return f"Unexpected error starting instance: {e}"

async def stop_instance(instance_name):
    """Stops a specific GCE instance."""
    if instance_name == "all":
         return "Updating 'all' instances is not yet supported in this function."

    cmd = [
        "gcloud", "compute", "instances", "stop", instance_name,
        f"--zone={ZONE}",
        f"--project={PROJECT_ID}",
        "--format=json"
    ]
    try:
        output = await asyncio.to_thread(
            lambda: subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()
        )
        return f"Instance '{instance_name}' stopped successfully.\nDetails: {output[:200]}..."
    except subprocess.CalledProcessError as e:
        return f"Error stopping instance '{instance_name}': {e.output}"
    except Exception as e:
        return f"Unexpected error stopping instance: {e}"

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
