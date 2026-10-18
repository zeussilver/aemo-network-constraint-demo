"""Entry point for the AEMO NEMWeb MCP server.

Kept at the repository root so MCP clients can launch the server with an absolute
path from any working directory:

    python /path/to/aemo-network-constraint-demo/run_mcp_server.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.aemo_constraint_demo.mcp_server import main  # noqa: E402 - needs the path above

if __name__ == "__main__":
    main()
