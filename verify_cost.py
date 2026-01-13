import asyncio
import tools

async def main():
    print("Generating report...")
    try:
        report = await tools.get_instance_report()
        print(report)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
