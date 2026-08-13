from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))


async def _list_titan_memory_tools(include_schema: bool) -> list[dict[str, Any]]:
    from entrypoints import mcp_server

    tools = await mcp_server.server.list_tools()
    payload = []
    for tool in tools:
        item: dict[str, Any] = {"name": tool.name}
        if include_schema:
            item["input_schema"] = tool.inputSchema
        payload.append(item)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="List tools exported by a local Titan MCP server.")
    parser.add_argument("server", choices=["titan-memory"], help="MCP server to introspect")
    parser.add_argument("--schema", action="store_true", help="Include each tool input schema")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()

    tools = asyncio.run(_list_titan_memory_tools(include_schema=args.schema))
    if args.json:
        print(json.dumps({"server": args.server, "count": len(tools), "tools": tools}, indent=2, sort_keys=True))
        return 0

    print(f"{args.server}: {len(tools)} tools")
    for tool in tools:
        print(tool["name"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
