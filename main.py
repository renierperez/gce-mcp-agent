import asyncio
import argparse
import json
import traceback
import subprocess
import httpx

# Configuration
PROJECT_ID = "autonomous-agent-479317"
ZONE = "us-central1-a"
INSTANCE_NAME = "mcp-test-instance-v1"
SERVICE_ACCOUNT = "mcp-manager@autonomous-agent-479317.iam.gserviceaccount.com"
MCP_URL = "https://compute.googleapis.com/mcp"

async def get_authenticated_headers():
    print("Authenticating with Google Cloud (Impersonation via gcloud)...")
    try:
        # Use gcloud to get the access token with impersonation
        cmd = [
            "gcloud", "auth", "print-access-token",
            f"--impersonate-service-account={SERVICE_ACCOUNT}",
            "--format=value(token)"
        ]
        # Run synchronous subprocess in a thread to avoid blocking event loop
        token = await asyncio.to_thread(
            lambda: subprocess.check_output(cmd, text=True).strip()
        )
        
        print(f"Impersonated Service Account: {SERVICE_ACCOUNT}")
        return {
            "Authorization": f"Bearer {token}",
        }
    except subprocess.CalledProcessError as e:
        print(f"Error getting token via gcloud: {e}")
        if e.output:
            print(f"gcloud output: {e.output}")
        raise

async def call_mcp_tool(client, headers, tool_name, arguments):
    print(f"Executing tool: {tool_name}...")
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        },
        "id": 1
    }
    
    response = await client.post(MCP_URL, headers=headers, json=payload, timeout=120.0)
    print(f"Response Status: {response.status_code}")
    
    if response.status_code == 200:
        print("Response JSON:")
        print(json.dumps(response.json(), indent=2))
    else:
        print(f"Error: {response.text}")

async def create_instance(client, headers):
    payload = {
        "project": PROJECT_ID,
        "zone": ZONE,
        "name": INSTANCE_NAME,
        "machineType": "e2-micro",
        "imageProject": "debian-cloud",
        "imageFamily": "debian-11",
        "maintenancePolicy": "MIGRATE"
    }
    await call_mcp_tool(client, headers, "create_instance", payload)

async def stop_instance(client, headers, instance_name=None, stop_all=False):
    instances_to_stop = []

    if stop_all:
        print(f"Listing all instances in {ZONE} to stop them...")
        try:
            # Reusing report logic for listing could be cleaner, but a simple list is enough
            # Use MCP list_instances logic if possible, but we don't return the list there.
            # Using gcloud for robustness as used in report
            cmd = [
                "gcloud", "compute", "instances", "list",
                f"--filter=zone:({ZONE}) AND status:RUNNING",
                f"--project={PROJECT_ID}",
                "--format=json"
            ]
            output = await asyncio.to_thread(
                lambda: subprocess.check_output(cmd, text=True).strip()
            )
            data = json.loads(output)
            for inst in data:
                instances_to_stop.append(inst['name'])
        except Exception as e:
            print(f"Error listing instances to stop: {e}")
            return
    else:
        instances_to_stop.append(instance_name if instance_name else INSTANCE_NAME)

    if not instances_to_stop:
        print("No RUNNING instances found to stop.")
        return

    for name in instances_to_stop:
        print(f"Stopping instance: {name}...")
        payload = {
            "project": PROJECT_ID,
            "zone": ZONE,
            "name": name
        }
        await call_mcp_tool(client, headers, "stop_instance", payload)

async def start_instance(client, headers):
    payload = {
        "project": PROJECT_ID,
        "zone": ZONE,
        "name": INSTANCE_NAME
    }
    # Assuming start_instance has similar schema; if not available, this might fail or need check
    # But usually start_instance schema mirrors stop_instance
    await call_mcp_tool(client, headers, "start_instance", payload)

async def list_instances(client, headers):
    payload = {
        "project": PROJECT_ID,
        "zone": ZONE
    }
    await call_mcp_tool(client, headers, "list_instances", payload)

