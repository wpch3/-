"""单元测试：python -m unittest discover -s tests -v"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skytrans import esp  # noqa: E402
from skytrans.cache import Cache  # noqa: E402
from skytrans.mcm import McmFile  # noqa: E402
from skytrans.pipeline import Options, Translator  # noqa: E402
from skytrans.providers import MockProvider  # noqa: E402
from skytrans.scan import scan_pack  # noqa: E402
from skytrans.strings import StringTable  # noqa: E402
from skytrans.textfilter import Protector, is_translatable, split_text  # noqa: E402


# ----------------------------------------------------------------------
# 构造合成插件
# ----------------------------------------------------------------------
def sub(sig: bytes, data: bytes) -> bytes:
    if len(data) > 0xFFFF:
        return (
            b"XXXX"
            + struct.pack("<H", 4)
            + struct.pack("<I", len(data))
            + sig
            + b"\x00\x00"
            + data
        )
    return sig + struct.pack("<H", len(data)) + data


def zs(text: str) -> bytes:
    return text.encode("utf-8") + b"\x00"


def record(sig: bytes, formid: int, subs, flags: int = 0, compress: bool = False) -> bytes:
    body = b"".join(subs)
    if compress:
        body = struct.pack("<I", len(body)) + zlib.compress(body, 9)
        flags |= 0x00040000
    return (
        sig
        + struct.pack("<I", len(body))
        + struct.pack("<I", flags)
        + struct.pack("<I", formid)
        + b"\x00" * 8
        + body
    )


def group(label: bytes, children: bytes, gtype: int = 0) -> bytes:
    body = children
    return (
        b"GRUP"
        + struct.pack("<I", 24 + len(body))
        + label
        + struct.pack("<I", gtype)
        + b"\x00" * 8
        + body
    )


LONG_TEXT = ("This is a long book text. " * 3000).strip()  # > 65535 字节


def make_plugin(localized: bool = False) -> bytes:
    flags = 0x00000080 if localized else 0
    header = record(
        b"TES4",
        0,
        [
            sub(b"HEDR", b"\x00" * 12),
            sub(b"MAST", zs("Skyrim.esm")),
            sub(b"CNAM", zs("Test Author")),
        ],
        flags=flags,
    )

    weapons = (
        record(
            b"WEAP",
            0x01000800,
            [
                sub(b"EDID", zs("IronSword")),
                sub(b"FULL", zs("Iron Sword")),
                sub(b"MODL", zs("weapons\\iron\\sword.nif")),
            ],
        )
        + record(
            b"WEAP",
            0x01000801,
            [
                sub(b"EDID", zs("SteelSword")),
                sub(b"FULL", zs("Steel Sword")),
                sub(b"MODL", zs("weapons\\steel\\sword.nif")),
            ],
            compress=True,
        )
    )
    books = record(
        b"BOOK",
        0x01000802,
        [
            sub(b"EDID", zs("TestBook")),
            sub(b"FULL", zs("A Test Book")),
            sub(b"DESC", zs(LONG_TEXT)),
        ],
    )
    messages = record(
        b"MESG",
        0x01000803,
        [
            sub(b"EDID", zs("TestMsg")),
            sub(b"DESC", zs("Are you sure?")),
            sub(b"ITXT", zs("Yes")),
        ],
    )
    return (
        header
        + group(b"WEAP", weapons)
        + group(b"BOOK", books)
        + group(b"MESG", messages)
    )


class EspTests(unittest.TestCase):
    def test_roundtrip_is_byte_identical(self):
        data = make_plugin()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Test.esp"
            path.write_bytes(data)
            plugin = esp.Plugin.load(path)
            self.assertEqual(plugin.serialize(), data)
            self.assertFalse(plugin.localized)

    def test_localized_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Loc.esm"
            path.write_bytes(make_plugin(localized=True))
            plugin = esp.Plugin.load(path)
            self.assertTrue(plugin.localized)

    def test_edit_inline_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Test.esp"
            path.write_bytes(make_plugin())
            plugin = esp.Plugin.load(path)
            names = []
            for rec in plugin.iter_records():
                if rec.sig == b"WEAP" and rec.formid == 0x01000800:
                    rec.get(b"FULL").set_text("铁剑")
                if rec.sig == b"BOOK":
                    rec.get(b"DESC").set_text("这是一本测试书。")
                names.append(rec.sig)
            self.assertTrue(plugin.dirty)

            out = Path(tmp) / "Out.esp"
            plugin.save(out)

            again = esp.Plugin.load(out)
            found = {}
            for rec in again.iter_records():
                if rec.sig == b"WEAP":
                    found[rec.formid] = (rec.get(b"EDID").text, rec.get(b"FULL").text,
                                         rec.get(b"MODL").text)
                if rec.sig == b"BOOK":
                    found["book"] = rec.get(b"DESC").text
            self.assertEqual(found[0x01000800][1], "铁剑")
            self.assertEqual(found[0x01000800][0], "IronSword")
            self.assertEqual(found[0x01000800][2], "weapons\\iron\\sword.nif")
            # 压缩记录未修改，仍然完好
            self.assertEqual(found[0x01000801][1], "Steel Sword")
            self.assertEqual(found["book"], "这是一本测试书。")

    def test_long_subrecord_uses_xxxx(self):
        text = "长" * 40000  # 120000 字节 > 65535
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Test.esp"
            path.write_bytes(make_plugin())
            plugin = esp.Plugin.load(path)
            for rec in plugin.iter_records():
                if rec.sig == b"BOOK":
                    rec.get(b"DESC").set_text(text)
            out = Path(tmp) / "Out.esp"
            plugin.save(out)

            self.assertIn(b"XXXX", out.read_bytes())
            again = esp.Plugin.load(out)
            for rec in again.iter_records():
                if rec.sig == b"BOOK":
                    self.assertEqual(rec.get(b"DESC").text, text)

    def test_compressed_record_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Test.esp"
            path.write_bytes(make_plugin())
            plugin = esp.Plugin.load(path)
            for rec in plugin.iter_records():
                if rec.formid == 0x01000801:
                    rec.get(b"FULL").set_text("钢剑")
            out = Path(tmp) / "Out.esp"
            plugin.save(out)
            again = esp.Plugin.load(out)
            for rec in again.iter_records():
                if rec.formid == 0x01000801:
                    self.assertEqual(rec.get(b"FULL").text, "钢剑")
                    self.assertEqual(rec.get(b"EDID").text, "SteelSword")
                    self.assertTrue(rec.flags & 0x00040000)


class StringsTests(unittest.TestCase):
    def test_strings_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Mod_english.STRINGS"
            table = StringTable(path=path, kind="strings",
                                entries=[b"Iron Sword", b"Steel Sword", b""])
            path.write_bytes(table.serialize())
            loaded = StringTable.load(path)
            self.assertEqual(loaded.entries, table.entries)
            self.assertEqual(loaded.text(0), "Iron Sword")

    def test_dlstrings_roundtrip_and_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Mod_english.DLSTRINGS"
            table = StringTable(path=path, kind="dlstrings",
                                entries=[b"A long description.", b""])
            path.write_bytes(table.serialize())
            loaded = StringTable.load(path)
            self.assertEqual(loaded.entries, table.entries)
            loaded.set_text(0, "一段很长的描述。")
            path.write_bytes(loaded.serialize())
            again = StringTable.load(path)
            self.assertEqual(again.text(0), "一段很长的描述。")
            self.assertEqual(len(again.entries), 2)

    def test_ids_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Mod_english.STRINGS"
            table = StringTable(path=path, kind="strings",
                                entries=[b"one", b"two", b"three"])
            path.write_bytes(table.serialize())
            loaded = StringTable.load(path)
            loaded.set_text(1, "二")
            path.write_bytes(loaded.serialize())
            again = StringTable.load(path)
            self.assertEqual([again.text(i) for i in range(3)], ["one", "二", "three"])


class TextTests(unittest.TestCase):
    def test_filters(self):
        self.assertTrue(is_translatable("Iron Sword"))
        self.assertTrue(is_translatable("A long description of the blade."))
        self.assertFalse(is_translatable("weapons\\iron\\sword.nif"))
        self.assertFalse(is_translatable("IronSword_ID"))
        self.assertFalse(is_translatable("铁剑"))
        self.assertFalse(is_translatable("12"))
        self.assertFalse(is_translatable("0x1F"))

    def test_protector(self):
        p = Protector()
        masked = p.protect("Hello %s, you have <font color='red'>%d</font> gold. [PLYR]")
        self.assertNotIn("%s", masked)
        self.assertNotIn("<font", masked)
        self.assertIn("\u27e60\u27e7", masked)
        restored, missing, soft = p.restore(masked)
        self.assertFalse(missing)
        self.assertFalse(soft)
        self.assertEqual(restored, "Hello %s, you have <font color='red'>%d</font> gold. [PLYR]")

    def test_missing_placeholder_detected(self):
        p = Protector()
        masked = p.protect("Hello %s")
        restored, missing, soft = p.restore("你好，旅行者")
        self.assertEqual(missing, [0])

    def test_split_text(self):
        text = "First sentence. " * 300
        pieces = split_text(text, 200)
        self.assertGreater(len(pieces), 1)
        self.assertEqual("".join(p + s for p, s in pieces), text)


# ----------------------------------------------------------------------
# 端到端
# ----------------------------------------------------------------------
class PipelineTests(unittest.TestCase):
    def _build_pack(self, root: Path) -> None:
        (root / "ModA.esp").write_bytes(make_plugin())
        (root / "Strings").mkdir(parents=True)
        (root / "ModB.esp").write_bytes(make_plugin(localized=True))
        (root / "Strings" / "ModB_english.STRINGS").write_bytes(
            StringTable(path=Path("x"), kind="strings",
                        entries=[b"Iron Sword", b"Steel Sword", b"Health"]).serialize()
        )
        (root / "Strings" / "ModB_english.DLSTRINGS").write_bytes(
            StringTable(path=Path("x"), kind="dlstrings",
                        entries=[b"A long description."]).serialize()
        )
        trans = root / "Interface" / "Translations"
        trans.mkdir(parents=True)
        (trans / "ModB_english.txt").write_text(
            "$ModName\tMy Test Mod\n$Toggle\tEnable feature\n", encoding="utf-8"
        )

    def test_end_to_end_mock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "pack"
            root.mkdir()
            self._build_pack(root)

            pack = scan_pack(root, use_bsa=False, log=lambda *a: None)
            self.assertEqual(len(pack.plugins), 2)

            out = Path(tmp) / "out"
            opts = Options(out=out, use_bsa=False)
            cache = Cache(Path(tmp) / "cache.sqlite", "mock")
            translator = Translator(
                pack, opts, cache=cache, provider=MockProvider(), log=lambda *a: None
            )
            plan = translator.build_plan()
            self.assertGreater(len(plan.units), 8)

            translator.translate(plan)
            written = translator.write(plan)
            self.assertEqual(written["tables"], 2)
            self.assertEqual(written["mcm"], 1)
            self.assertEqual(written["plugins"], 1)

            # 内嵌字符串插件：中文已写入，路径与 EDID 未动
            out_plugin = esp.Plugin.load(out / "ModA.esp")
            texts = {}
            for rec in out_plugin.iter_records():
                if rec.sig == b"WEAP":
                    texts[rec.formid] = (rec.get(b"EDID").text, rec.get(b"FULL").text,
                                         rec.get(b"MODL").text)
            self.assertEqual(texts[0x01000800][1], "【译】Iron Sword")
            self.assertEqual(texts[0x01000800][0], "IronSword")
            self.assertEqual(texts[0x01000801][1], "【译】Steel Sword")
            self.assertTrue(texts[0x01000800][2].endswith(".nif"))

            # 字符串表：ID 顺序与数量不变
            table = StringTable.load(out / "Strings" / "ModB_english.STRINGS")
            self.assertEqual(len(table.entries), 3)
            self.assertEqual(table.text(0), "【译】Iron Sword")
            self.assertEqual(table.text(2), "【译】Health")

            dl = StringTable.load(out / "Strings" / "ModB_english.DLSTRINGS")
            self.assertEqual(dl.text(0), "【译】A long description.")

            # MCM
            mcm = McmFile.load(out / "Interface" / "Translations" / "ModB_english.txt")
            self.assertTrue(mcm.text(0).startswith("【译】"))
            self.assertEqual(len(mcm.keys), 2)

            # 第二次运行应命中缓存，不产生新请求
            calls = translator.provider.calls
            translator2 = Translator(pack, opts, cache=cache, provider=MockProvider(),
                                     log=lambda *a: None)
            plan2 = translator2.build_plan()
            translator2.translate(plan2)
            self.assertEqual(translator2.provider.calls, 0)
            self.assertGreater(calls, 0)

    def test_already_chinese_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "pack"
            root.mkdir()
            (root / "Strings").mkdir()
            (root / "ModB.esp").write_bytes(make_plugin(localized=True))
            (root / "Strings" / "ModB_english.STRINGS").write_bytes(
                StringTable(path=Path("x"), kind="strings", entries=["铁剑".encode()]).serialize()
            )
            pack = scan_pack(root, use_bsa=False, log=lambda *a: None)
            translator = Translator(pack, Options(out=Path(tmp) / "o"),
                                    provider=MockProvider(), log=lambda *a: None)
            plan = translator.build_plan()
            self.assertEqual(len(plan.units), 0)
            self.assertEqual(plan.stats.get("already_zh"), 1)


class McmTests(unittest.TestCase):
    def test_utf8_bom_and_crlf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "X_english.txt"
            path.write_bytes(b"\xef\xbb\xbf$A\tHello\r\n$B\tWorld\r\n")
            mcm = McmFile.load(path)
            self.assertEqual(len(mcm.keys), 2)
            mcm.set_text(0, "你好")
            data = mcm.serialize()
            self.assertIn("你好".encode("utf-8"), data)
            self.assertIn(b"\r\n", data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
