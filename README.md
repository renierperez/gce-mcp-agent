# GCE MCP Agent

An intelligent agent backend for managing Google Compute Engine (GCE) instances using the Model Context Protocol (MCP).

## Features
- **MCP Integration**: Uses the official Google Compute Engine MCP server (`compute.googleapis.com/mcp`).
- **Commands**:
  - `list`: List all instances in the configured zone.
  - `start`: Start a specific instance.
  - `stop`: Stop a specific instance.
  - `report`: Generate a detailed metadata report (IPs, Region, Hardware Specs, OS).
- **Authentication**: Supports Service Account Impersonation via `gcloud`.

## Usage
```bash
# List instances
python3 main.py list

# Generate Report
python3 main.py report
```

## Requirements
- Python 3.10+
- `gcloud` CLI installed and authenticated
- Google Cloud Project with GCE API enabled