async def print_instance_report(info_data):
    # Extract Details
    name = info_data.get("name", "Unknown")
    status = info_data.get("status", "Turned Off/Unknown")
    machine_type_url = info_data.get("machineType", "")
    machine_type = machine_type_url.split("/")[-1] if "/" in machine_type_url else machine_type_url
    zone_url = info_data.get("zone", "")
    region = zone_url.split("/")[-1] if "/" in zone_url else zone_url
    
    # Networking
    network_interfaces = info_data.get("networkInterfaces", [])
    private_ip = "N/A"
    public_ip = "N/A"
    
    if network_interfaces:
        nic0 = network_interfaces[0]
        private_ip = nic0.get("networkIP", "N/A")
        access_configs = nic0.get("accessConfigs", [])
        if access_configs:
            public_ip = access_configs[0].get("natIP", "N/A")
    
    # Machine Type Specs
    vcpu = "?"
    ram_gb = "?"
    try:
        # Optimization: To avoid N+1 calls, only fetch if we don't know it. 
        # But for 'list all', N calls might be slow. 
        # For now, keep it simple. If status is TERMINATED, maybe skip? NO, user wants report.
        # We can just fetch it.
        cmd_mt = [
            "gcloud", "compute", "machine-types", "describe", machine_type,
            f"--zone={ZONE}",
            f"--project={PROJECT_ID}",
            "--format=json"
        ]
        mt_json = await asyncio.to_thread(
            lambda: subprocess.check_output(cmd_mt, text=True).strip()
        )
        mt_data = json.loads(mt_json)
        vcpu = mt_data.get("guestCpus", "?")
        memory_mb = mt_data.get("memoryMb", 0)
        ram_gb = f"{memory_mb / 1024:.1f}" if memory_mb else "?"
    except Exception:
        # Some custom types might fail 'describe' if not handled perfectly, 
        # or if name is custom-..., we can parse it from name too!
        if "custom" in machine_type:
            try:
                # n2-custom-2-4096 -> 2 vcpu, 4096 mb
                parts = machine_type.split("-")
                vcpu = parts[2]
                memory_mb = int(parts[3])
                ram_gb = f"{memory_mb / 1024:.1f}"
            except:
                pass
        pass

    cpu_platform = info_data.get("cpuPlatform", "Unknown CPU Platform")

    # Disks / OS
    disks = info_data.get("disks", [])
    disk_info_list = []
    os_name = "Unknown Linux/OS"
    
    for disk in disks:
        size = disk.get("diskSizeGb", "?")
        is_boot = disk.get("boot", False)
        kind = "Boot" if is_boot else "Data"
        disk_name = disk.get("deviceName", "unknown")
        disk_info_list.append(f"{kind} ({disk_name}): {size} GB")

        # Try to detect OS from boot disk
        if is_boot:
            licenses = disk.get("licenses", [])
            for lic in licenses:
                if "debian" in lic:
                    os_name = "Debian"
                    if "11" in lic: os_name += " 11"
                    if "12" in lic: os_name += " 12"
                elif "ubuntu" in lic:
                    os_name = "Ubuntu"
                elif "windows" in lic:
                    os_name = "Windows"
                elif "centos" in lic:
                    os_name = "CentOS"
                elif "rhel" in lic:
                    os_name = "RHEL"

    # Print Report
    print("\n" + "="*50)
    print(f" GCE INSTANCE REPORT: {name}")
    print("="*50)
    print(f"Status:       {status}")
    print(f"Region/Zone:  {region}")
    print(f"Machine Type: {machine_type} ({vcpu} vCPU, {ram_gb} GB RAM)")
    print(f"CPU Platform: {cpu_platform}")
    print("-" * 50)
    print(f"Internal IP:  {private_ip}")
    print(f"External IP:  {public_ip}")
    print("-" * 50)
    print(f"OS:           {os_name}")
    for d_info in disk_info_list:
        print(f"Disk:         {d_info}")
    print("="*50 + "\n")

