"""账号密码登录相关测试（不联网）。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from fastmcp.exceptions import ToolError

from mcp_115_server import p115_login
from mcp_115_server.config import Settings
from mcp_115_server.service import P115Service, _cookies_to_str, _image_data_uri

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 16

SUCCESS = {"state": True, "data": {"cookie": {"UID": "1_A1_2", "CID": "c", "SEID": "s", "KID": "k"}}}
FAILED = {"state": False, "error": "用户名或密码错误", "errno": 40101045}


def fake_client_fallback(self, operation, callback, **kwargs):
    """_with_client_fallback 的替身：trust 走 callback，其余（激活 cookies）直接成功。"""
    if operation == "trust_login_device":
        return callback(None, None)
    return True


class FakeCaptchaClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def captcha_sign(self):
        self.calls.append("sign")
        return {"state": True, "sign": "SIGN123"}

    def captcha_code(self):
        self.calls.append("code")
        return PNG

    def captcha_all(self):
        self.calls.append("all")
        return JPEG


class HelpersTests(unittest.TestCase):
    def test_cookies_to_str_accepts_mapping_and_string(self) -> None:
        self.assertEqual(
            _cookies_to_str({"UID": "u", "CID": "c"}),
            "UID=u; CID=c",
        )
        self.assertEqual(_cookies_to_str("UID=u; CID=c"), "UID=u; CID=c")

    def test_image_data_uri_sniffs_mime(self) -> None:
        self.assertTrue(_image_data_uri(PNG).startswith("data:image/png;base64,"))
        self.assertTrue(_image_data_uri(JPEG).startswith("data:image/jpeg;base64,"))
        self.assertEqual(_image_data_uri(b""), "")
        self.assertEqual(_image_data_uri(None), "")


class P115LoginHelpersTests(unittest.TestCase):
    def test_normalize_app_matches_p115client(self) -> None:
        self.assertEqual(p115_login.normalize_app("desktop"), "web")
        self.assertEqual(p115_login.normalize_app("windows"), "os_windows")
        self.assertEqual(p115_login.normalize_app("mac"), "os_mac")
        self.assertEqual(p115_login.normalize_app("linux"), "os_linux")
        self.assertEqual(p115_login.normalize_app("android"), "android")

    def test_needs_captcha_detects_hints(self) -> None:
        self.assertTrue(p115_login.needs_captcha({"error": "请输入验证码"}))
        self.assertTrue(p115_login.needs_captcha({"message": "短信验证次数超过限制"}))
        self.assertFalse(p115_login.needs_captcha({"error": "用户名或密码错误"}))
        self.assertFalse(p115_login.needs_captcha({}))


class LoginCaptchaTests(unittest.TestCase):
    def test_returns_code_id_and_images(self) -> None:
        service = P115Service(Settings())
        fake = FakeCaptchaClient()
        with patch.object(P115Service, "_captcha_client", return_value=fake):
            result = service.get_login_captcha()

        self.assertEqual(result["code_id"], "SIGN123")
        self.assertTrue(result["target_image"].startswith("data:image/png;base64,"))
        self.assertTrue(result["pool_image"].startswith("data:image/jpeg;base64,"))
        self.assertIn("code", result["how_to"])
        self.assertEqual(fake.calls, ["sign", "code", "all"])


class LoginWithPasswordTests(unittest.TestCase):
    def make_service(self) -> P115Service:
        return P115Service(Settings(P115_COOKIES="UID=old; CID=old; SEID=old; KID=old"))

    def test_success_saves_cookies(self) -> None:
        service = self.make_service()
        with tempfile.TemporaryDirectory() as temp_dir:
            cookies_path = Path(temp_dir) / "115-cookies.txt"
            service.settings.p115_cookies_path = str(cookies_path)
            with patch("mcp_115_server.service.p115_login.submit_password", return_value=SUCCESS) as submit, \
                 patch("mcp_115_server.service.p115_login.trust_device", return_value={"state": True}) as trust, \
                 patch.object(P115Service, "_with_client_fallback", fake_client_fallback):
                result = service.login_with_password("13800138000", "secret")

            self.assertEqual(result["stage"], "done")
            self.assertTrue(result["logged_in"])
            self.assertEqual(result["saved_to"], str(cookies_path))
            self.assertEqual(
                cookies_path.read_text(encoding="utf-8"),
                "UID=1_A1_2; CID=c; SEID=s; KID=k",
            )
            self.assertEqual(submit.call_args.kwargs["account"], "13800138000")
            self.assertEqual(submit.call_args.kwargs["password"], "secret")
            # 登录成功后自动信任这台设备
            self.assertEqual(trust.call_args.args[1], p115_login.derive_device_id("13800138000"))
            self.assertTrue(result["trusted_device"]["ok"])

    def test_incomplete_logins_do_not_trust_anything(self) -> None:
        service = self.make_service()
        two_step = {"state": False, "error": "已开启两步验证登录！", "errno": 40101010, "data": {"user_id": 42}}
        for response in (two_step, {"state": False, "error": "请输入验证码", "errno": 40101004}, FAILED):
            with self.subTest(errno=response["errno"]), \
                 patch("mcp_115_server.service.p115_login.submit_password", return_value=response), \
                 patch("mcp_115_server.service.p115_login.send_login_sms", return_value={"state": True}), \
                 patch("mcp_115_server.service.p115_login.trust_device") as trust:
                result = service.login_with_password("a@b.com", "secret")
            self.assertNotEqual(result["stage"], "done")
            trust.assert_not_called()

    def test_trust_failure_does_not_break_the_login(self) -> None:
        service = self.make_service()
        with tempfile.TemporaryDirectory() as temp_dir:
            service.settings.p115_cookies_path = str(Path(temp_dir) / "115-cookies.txt")
            with patch("mcp_115_server.service.p115_login.submit_password", return_value=SUCCESS), \
                 patch("mcp_115_server.service.p115_login.trust_device", side_effect=ToolError("信任接口挂了")), \
                 patch.object(P115Service, "_with_client_fallback", fake_client_fallback):
                result = service.login_with_password("a@b.com", "secret")

        self.assertEqual(result["stage"], "done")
        self.assertEqual(result["trusted_device"]["ok"], False)
        self.assertIn("信任接口挂了", result["trusted_device"]["error"])

    def test_captcha_stage_is_reported(self) -> None:
        service = self.make_service()
        response = {"state": False, "error": "请输入验证码", "errno": 40101004}
        with patch("mcp_115_server.service.p115_login.submit_password", return_value=response):
            result = service.login_with_password("a@b.com", "secret")
        self.assertEqual(result["stage"], "captcha")
        self.assertEqual(result["errno"], 40101004)
        self.assertIn("get_login_captcha", result["hint"])

    def test_two_step_stage_sends_sms(self) -> None:
        service = self.make_service()
        response = {"state": False, "error": "已开启两步验证登录！", "errno": 40101010, "data": {"user_id": 42}}
        with patch("mcp_115_server.service.p115_login.submit_password", return_value=response), \
             patch("mcp_115_server.service.p115_login.send_login_sms", return_value={"state": True}) as send:
            result = service.login_with_password("a@b.com", "secret")

        self.assertEqual(result["stage"], "sms")
        self.assertEqual(result["user_id"], 42)
        self.assertTrue(result["sms_sent"])
        self.assertEqual(send.call_args.args[0], 42)

    def test_wrong_password_stage(self) -> None:
        service = self.make_service()
        response = {"state": False, "error": "用户名或密码错误", "errno": 40101045}
        with patch("mcp_115_server.service.p115_login.submit_password", return_value=response):
            result = service.login_with_password("a@b.com", "secret")
        self.assertEqual(result["stage"], "error")
        self.assertEqual(result["errno"], 40101045)

    def test_password_is_never_written_to_debug_log(self) -> None:
        service = self.make_service()
        service.settings.p115_debug_logging = True
        logged: list[tuple] = []

        def capture(event, **fields):
            logged.append((event, fields))

        with patch.object(P115Service, "_debug_log", staticmethod(capture)), \
             patch("mcp_115_server.service.p115_login.submit_password", return_value=SUCCESS), \
             patch("mcp_115_server.service.p115_login.trust_device", return_value={"state": True}), \
             patch.object(P115Service, "_with_client_fallback", fake_client_fallback):
            service.login_with_password("a@b.com", "SUPER-SECRET")

        self.assertNotIn("SUPER-SECRET", repr(logged))

    def test_blank_inputs_are_rejected(self) -> None:
        service = self.make_service()
        with self.assertRaises(ToolError):
            service.login_with_password("  ", "x")
        with self.assertRaises(ToolError):
            service.login_with_password("a@b.com", "")

    def test_login_is_a_device_login_not_web(self) -> None:
        service = self.make_service()
        with patch("mcp_115_server.service.p115_login.submit_password", return_value=FAILED) as submit:
            service.login_with_password("a@b.com", "secret")
        self.assertEqual(submit.call_args.kwargs["app"], "android")
        self.assertEqual(submit.call_args.kwargs["device_id"], p115_login.derive_device_id("a@b.com"))

    def test_web_login_is_rejected(self) -> None:
        service = self.make_service()
        for app in ("web", "desktop"):
            with self.subTest(app=app), self.assertRaises(ToolError) as ctx:
                service.login_with_password("a@b.com", "secret", app=app)
            self.assertIn("设备", str(ctx.exception))


class DeviceIdDerivationTests(unittest.TestCase):
    """device_id 由账号名按固定算法推出：同一账号永远是同一台设备。"""

    def test_same_account_always_yields_the_same_device(self) -> None:
        first = p115_login.derive_device_id("13306060199")
        UUID(first)                                     # 形状就是 115 安卓端的 UUID
        self.assertEqual(first, p115_login.derive_device_id("13306060199"))
        self.assertEqual(
            p115_login.derive_device_id("kleverx"),
            p115_login.derive_device_id("  KLEVERX  "),   # 大小写与空白不敏感
        )

    def test_different_accounts_get_different_devices(self) -> None:
        self.assertNotEqual(
            p115_login.derive_device_id("13800138000"),
            p115_login.derive_device_id("13900139000"),
        )

    def test_login_uses_the_derived_device_id(self) -> None:
        service = P115Service(Settings(P115_COOKIES="x"))
        with patch("mcp_115_server.service.p115_login.submit_password", return_value=FAILED) as submit:
            service.login_with_password("a@b.com", "secret")
        self.assertEqual(
            submit.call_args.kwargs["device_id"],
            p115_login.derive_device_id("a@b.com"),
        )

    def test_caller_supplied_device_id_is_used_verbatim(self) -> None:
        service = P115Service(Settings(P115_COOKIES="x"))
        with patch("mcp_115_server.service.p115_login.submit_password", return_value=FAILED) as submit:
            service.login_with_password("a@b.com", "secret", device_id="explicit-one")
        self.assertEqual(submit.call_args.kwargs["device_id"], "explicit-one")


class DeviceManagementIsNotExposedTests(unittest.TestCase):
    """设备/信任列表的查询与维护能力都不在本服务里（见 README 说明）。"""

    def test_service_exposes_no_device_management(self) -> None:
        for name in (
            "list_login_devices",
            "list_trust_devices",
            "trust_login_device",
            "untrust_login_device",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(P115Service, name))


class SubmitLoginSmsTests(unittest.TestCase):
    def test_success_saves_cookies(self) -> None:
        service = P115Service(Settings(P115_COOKIES="UID=old; CID=old; SEID=old; KID=old"))
        with tempfile.TemporaryDirectory() as temp_dir:
            cookies_path = Path(temp_dir) / "115-cookies.txt"
            service.settings.p115_cookies_path = str(cookies_path)
            with patch("mcp_115_server.service.p115_login.submit_login_sms_code", return_value=SUCCESS) as submit, \
                 patch("mcp_115_server.service.p115_login.trust_device", return_value={"state": True}) as trust, \
                 patch.object(P115Service, "_with_client_fallback", fake_client_fallback):
                result = service.submit_login_sms("a@b.com", "123456")

            self.assertEqual(result["stage"], "done")
            self.assertTrue(cookies_path.exists())
            self.assertEqual(submit.call_args.kwargs["code"], "123456")
            # 走完两步验证后同样要信任这台设备
            self.assertEqual(trust.call_args.args[1], p115_login.derive_device_id("a@b.com"))
            self.assertTrue(result["trusted_device"]["ok"])

    def test_failure_is_reported(self) -> None:
        service = P115Service(Settings(P115_COOKIES="UID=old; CID=old; SEID=old; KID=old"))
        response = {"state": False, "error": "短信验证码错误", "errno": 40103003}
        with patch("mcp_115_server.service.p115_login.submit_login_sms_code", return_value=response):
            result = service.submit_login_sms("a@b.com", "000000")
        self.assertEqual(result["stage"], "error")
        self.assertEqual(result["errno"], 40103003)


if __name__ == "__main__":
    unittest.main()
