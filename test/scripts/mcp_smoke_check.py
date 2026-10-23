#!/usr/bin/env python3
"""Check that a configured stdio MCP server initializes, lists tools, and responds."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def check(args: argparse.Namespace) -> int:
    command = args.command or sys.executable
    if args.arg is not None:
        server_args = args.arg
    elif args.command is None:
        server_args = [str(Path(__file__).resolve().parents[1] / "handy-coder_mcp.py")]
    else:
        server_args = []
    if args.root and "--root" not in server_args:
        server_args.extend(["--root", args.root])

    parameters = StdioServerParameters(
        command=command,
        args=server_args,
        cwd=args.cwd,
    )

    try:
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=args.timeout),
            ) as session:
                initialized = await session.initialize()
                response = await session.list_tools()
                names = [tool.name for tool in response.tools]
                if args.probe_tool not in names:
                    raise RuntimeError(
                        f"Probe tool {args.probe_tool!r} is not registered. "
                        f"Registered tools: {', '.join(names)}"
                    )

                result = await session.call_tool(
                    args.probe_tool,
                    {"paths": [args.probe_file]},
                )
                if result.isError:
                    details = "\n".join(
                        block.text for block in result.content if hasattr(block, "text")
                    )
                    raise RuntimeError(f"Probe call returned an MCP error: {details}")

                print(json.dumps({
                    "status": "ok",
                    "server": initialized.serverInfo.name,
                    "server_version": initialized.serverInfo.version,
                    "tool_count": len(names),
                    "tools": names,
                    "probe": args.probe_tool,
                    "probe_file": args.probe_file,
                }, indent=2))
                return 0
    except Exception as exc:
        print(f"MCP smoke check failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "Check the command, arguments, working directory, Python environment, "
            "and server stderr in the MCP client log.",
            file=sys.stderr,
        )
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", help="Server executable; defaults to this Python interpreter")
    parser.add_argument(
        "--arg",
        action="append",
        help="One server argument; repeat for each argument. Defaults to handy-coder_mcp.py.",
    )
    parser.add_argument("--cwd", help="Server working directory")
    parser.add_argument("--root", help="Workspace root passed as --root unless already provided")
    parser.add_argument("--timeout", type=float, default=10, help="Per-request timeout in seconds")
    parser.add_argument("--probe-tool", default="read_files", help="Harmless tool to call after listing")
    parser.add_argument("--probe-file", default="README.md", help="Workspace-relative file to read")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    return asyncio.run(check(args))


if __name__ == "__main__":
    raise SystemExit(main())
