"""p115client 兼容层。

本仓库最初是照着并不存在的 p115client 接口写的：``offline_*`` / ``offline_*_open``
方法名和 ``P115Client(check_for_relogin=...)`` 参数在任何已发布版本里都不存在
（PyPI 上最早的 p115client 是 0.0.9，方法名是 ``clouddownload_*``）。

本模块把这些历史名字映射到真实的 p115client 0.0.9.x 接口：

* 名字只变了的，直接转发（例如 ``offline_list`` -> ``clouddownload_task_list``）；
* p115client 没有等价方法的，直接调用官方 HTTP 接口
  （这类都是原来的 ``*_open`` 名字，对应开放平台的 ``/open/offline/*``）。
"""
from __future__ import annotations

import inspect
import weakref
from typing import Any, Callable

PROAPI = "https://proapi.115.com"


# --------------------------------------------------------------------------
# 客户端构造
# --------------------------------------------------------------------------
def create_client(cookies: Any = None, *, app: str = "", console_qrcode: bool = False, **extra: Any) -> Any:
    """按当前安装的 p115client 版本构造 P115Client。

    不同版本接受的参数不同（例如 ``check_for_relogin`` 在 0.0.9 里已被删除），
    这里按运行时签名过滤，避免 TypeError。
    """
    from p115client import P115Client

    params = inspect.signature(P115Client.__init__).parameters
    kwargs: dict[str, Any] = {}
    if "app" in params:
        kwargs["app"] = app or ""
    if "console_qrcode" in params:
        kwargs["console_qrcode"] = console_qrcode
    kwargs.update({k: v for k, v in extra.items() if k in params})
    return P115Client(cookies, **kwargs)


def create_fs(client: Any) -> Any:
    """构造 P115FileSystem，并绕开 p115client 0.0.9.7 的一个回归。

    0.0.9.7 把 ``id_to_attr`` / ``_pid_name_to_attr`` 改成了
    ``WeakValueDictionary``，但往里存的却是普通 ``dict``；``dict`` 不支持弱引用，
    于是「按 id 查元数据」会抛
    ``TypeError: cannot create weak reference to 'dict' object``。

    这里把这两个缓存换回普通 ``dict``。它们只是缓存，改成强引用没有副作用。
    """
    from p115client.fs import P115FileSystem

    fs = P115FileSystem(client)
    for attr in ("id_to_attr", "_pid_name_to_attr"):
        cache = getattr(fs, attr, None)
        if isinstance(cache, weakref.WeakValueDictionary):
            setattr(fs, attr, dict(cache))
    return fs


# --------------------------------------------------------------------------
# 开放平台（/open/offline/*）直连接口
# --------------------------------------------------------------------------
def _open_get(client: Any, path: str, payload: Any = None) -> Any:
    return client.request(f"{PROAPI}{path}", method="GET", payload=payload)


def _open_post(client: Any, path: str, payload: Any) -> Any:
    return client.request(f"{PROAPI}{path}", method="POST", payload=payload)


def _open_quota_info(client: Any, *args: Any, **kwargs: Any) -> Any:
    return _open_get(client, "/open/offline/get_quota_info")


def _open_add_task_bt(client: Any, payload: dict, *args: Any, **kwargs: Any) -> Any:
    return _open_post(client, "/open/offline/add_task_bt", payload)


def _open_add_task_urls(client: Any, payload: Any, *args: Any, **kwargs: Any) -> Any:
    return _open_post(client, "/open/offline/add_task_urls", payload)


def _open_clear_task(client: Any, payload: Any = 0, *args: Any, **kwargs: Any) -> Any:
    if isinstance(payload, int):
        payload = {"flag": payload}
    return _open_post(client, "/open/offline/clear_task", payload)


def _open_del_task(client: Any, payload: Any, *args: Any, **kwargs: Any) -> Any:
    if isinstance(payload, str):
        payload = {"info_hash": payload}
    return _open_post(client, "/open/offline/del_task", payload)


def _open_get_task_list(client: Any, payload: Any = 1, *args: Any, **kwargs: Any) -> Any:
    if isinstance(payload, int):
        payload = {"page": payload}
    return _open_get(client, "/open/offline/get_task_list", payload)


def _open_torrent(client: Any, payload: Any, *args: Any, **kwargs: Any) -> Any:
    if isinstance(payload, str):
        payload = {"sha1": payload}
    return _open_post(client, "/open/offline/torrent", payload)


# --------------------------------------------------------------------------
# 旧名字 -> 新接口
# --------------------------------------------------------------------------
LEGACY_CLIENT_ALIASES: dict[str, str] = {
    # 只是改了名字，签名兼容，直接转发
    "offline_list": "clouddownload_task_list",
    "offline_remove": "clouddownload_task_del",
    "offline_add_urls": "clouddownload_task_add_urls",
    "offline_quota_package_array": "clouddownload_quota_package_array",
    "offline_quota_package_info": "clouddownload_quota_package_info",
    "offline_sign": "clouddownload_sign",
    "offline_task_count": "clouddownload_task_cnt",
    "offline_download_path": "clouddownload_downpath",
    "offline_download_path_set": "clouddownload_downpath_set",
    "offline_restart": "clouddownload_task_restart",
}

LEGACY_CLIENT_ADAPTERS: dict[str, Callable[..., Any]] = {
    # 开放平台接口，p115client 的新版本只保留了 web/ssp 版本，这里直连 HTTP
    "offline_quota_info_open": _open_quota_info,
    "offline_add_torrent_open": _open_add_task_bt,
    "offline_add_urls_open": _open_add_task_urls,
    "offline_clear_open": _open_clear_task,
    "offline_remove_open": _open_del_task,
    "offline_list_open": _open_get_task_list,
    "offline_torrent_info_open": _open_torrent,
}


def resolve_client_method(client: Any, name: str) -> Callable[..., Any]:
    """返回 ``name`` 对应的可调用对象，自动处理历史名字。"""
    adapter = LEGACY_CLIENT_ADAPTERS.get(name)
    if adapter is not None:
        return lambda *args, **kwargs: adapter(client, *args, **kwargs)

    target = LEGACY_CLIENT_ALIASES.get(name, name)
    method = getattr(client, target, None)
    if method is None:
        raise AttributeError(
            f"{type(client).__name__} 上不存在 {target!r}（来自历史名字 {name!r}）；"
            "当前 p115client 版本可能已再次改名"
        )
    return method


def resolve_fs_method(fs: Any, name: str) -> Callable[..., Any]:
    """P115FileSystem 目前名字没有变化，保留一个统一入口以便将来兜底。"""
    method = getattr(fs, name, None)
    if method is None:
        raise AttributeError(f"{type(fs).__name__} 上不存在 {name!r}")
    return method
