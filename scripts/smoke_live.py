"""用真实 115 账号做一次只读冒烟测试，验证 MCP 后端确实能用。

用法：
    P115_COOKIES_PATH=... uv run python scripts/smoke_live.py
"""
from __future__ import annotations

import os
import sys
import traceback

from mcp_115_server.config import Settings
from mcp_115_server.service import P115Service


def check(name, fn):
    try:
        result = fn()
        preview = str(result)
        print(f"  [OK]   {name}: {preview[:150]}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] {name}: {type(exc).__name__}: {exc}")
        return False


def main() -> int:
    service = P115Service(Settings())
    results = []

    print("== 认证 ==")
    results.append(check("auth_status", lambda: service.auth_status(validate_remote=True)))

    print("== 文件系统 ==")
    results.append(check("get_account_info", service.get_account_info))
    results.append(check("get_storage_info", service.get_storage_info))
    results.append(check("list_directory(root)", lambda: {"count": service.list_directory()["count"]}))
    results.append(check("resolve_directory(/)", lambda: service.resolve_directory(remote_path="/")))

    def _first_dir():
        entries = service.list_directory()["entries"]
        dirs = [e for e in entries if e.get("is_dir")]
        return dirs[0]["name"] if dirs else ""

    first_dir = _first_dir() if results[-1] else ""
    print(f"  (取第一个目录: {first_dir!r})")
    if first_dir:
        results.append(check(
            "list_directory(sub)",
            lambda: {"count": service.list_directory(remote_path="/" + first_dir)["count"]},
        ))
    results.append(check("search_entries", lambda: {"count": service.search_entries("a", limit=3).get("count")}))

    print("== 云下载（只读）==")
    results.append(check("offline_get_quota_info", service.offline_get_quota_info))
    results.append(check("offline_get_sign_info", service.offline_get_sign_info))
    results.append(check("offline_get_task_count", service.offline_get_task_count))
    results.append(check("offline_get_download_paths", service.offline_get_download_paths))
    results.append(check("offline_get_quota_package_array", service.offline_get_quota_package_array))
    results.append(check("offline_list_tasks", lambda: {"count": service.offline_list_tasks(page=1)["count"]}))

    print("== 回收站 / 标签 / 分享（只读）==")
    results.append(check("list_recycle_bin", lambda: {"count": service.list_recycle_bin(limit=3).get("count")}))
    results.append(check("list_labels", service.list_labels))
    results.append(check("list_shares", lambda: {"count": service.list_shares(limit=3).get("count")}))

    print("== 二维码登录（只读，不完成登录）==")
    login_result = {}

    def _start_login():
        result = service.start_qrcode_login(app="web")
        login_result.update(result)
        return {"session_id": result["session_id"], "has_image": bool(result.get("qrcode_image"))}

    results.append(check("start_qrcode_login", _start_login))
    if login_result:
        results.append(check(
            "get_qrcode_login_status",
            lambda: service.get_qrcode_login_status(login_result["session_id"], timeout=2),
        ))

    ok = sum(1 for r in results if r)
    print(f"\n通过 {ok}/{len(results)}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
