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
            lambda: subprocess.check_output(cmd, text=True).strip()
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
        output = subprocess.check_output(cmd, text=True).strip()
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
        mt_json = subprocess.check_output(cmd_mt, text=True).strip()
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
        output = subprocess.check_output(cmd, text=True).strip()
        data = json.loads(output)
        
        # If describe, wrap in list to reuse logic
        if isinstance(data, dict):
            data = [data]
        
        report_lines = []
        for info in data:
            name = info.get("name", "Unknown")
            status = info.get("status", "Turned Off/Unknown")
            machine_type_url = info.get("machineType", "")
            machine_type = machine_type_url.split("/")[-1] if "/" in machine_type_url else machine_type_url
            cpu_platform = info.get("cpuPlatform", "Unknown CPU Platform")
            
            # Fallback for terminated instances where CPU Platform is unknown
            if cpu_platform == "Unknown CPU Platform" or not cpu_platform:
                # Heuristics based on https://cloud.google.com/compute/docs/cpu-platforms
                prefix = machine_type.split("-")[0]
                
                if prefix == "e2":
                    cpu_platform = "Intel Broadwell/Haswell/Skylake (Estimated)"
                elif prefix in ["n2", "n2-custom"]:
                     cpu_platform = "Intel Cascade Lake/Ice Lake (Estimated)"
                elif prefix == "n1":
                     cpu_platform = "Intel Skylake/Haswell/Broadwell/Ivy Bridge (Estimated)"
                elif prefix == "n2d":
                     cpu_platform = "AMD EPYC Milan (Estimated)"
                elif prefix == "t2d":
                     cpu_platform = "AMD EPYC Milan (Estimated)"
                elif prefix == "t2a":
                     cpu_platform = "Ampere Altra (ARM)"
                elif prefix in ["c2", "m2"]:
                     cpu_platform = "Intel Cascade Lake/Skylake (Estimated)"
                elif prefix in ["c3", "n4", "c4"]:
                     cpu_platform = "Intel Sapphire Rapids/Emerald Rapids (Estimated)"
                elif prefix == "a2":
                     cpu_platform = "Intel Cascade Lake (Estimated)"
                elif prefix == "g2":
                     cpu_platform = "Intel Cascade Lake (Estimated)"
                elif "custom" in machine_type:
                    # Fallback for generic custom without family prefix (rare) OR n2-custom matches prefix logic above
                     if "n2-custom" in machine_type:
                         cpu_platform = "Intel Cascade Lake/Ice Lake (Estimated)"
                     else:
                         cpu_platform = "Intel/AMD (Unknown model)"
            
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
            disk_info = []
            os_name = "Unknown"
            
            for d in disks:
                 sz = d.get("diskSizeGb", "?")
                 kind = "Boot" if d.get("boot") else "Data"
                 disk_info.append(f"{kind} ({sz} GB)")
                 
                 # Try to guess OS from licenses on boot disk
                 if d.get("boot") and d.get("licenses"):
                     for lic in d.get("licenses"):
                         if "debian" in lic: os_name = "Debian"
                         elif "rhel" in lic: os_name = "RHEL"
                         elif "centos" in lic: os_name = "CentOS"
                         elif "ubuntu" in lic: os_name = "Ubuntu"
                         elif "windows" in lic: os_name = "Windows"
            
            # Fetch VCPU/RAM details
            # Note: sequential fetching for 'all' might be slow but provides the required detail.
            # In a production agent we might cache or parallelize this.
            vcpu, ram_gb = get_machine_type_details(machine_type, ZONE, PROJECT_ID)

            report_lines.append("=" * 50)
            report_lines.append(f" GCE INSTANCE REPORT: {name}")
            report_lines.append("=" * 50)
            report_lines.append(f"Status:       {status}")
            report_lines.append(f"Region/Zone:  {ZONE}")
            report_lines.append(f"Machine Type: {machine_type} ({vcpu} vCPU, {ram_gb} GB RAM)")
            report_lines.append(f"CPU Platform: {cpu_platform}")
            report_lines.append("-" * 50)
            report_lines.append(f"Internal IP:  {priv_ip}")
            report_lines.append(f"External IP:  {pub_ip}")
            report_lines.append("-" * 50)
            report_lines.append(f"OS:           {os_name}")
            report_lines.append(f"Disk:         {', '.join(disk_info)}")
            report_lines.append("=" * 50)
            report_lines.append("") # Empty line between intances

        return "\n".join(report_lines)

    except Exception as e:
        return f"Error generating report: {e}"

async def start_instance(instance_name):
    """Starts a specific GCE instance."""
    if instance_name == "all":
         # Re-implement start all locally or reuse main logic logic?
         # For simplicity, let's just support single or handle 'all' by listing
         # But the agent might handle loop. Let's support strict name for now
         # checking if user meant all?
         pass
         
    # Call MCP
    payload = {
        "project": PROJECT_ID,
        "zone": ZONE,
        "name": instance_name
    }
    res = await call_mcp_tool("start_instance", payload)
    return f"Start Instance '{instance_name}': {res}"

async def stop_instance(instance_name):
    """Stops a specific GCE instance."""
    payload = {
        "project": PROJECT_ID,
        "zone": ZONE,
        "name": instance_name
    }
    res = await call_mcp_tool("stop_instance", payload)
    return f"Stop Instance '{instance_name}': {res}"

async def create_custom_instance(name, machine_type="n2-custom-2-4096", image_family="rhel-9", disk_size="10"):
    """
    Creates a new custom instance.
    Args:
        name: Name of the new instance.
        machine_type: Machine type (default: n2-custom-2-4096).
        image_family: Image family (default: rhel-9).
        disk_size: Size of additional data disk in GB (default: 10).
    """
    cmd = [
        "gcloud", "compute", "instances", "create", name,
        f"--project={PROJECT_ID}",
        f"--zone={ZONE}",
        f"--machine-type={machine_type}",
        "--network-interface=network-tier=PREMIUM,stack-type=IPV4_ONLY,subnet=default,no-address",
        "--maintenance-policy=MIGRATE",
        "--provisioning-model=STANDARD",
        "--service-account=646392362677-compute@developer.gserviceaccount.com",
        "--scopes=https://www.googleapis.com/auth/devstorage.read_only,https://www.googleapis.com/auth/logging.write,https://www.googleapis.com/auth/monitoring.write,https://www.googleapis.com/auth/servicecontrol,https://www.googleapis.com/auth/service.management.readonly,https://www.googleapis.com/auth/trace.append",
        f"--create-disk=auto-delete=yes,boot=yes,device-name={name},image=projects/rhel-cloud/global/images/family/{image_family},mode=rw,size=20,type=projects/{PROJECT_ID}/zones/{ZONE}/diskTypes/pd-balanced",
        f"--create-disk=device-name={name}-data,mode=rw,name={name}-data,size={disk_size},type=projects/{PROJECT_ID}/zones/{ZONE}/diskTypes/pd-balanced,auto-delete=yes",
        "--labels=goog-ec-src=vm_add-gcloud",
        "--format=json"
    ]
    
    try:
        output = await asyncio.to_thread(
            lambda: subprocess.check_output(cmd, text=True).strip()
        )
        return f"Instance '{name}' created successfully.\nOutput: {output[:200]}..."
    except subprocess.CalledProcessError as e:
        return f"Error creating instance: {e.output}"
