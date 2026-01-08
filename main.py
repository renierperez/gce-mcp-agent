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

async def stop_instance(client, headers):
    payload = {
        "project": PROJECT_ID,
        "zone": ZONE,
        "name": INSTANCE_NAME
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

async def report_instance(client, headers):
    print(f"Fetching enhanced metadata for instance: {INSTANCE_NAME}...")
    
    try:
        # Use gcloud to get full instance details in JSON format
        cmd = [
            "gcloud", "compute", "instances", "describe", INSTANCE_NAME,
            f"--zone={ZONE}", 
            f"--project={PROJECT_ID}",
            "--format=json"
        ]
        
        # Run synchronous subprocess in a thread
        json_output = await asyncio.to_thread(
            lambda: subprocess.check_output(cmd, text=True).strip()
        )
        
        info_data = json.loads(json_output)
        
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
        except Exception as e:
            print(f"Warning: Could not fetch machine type specs: {e}")

        cpu_platform = info_data.get("cpuPlatform", "Unknown CPU Platform")

        # Disks / OS
        disks = info_data.get("disks", [])
        boot_disk_size = "?"
        os_name = "Unknown Linux/OS"
        
        for disk in disks:
            if disk.get("boot", False):
                boot_disk_size = disk.get("diskSizeGb", "?")
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
                break

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
        print(f"Boot Disk:    {boot_disk_size} GB")
        print("="*50 + "\n")

    except subprocess.CalledProcessError as e:
        print(f"Error fetching instance details via gcloud: {e}")
        if e.output:
            print(f"gcloud output: {e.output}")
    except json.JSONDecodeError:
        print("Error parsing gcloud output JSON.")

async def main():
    parser = argparse.ArgumentParser(description="GCE Manager Agent CLI")
    parser.add_argument("command", choices=["create", "stop", "start", "list", "report"], help="Action to perform")
    args = parser.parse_args()

    try:
        headers = await get_authenticated_headers()
        
        async with httpx.AsyncClient() as client:
            print(f"Connecting to MCP server at {MCP_URL}...")
            
            if args.command == "create":
                await create_instance(client, headers)
            elif args.command == "stop":
                await stop_instance(client, headers)
            elif args.command == "start":
                await start_instance(client, headers)
            elif args.command == "list":
                await list_instances(client, headers)
            elif args.command == "report":
                await report_instance(client, headers)

    except Exception as e:
        print(f"\nError: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
