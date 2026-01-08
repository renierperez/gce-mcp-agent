import asyncio
import google.auth
from google.auth.transport.requests import Request
import httpx
import json

async def main():
    credentials, project = google.auth.default(
        scopes=['https://www.googleapis.com/auth/cloud-platform']
    )
    credentials.refresh(Request())
    
    url = "https://compute.googleapis.com/mcp"
    headers = {
        "Authorization": f"Bearer {credentials.token}",
    }

    payload = {
        "jsonrpc": "2.0",
        "method": "tools/list",
        "id": 1
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)
        data = response.json()
        tools = data.get("result", {}).get("tools", [])
        
        for tool in tools:
            if tool.get("name") == "stop_instance":
                with open("schema.json", "w") as f:
                    json.dump(tool.get("inputSchema"), f, indent=2)
                print("Schema saved to schema.json")
                break

if __name__ == "__main__":
    asyncio.run(main())
