import asyncio
import tools

async def main():
    print("Testing create_custom_instance (Native)...")
    name = "verification-instance-native"
    try:
        # Create
        print(f"Creating {name}...")
        result = await tools.create_custom_instance(
            name=name,
            machine_type="e2-micro", # Cheaper for test
            image_family="debian-11",
            boot_disk_size="10"
        )
        print(f"Result: {result}")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
