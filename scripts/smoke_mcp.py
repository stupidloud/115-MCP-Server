"""通过 stdio 连接真实的 MCP 服务器，端到端验证工具。

用法：
    P115_COOKIES_PATH=... uv run python scripts/smoke_mcp.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

REPO = Path(__file__).resolve().parents[1]


async def main() -> int:
    env = dict(os.environ)
    transport = StdioTransport(
        command=str(REPO / ".venv" / "Scripts" / "115-MCP-Server.exe"),
        args=[],
        env=env,
        cwd=str(REPO),
    )
    failures = 0
    async with Client(transport) as client:
        tools = await client.list_tools()
        print(f"MCP 工具数: {len(tools)}")

        async def call(name: str, args: dict | None = None):
            nonlocal failures
            try:
                result = await client.call_tool(name, args or {})
                print(f"  [OK]   {name}: {str(result.data)[:140]}")
                return result.data
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"  [FAIL] {name}: {type(exc).__name__}: {exc}")
                return None

        print("== 认证 / 登录工具 ==")
        await call("auth_status", {"validate_remote": True})
        login = await call("start_qrcode_login", {"app": "web"})
        if isinstance(login, dict) and login.get("session_id"):
            await call("get_qrcode_login_status", {"session_id": login["session_id"], "timeout": 2})

        print("== 读取 ==")
        await call("list_directory", {"limit": 5} if False else {})
        await call("get_storage_info")
        await call("get_account_info")
        await call("offline_get_quota_info")
        await call("offline_list_tasks", {"page": 1})
        await call("list_recycle_bin", {"limit": 2})

        print("== 写入（建目录 -> 删除）==")
        created = await call("create_directory", {"name": "mcp115_smoke_test"})
        entry_id = created.get("cid") or created.get("file_id") if isinstance(created, dict) else None
        if entry_id:
            await call("remove_entry", {"remote_id": str(entry_id)})
        else:
            print(f"  [WARN] 未能从 create_directory 结果里取到 id: {created}")

    print(f"\n失败数: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
