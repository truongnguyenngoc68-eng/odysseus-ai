"""
browser_server.py

Built-in MCP server exposing Google Chrome automation via CDP (port 9222) and Playwright.
Allows Odysseus agents to open, read, and interact with Facebook, Threads, Instagram,
and any website directly on the user's Google Chrome browser.
"""

import asyncio
import json
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.chrome_bridge import run_chrome_action, fetch_page_with_chrome

server = Server("builtin_browser")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="browser_navigate",
            description="Open or navigate to a URL in Google Chrome and return the rendered page text and links.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full HTTP/HTTPS URL to open in Google Chrome."},
                    "reload": {"type": "boolean", "description": "Force reload if tab is already open."},
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="browser_snapshot",
            description="Read the rendered text and links of the active Google Chrome tab (or a specific open URL).",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Optional URL of an open Chrome tab to inspect."},
                },
            },
        ),
        Tool(
            name="browser_tabs",
            description="List all currently open tabs in Google Chrome.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="browser_click",
            description="Click an element in Google Chrome by CSS or Playwright selector.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Optional tab URL."},
                    "selector": {"type": "string", "description": "CSS or text selector (e.g. 'text=Log in' or 'button')."},
                },
                "required": ["selector"],
            },
        ),
        Tool(
            name="browser_type",
            description="Fill text into an input element in Google Chrome.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Optional tab URL."},
                    "selector": {"type": "string", "description": "Input selector."},
                    "text": {"type": "string", "description": "Text to type."},
                    "submit": {"type": "boolean", "description": "Press Enter after typing."},
                },
                "required": ["selector", "text"],
            },
        ),
    ]


def _format_chrome_result(res: dict) -> str:
    if not isinstance(res, dict) or not res.get("ok"):
        return f"Chrome Error: {res.get('error') if isinstance(res, dict) else 'Unknown error'}"
    if "tabs" in res:
        lines = ["Open Google Chrome Tabs:"]
        for t in res.get("tabs") or []:
            lines.append(f"- [{t.get('index')}] {t.get('title') or '(untitled)'} — {t.get('url')}")
        return "\n".join(lines)
    title = res.get("title") or ""
    url = res.get("url") or ""
    text = (res.get("text") or "").strip()
    links = res.get("links") or []
    out = [f"Title: {title}", f"URL: {url}", "", "### Rendered Content (Google Chrome):", text[:8000]]
    if links:
        out.append("\n### Links:")
        for item in links[:20]:
            out.append(f"- [{item.get('title')}]({item.get('url')})")
    return "\n".join(out)


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    args = arguments or {}
    loop = asyncio.get_running_loop()
    if name == "browser_tabs":
        res = await loop.run_in_executor(None, lambda: run_chrome_action({"action": "tabs"}))
    elif name == "browser_navigate":
        res = await loop.run_in_executor(
            None,
            lambda: fetch_page_with_chrome(
                str(args.get("url") or ""),
            ),
        )
    elif name == "browser_snapshot":
        res = await loop.run_in_executor(
            None,
            lambda: run_chrome_action({
                "action": "snapshot",
                "url": str(args.get("url") or ""),
            }),
        )
    elif name == "browser_click":
        res = await loop.run_in_executor(
            None,
            lambda: run_chrome_action({
                "action": "click",
                "url": str(args.get("url") or ""),
                "selector": str(args.get("selector") or ""),
            }),
        )
    elif name == "browser_type":
        res = await loop.run_in_executor(
            None,
            lambda: run_chrome_action({
                "action": "type",
                "url": str(args.get("url") or ""),
                "selector": str(args.get("selector") or ""),
                "text": str(args.get("text") or ""),
                "submit": bool(args.get("submit", False)),
            }),
        )
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return [TextContent(type="text", text=_format_chrome_result(res))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
