"""ASCII 二维码渲染的单元测试（不联网）。"""
from __future__ import annotations

import unittest

import qrcode

from mcp_115_server.service import P115Service

TOP, BOTTOM, FULL, BLANK = "\u2580", "\u2584", "\u2588", " "
ALLOWED = {TOP, BOTTOM, FULL, BLANK}
URL = "https://115.com/scan/dg-6ae86b118792f0152699613b728dea650afdd40b"


class QrcodeAsciiTests(unittest.TestCase):
    def test_renders_multiline_rectangle(self) -> None:
        art = P115Service._qrcode_ascii(URL)
        lines = art.split("\n")
        self.assertGreater(len(lines), 5)
        self.assertEqual(len({len(line) for line in lines}), 1, "每行宽度必须一致")

    def test_uses_only_block_characters(self) -> None:
        art = P115Service._qrcode_ascii(URL)
        self.assertLessEqual(set(art) - {"\n"}, ALLOWED)

    def test_surrounds_matrix_with_quiet_zone(self) -> None:
        lines = P115Service._qrcode_ascii(URL).split("\n")
        self.assertEqual(lines[0].strip(), "", "顶部应有静默区")
        self.assertEqual(lines[-1].strip(), "", "底部应有静默区")
        for line in lines:
            self.assertTrue(line.startswith(BLANK * 2), "左侧应有静默区")
            self.assertTrue(line.endswith(BLANK * 2), "右侧应有静默区")

    def test_round_trips_back_to_the_qr_matrix(self) -> None:
        """把 ASCII 还原成模块矩阵，必须和 qrcode 自己生成的严格一致。"""
        art = P115Service._qrcode_ascii(URL)
        rebuilt: list[list[bool]] = []
        for line in art.split("\n"):
            top, bottom = [], []
            for ch in line:
                top.append(ch in (TOP, FULL))
                bottom.append(ch in (BOTTOM, FULL))
            rebuilt.append(top)
            rebuilt.append(bottom)

        expected = qrcode.QRCode(border=2)
        expected.add_data(URL)
        expected.make(fit=True)
        matrix = expected.get_matrix()

        # 模块行数为奇数时，本实现会让最后一行只用上半块，多出一个空白行
        self.assertGreaterEqual(len(rebuilt), len(matrix))
        self.assertEqual(rebuilt[: len(matrix)], matrix)
        for extra_row in rebuilt[len(matrix):]:
            self.assertTrue(all(not cell for cell in extra_row))

    def test_empty_input_returns_empty_string(self) -> None:
        self.assertEqual(P115Service._qrcode_ascii(""), "")
        self.assertEqual(P115Service._qrcode_ascii("   "), "")


if __name__ == "__main__":
    unittest.main()
