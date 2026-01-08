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

def get_instance_report(instance_name="all"):
    """
    Generates a detailed report for a specific instance or all instances.
    Args:
        instance_name: Name of the instance, or 'all' for all instances.
    """
    # This logic mimics the 'report_instance' from main.py
    # We will reuse the gcloud commands but return string output instead of printing
    
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
            
            # Networking
            priv_ip = "N/A"
            network_interfaces = info.get("networkInterfaces", [])
            if network_interfaces:
                priv_ip = network_interfaces[0].get("networkIP", "N/A")
            
            # Disks
            disks = info.get("disks", [])
            disk_info = []
            for d in disks:
                 sz = d.get("diskSizeGb", "?")
                 kind = "Boot" if d.get("boot") else "Data"
                 disk_info.append(f"{kind} ({sz} GB)")
            
            report_lines.append(f"INSTANCE: {name}")
            report_lines.append(f"  Status: {status}")
            report_lines.append(f"  Type: {machine_type}")
            report_lines.append(f"  IP: {priv_ip}")
            report_lines.append(f"  Disks: {', '.join(disk_info)}")
            report_lines.append("-" * 30)

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
