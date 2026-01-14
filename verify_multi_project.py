import asyncio
import traceback
from tools import list_managed_projects, list_instances, get_instance_report

async def run_verification():
    print("🔍 Verifying Multi-Project Support...")
    
    # 1. List Managed Projects
    print("\n[1] Listing Managed Projects:")
    projects = await list_managed_projects()
    print(projects)
    
    if "autonomous-agent-479317" not in projects:
        print("❌ Error: Default project not found in managed list!")
        return

    # 2. List Instances (Default Project implicit)
    print("\n[2] Listing Instances (Default implicit):")
    try:
        res = await list_instances()
        print(f"Result length: {len(res)} chars")
    except Exception as e:
        print(f"❌ Error: {e}")

    # 3. List Instances (Explicit Project)
    print("\n[3] Listing Instances (Explicit Project):")
    try:
        res = await list_instances(project_id="autonomous-agent-479317")
        print(f"Result: {res[:200]}...") # truncate
    except Exception as e:
        print(f"❌ Error: {e}")

    # 4. List Instances (Invalid Project)
    print("\n[4] Listing Instances (Invalid Project):")
    try:
        res = await list_instances(project_id="invalid-project-id")
        print(res)
    except Exception as e:
        print(f"Received expected error or message: {e}")
        
    print("\n✅ Verification Complete.")

if __name__ == "__main__":
    try:
        asyncio.run(run_verification())
    except Exception as e:
        traceback.print_exc()
