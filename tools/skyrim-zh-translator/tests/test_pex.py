"""PEX（Papyrus 编译脚本）字符串表测试：解析、候选判定、字节级回写。"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skytrans.pex import (  # noqa: E402
    MAGIC,
    PexError,
    PexFile,
    looks_translatable,
)


def _wstr(text: str) -> bytes:
    raw = text.encode("utf-8")
    return struct.pack(">H", len(raw)) + raw


def build_pex(
    strings,
    source="TestScript.psc",
    user="tester",
    machine="DESKTOP",
    has_debug=True,
    object_name=0,
    parent_index=1,
    docstring_index=2,
    auto_state_index=3,
) -> bytes:
    """构造一个最小但结构完整的 .pex（大端，格式 3.2）。"""
    head = struct.pack(">IBBH", MAGIC, 3, 2, 1) + struct.pack(">Q", 1700000000)
    head += _wstr(source) + _wstr(user) + _wstr(machine)
    body = struct.pack(">H", len(strings)) + b"".join(_wstr(s) for s in strings)

    tail = bytearray()
    if has_debug:
        tail += b"\x01"
        tail += struct.pack(">Q", 1700000123)
        tail += struct.pack(">H", 1)  # 一个调试函数
        tail += struct.pack(">HHHBH", object_name, auto_state_index, 4, 2, 2)
        tail += struct.pack(">HH", 12, 13)  # 两行源码行号
    else:
        tail += b"\x00"
    tail += struct.pack(">H", 0)  # 用户标记数量
    # 单个对象：nameIndex + size + 对象体
    data = struct.pack(">HHIH", parent_index, docstring_index, 0, auto_state_index)
    data += struct.pack(">H", 0)  # 变量数
    data += struct.pack(">H", 0)  # 属性数
    data += struct.pack(">H", 0)  # 状态数
    tail += struct.pack(">H", 1)  # 对象数
    tail += struct.pack(">HI", object_name, len(data) + 4) + data
    return head + body + bytes(tail)


SAMPLE = [
    "TestScript",  # 0 对象名
    "ObjectReference",  # 1 父类
    "",  # 2 空 docstring
    "",  # 3 空 auto 状态
    "GetState",  # 4 函数名
    "Hello traveler, welcome to Whiterun",  # 5 玩家可见
    "Dialogue Menu",  # 6 玩家可见
    "sound\\fx\\door.wav",  # 7 路径
    "_root.HUDMovieBaseInstance.",  # 8 点分标识符
    "$TranslationKey",  # 9 翻译键
]


class LooksTranslatableTests(unittest.TestCase):
    def test_prose_passes(self):
        self.assertTrue(looks_translatable("Hello traveler, welcome to Whiterun")[0])
        self.assertTrue(looks_translatable("Dialogue Menu")[0])
        self.assertTrue(looks_translatable("Wielding melee weapon")[0])

    def test_identifiers_rejected(self):
        for text in ("GetState", "iHUDControl", "_root.HUDMovieBaseInstance.", "sound\\fx\\door.wav"):
            self.assertFalse(looks_translatable(text)[0], text)

    def test_single_word_rejected_in_strict_mode(self):
        self.assertFalse(looks_translatable("Dragonborn-DLC")[0])
        self.assertTrue(looks_translatable("Dragonborn-DLC", strict=False)[0])

    def test_empty_and_placeholder(self):
        for text in ("", "   ", "{}", "2"):
            self.assertFalse(looks_translatable(text)[0], repr(text))


class PexParseTests(unittest.TestCase):
    def test_parse_and_candidates(self):
        pex = PexFile(data=build_pex(SAMPLE))
        self.assertEqual(len(pex.strings), len(SAMPLE))
        self.assertEqual(pex.major, 3)
        self.assertEqual(pex.minor, 2)
        self.assertEqual(pex.game_id, 1)
        self.assertEqual(pex.source_name, b"TestScript.psc")
        self.assertEqual(pex.objects, 1)
        texts = [s.text for s in pex.candidates]
        self.assertEqual(texts, ["Hello traveler, welcome to Whiterun", "Dialogue Menu"])
        # 结构里被当作名称引用的索引不参与翻译
        for index in (0, 1, 2, 3, 4):
            self.assertTrue(pex.strings[index].identifier, index)

    def test_no_debug_info_accepted(self):
        pex = PexFile(data=build_pex(SAMPLE, has_debug=False))
        self.assertEqual(len(pex.candidates), 2)

    def test_bad_magic_rejected(self):
        raw = build_pex(SAMPLE)
        with self.assertRaises(PexError):
            PexFile(data=b"BOGUS!" + raw[6:])

    def test_truncated_rejected(self):
        raw = build_pex(SAMPLE)
        with self.assertRaises(PexError):
            PexFile(data=raw[:20])

    def test_load_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "TestScript.pex"
            path.write_bytes(build_pex(SAMPLE))
            pex = PexFile(path=path)
            self.assertEqual(pex.path, path)
            self.assertIn("TestScript.pex", pex.label)


class PexRewriteTests(unittest.TestCase):
    def test_identity_rebuild(self):
        raw = build_pex(SAMPLE)
        pex = PexFile(data=raw)
        self.assertEqual(pex.to_bytes(), raw)
        self.assertFalse(pex.dirty)

    def test_set_one_string(self):
        raw = build_pex(SAMPLE)
        pex = PexFile(data=raw)
        pex.set(5, "旅行者你好，欢迎来到雪漫城")
        self.assertTrue(pex.dirty)
        rebuilt = pex.to_bytes()
        check = PexFile(data=rebuilt)
        self.assertEqual(len(check.strings), len(SAMPLE))
        self.assertEqual(check.strings[5].text, "旅行者你好，欢迎来到雪漫城")
        self.assertEqual(check.strings[6].raw, SAMPLE[6].encode())
        # 表之后的所有结构原样保留
        self.assertEqual(check._tail, pex._tail)
        pex.verify()

    def test_change_length_keeps_structure(self):
        pex = PexFile(data=build_pex(SAMPLE))
        before = len(pex.to_bytes())
        pex.set(5, "短")
        after = pex.to_bytes()
        self.assertLess(len(after), before)
        check = PexFile(data=after)
        self.assertEqual(check.objects, 1)
        self.assertEqual(check._tail, pex._tail)

    def test_oversized_string_rejected(self):
        pex = PexFile(data=build_pex(SAMPLE))
        with self.assertRaises(PexError):
            pex.set(5, "字" * 30000)

    def test_save_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "Scripts" / "TestScript.pex"
            pex = PexFile(data=build_pex(SAMPLE))
            pex.set(6, "对话菜单")
            pex.save(target)
            self.assertTrue(target.exists())
            again = PexFile(path=target)
            self.assertEqual(again.strings[6].text, "对话菜单")
            self.assertEqual(again._tail, pex._tail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
