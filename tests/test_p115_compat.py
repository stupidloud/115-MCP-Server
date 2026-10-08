"""p115client 兼容层的单元测试（不联网）。"""
from __future__ import annotations

import unittest
import weakref
from unittest.mock import patch

from mcp_115_server import p115_compat


class FakeLegacyClient:
    """只实现新版 p115client 方法名。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def clouddownload_task_list(self, payload, method="GET", type="web"):
        self.calls.append(("clouddownload_task_list", (payload,), {"type": type}))
        return {"state": True, "tasks": []}

    def clouddownload_task_del(self, payload, method="POST", type="web"):
        self.calls.append(("clouddownload_task_del", (payload,), {"type": type}))
        return {"state": True}

    def clouddownload_quota_package_array(self):
        self.calls.append(("clouddownload_quota_package_array", (), {}))
        return {"state": True, "data": []}

    def request(self, url, method="GET", payload=None, **kwargs):
        self.calls.append(("request", (url,), {"method": method, "payload": payload}))
        return {"state": True, "url": url}


class ResolveClientMethodTests(unittest.TestCase):
    def test_renamed_methods_are_forwarded(self) -> None:
        client = FakeLegacyClient()
        method = p115_compat.resolve_client_method(client, "offline_list")
        method({"page": 1}, type="web")
        self.assertEqual(client.calls[0][0], "clouddownload_task_list")

        p115_compat.resolve_client_method(client, "offline_remove")({"hash[0]": "x"})
        self.assertEqual(client.calls[1][0], "clouddownload_task_del")

        p115_compat.resolve_client_method(client, "offline_quota_package_array")()
        self.assertEqual(client.calls[2][0], "clouddownload_quota_package_array")

    def test_open_api_names_call_the_open_endpoints(self) -> None:
        client = FakeLegacyClient()
        p115_compat.resolve_client_method(client, "offline_quota_info_open")()
        name, args, kwargs = client.calls[0]
        self.assertEqual(name, "request")
        self.assertEqual(args[0], "https://proapi.115.com/open/offline/get_quota_info")

        p115_compat.resolve_client_method(client, "offline_add_torrent_open")({"torrent_sha1": "s"})
        self.assertEqual(client.calls[1][1][0], "https://proapi.115.com/open/offline/add_task_bt")

        p115_compat.resolve_client_method(client, "offline_list_open")(3)
        self.assertEqual(client.calls[2][1][0], "https://proapi.115.com/open/offline/get_task_list")
        self.assertEqual(client.calls[2][2]["payload"], {"page": 3})

        p115_compat.resolve_client_method(client, "offline_remove_open")({"info_hash": "h"})
        self.assertEqual(client.calls[3][1][0], "https://proapi.115.com/open/offline/del_task")

        p115_compat.resolve_client_method(client, "offline_clear_open")(2)
        self.assertEqual(client.calls[4][2]["payload"], {"flag": 2})

    def test_unknown_name_raises_attribute_error(self) -> None:
        with self.assertRaises(AttributeError):
            p115_compat.resolve_client_method(FakeLegacyClient(), "offline_does_not_exist")

    def test_current_names_pass_through(self) -> None:
        client = FakeLegacyClient()
        p115_compat.resolve_client_method(client, "clouddownload_quota_package_array")()
        self.assertEqual(client.calls[0][0], "clouddownload_quota_package_array")


class CreateClientTests(unittest.TestCase):
    def test_kwargs_not_supported_by_installed_version_are_dropped(self) -> None:
        captured: dict = {}

        class NewStyleClient:
            def __init__(self, cookies=None, app="", app_id=0, console_qrcode=True):
                captured.update(cookies=cookies, app=app, console_qrcode=console_qrcode)

        with patch("p115client.P115Client", NewStyleClient):
            p115_compat.create_client("UID=1", app="web", console_qrcode=False, check_for_relogin=True)

        self.assertEqual(captured["cookies"], "UID=1")
        self.assertEqual(captured["app"], "web")
        self.assertFalse(captured["console_qrcode"])


class CreateFsTests(unittest.TestCase):
    def test_weak_value_caches_are_replaced(self) -> None:
        class FakeFs:
            def __init__(self, client):
                self.client = client
                self.id_to_attr = weakref.WeakValueDictionary()
                self._pid_name_to_attr = weakref.WeakValueDictionary()
                self.other = object()

        with patch("p115client.fs.P115FileSystem", FakeFs):
            fs = p115_compat.create_fs(object())

        self.assertIsInstance(fs.id_to_attr, dict)
        self.assertNotIsInstance(fs.id_to_attr, weakref.WeakValueDictionary)
        self.assertIsInstance(fs._pid_name_to_attr, dict)
        # 换成普通 dict 后，可以存普通 dict 了
        fs.id_to_attr[1] = {"id": 1}
        self.assertEqual(fs.id_to_attr[1]["id"], 1)

    def test_non_weak_caches_are_left_alone(self) -> None:
        sentinel = {}

        class FakeFs:
            def __init__(self, client):
                self.id_to_attr = sentinel
                self._pid_name_to_attr = sentinel

        with patch("p115client.fs.P115FileSystem", FakeFs):
            fs = p115_compat.create_fs(object())

        self.assertIs(fs.id_to_attr, sentinel)


if __name__ == "__main__":
    unittest.main()