async def report_instance(client, headers, instance_name=None, report_all=False):
    if report_all:
        print(f"Fetching enhanced metadata for ALL instances in {ZONE}...")
        cmd = [
            "gcloud", "compute", "instances", "list",
            f"--filter=zone:({ZONE})",
            f"--project={PROJECT_ID}",
            "--format=json"
        ]
    else:
        name_to_use = instance_name if instance_name else INSTANCE_NAME
        print(f"Fetching enhanced metadata for instance: {name_to_use}...")
        cmd = [
            "gcloud", "compute", "instances", "describe", name_to_use,
            f"--zone={ZONE}", 
            f"--project={PROJECT_ID}",
            "--format=json"
        ]
    
    try:
        json_output = await asyncio.to_thread(
            lambda: subprocess.check_output(cmd, text=True).strip()
        )
        
        data = json.loads(json_output)
        
        if report_all:
            # data is list
            for instance in data:
                await print_instance_report(instance)
        else:
            # data is dict
            await print_instance_report(data)

    except subprocess.CalledProcessError as e:
        print(f"Error fetching instance details via gcloud: {e}")
        if e.output:
            print(f"gcloud output: {e.output}")
    except json.JSONDecodeError:
        print("Error parsing gcloud output JSON.")

async def create_custom_instance(headers, name, machine_type, image_family, image_project, extra_disk_size_gb):
    print(f"Creating Custom Instance: {name}...")
    print(f"  Type: {machine_type}")
    print(f"  OS:   {image_family} ({image_project})")
    print(f"  Disk: +{extra_disk_size_gb}GB Data Disk")

    try:
        # Construct gcloud command
        cmd = [
            "gcloud", "compute", "instances", "create", name,
            f"--project={PROJECT_ID}",
            f"--zone={ZONE}",
            f"--machine-type={machine_type}",
            f"--image-family={image_family}",
            f"--image-project={image_project}",
            "--no-address",  # Prevent external IP creation to avoid policy violation
            # Boot disk is auto-created from image.
            # Add extra data disk
            f"--create-disk=mode=rw,size={extra_disk_size_gb},type=pd-standard,name={name}-data-1,auto-delete=yes"
        ]
        
        print(f"  Running: {' '.join(cmd)}")
        
        # Run gcloud
        output = await asyncio.to_thread(
            lambda: subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        )
        print("  \nSuccess! gcloud output:")
        print(output)

    except subprocess.CalledProcessError as e:
        print(f"Error creating instance: {e}")
        if e.output:
            print(f"gcloud output: {e.output}")

async def main():
    parser = argparse.ArgumentParser(description="GCE Manager Agent CLI")
    parser.add_argument("command", choices=["create", "stop", "start", "list", "report", "create-custom"], help="Action to perform")
    
    # Optional args for create-custom
    parser.add_argument("--name", default=INSTANCE_NAME, help="Instance Name")
    parser.add_argument("--machine-type", default="e2-micro", help="Machine Type")
    parser.add_argument("--image-family", default="debian-11", help="Image Family")
    parser.add_argument("--image-project", default="debian-cloud", help="Image Project")
    parser.add_argument("--disk-size", default="10", help="Extra Disk Size in GB")
    parser.add_argument("--all", action="store_true", help="Report all instances")

    args = parser.parse_args()

    try:
        headers = await get_authenticated_headers()
        
        async with httpx.AsyncClient() as client:
            print(f"Connecting to MCP server at {MCP_URL}...")
            
            if args.command == "create":
                await create_instance(client, headers)
            elif args.command == "create-custom":
                await create_custom_instance(
                    headers, 
                    args.name, 
                    args.machine_type, 
                    args.image_family, 
                    args.image_project, 
                    args.disk_size
                )
            elif args.command == "stop":
                await stop_instance(client, headers, args.name, args.all)
            elif args.command == "start":
                await start_instance(client, headers)
            elif args.command == "list":
                await list_instances(client, headers)
            elif args.command == "report":
                await report_instance(client, headers, args.name, args.all)

    except Exception as e:
        print(f"\nError: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
