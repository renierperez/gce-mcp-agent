
import asyncio
import logging
from unittest.mock import MagicMock

# Configure logging to verify debug output
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tools")

# Mocking the missing dependencies for the test
async def _get_instance_details_string_test():
    # Mock Data
    project_id = "test-project"
    zone = "us-central1-a"
    
    # Mock Instance Object
    inst = MagicMock()
    inst.name = "demobch-sql"
    inst.status = "TERMINATED"
    inst.machine_type = "zones/us-central1-a/machineTypes/n2-standard-4"
    inst.creation_timestamp = "2024-07-18T10:00:00.000-07:00"
    inst.disks = [] # Skip disk logic for this test
    inst.network_interfaces = []

    # Mock SKU Details (from screenshot)
    sku_details = [
        {
            "sku_description": "N2 Instance Core running in Santiago",
            "usage_unit": "seconds",
            "usage": 2028301.0
        },
        {
            "sku_description": "Balanced PD Capacity",
            "usage_unit": "byte-seconds",
            "usage": 275000000000.0
        }
    ]

    # Mock vCPU (since we can't call Google API)
    # in the real function, this comes from get_machine_type_details_sync
    # We will manually simulate the variable state inside the function
    # by copying the relevant logic block here or mocking the helper.
    
    vcpu = "4" # It returns a string in production

    # --- LOGIC UNDER TEST (Copied from tools.py) ---
    uptime_str = ""
    # Convert vcpu to simple number
    try:
        vcpu_num = int(float(vcpu))
    except:
        vcpu_num = 0
        
    print(f"DEBUG TEST: vcpu_num parsed as: {vcpu_num}")

    if sku_details and vcpu_num > 0:
        total_usage_seconds = 0.0
        print(f"DEBUG TEST: Processing {len(sku_details)} rows")
        for row in sku_details:
            # Look for "Instance Core" usage
            desc = row.get("sku_description", "").lower()
            usage_unit = row.get("usage_unit", "").lower()
            
            # "instance core" usually covers the vCPU usage
            if "instance core" in desc and "second" in usage_unit:
                try:
                    usage_val = float(row.get("usage", 0))
                    total_usage_seconds += usage_val
                    print(f"DEBUG TEST: Match! {usage_val}")
                except: pass
        
        print(f"DEBUG TEST: Total Seconds: {total_usage_seconds}")
        if total_usage_seconds > 0:
            # Formula: Usage (seconds) / (vCPU * 3600)
            uptime_hours = total_usage_seconds / (vcpu_num * 3600)
            uptime_str = f" | ⏰ **Uptime**: {uptime_hours:.2f}h"
            print(f"DEBUG TEST: Result: {uptime_str}")
    else:
        print("DEBUG TEST: Skipped logic (vcpu=0 or no skus)")
        
    return uptime_str

if __name__ == "__main__":
    asyncio.run(_get_instance_details_string_test())
