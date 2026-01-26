import asyncio
import logging
from tools import get_instance_sku_report

# Configure logging
logging.basicConfig(level=logging.INFO)

async def main():
    print("Testing get_instance_sku_report...")
    try:
        # We need a real instance name that exists in the user's project
        # User mentioned "demobch-sql"
        report = await get_instance_sku_report("demobch-sql")
        print("\n--- REPORT START ---")
        print(report)
        print("--- REPORT END ---\n")
    except Exception as e:
        print(f"CRASHED: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
