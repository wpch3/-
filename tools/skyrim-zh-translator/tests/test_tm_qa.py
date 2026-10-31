"""词典（TM）、并发、质检的测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skytrans import esp  # noqa: E402
from skytrans.pipeline import Options, Plan, Translator, Unit  # noqa: E402
from skytrans.providers import MockProvider  # noqa: E402
from skytrans.qa import ERROR, WARN, check_plan  # noqa: E402
from skytrans.scan import scan_pack  # noqa: E402
from skytrans.strings import StringTable  # noqa: E402
from skytrans.tm import TranslationMemory, normalize  # noqa: E402
from test_all import make_plugin  # noqa: E402


class TMTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "tm.sqlite"
        self.tm = TranslationMemory(self.path)

    def tearDown(self):
        self.tm.close()
        self.tmp.cleanup()

    def test_exact_and_normalized(self):
        self.tm.add("Iron Sword", "铁剑")
        self.assertEqual(self.tm.lookup("Iron Sword"), "铁剑")
        # 归一化：大小写/标点/空格差异
        self.assertEqual(self.tm.lookup_normalized("iron  sword."), "铁剑")
        self.assertEqual(self.tm.lookup_smart("IRON SWORD")[1], "normalized")

    def test_number_renumbering(self):
        """ESP-ESM Translator 的「translation without numbers」：数字换回原文的。"""
        self.tm.add("Deal 10 points of fire damage.", "造成 10 点火焰伤害。")
        self.assertEqual(
            self.tm.lookup_normalized("Deal 25 points of fire damage."),
            "造成 25 点火焰伤害。",
        )

    def test_token_signature_guard(self):
        """占位符不一致的条目不能拿来复用。"""
        self.tm.add("You have %s gold.", "你有 %s 金币。")
        self.assertIsNone(self.tm.lookup_normalized("You have 5 gold."))

    def test_fuzzy_opt_in(self):
        self.tm.add("The quick brown fox jumps over the lazy dog.",
                    "敏捷的棕色狐狸跳过了懒狗。")
        # 只差一个词：阈值高时不命中，阈值低时命中
        self.assertIsNone(
            self.tm.lookup_fuzzy("The quick brown cat jumps over the lazy dog.", threshold=0.99)
        )
        self.assertEqual(
            self.tm.lookup_fuzzy("The quick brown cat jumps over the lazy dog.", threshold=0.90),
            "敏捷的棕色狐狸跳过了懒狗。",
        )

    def test_import_file_and_export(self):
        tsv = Path(self.tmp.name) / "dic.tsv"
        tsv.write_text("# 注释\nIron Sword\t铁剑\nSteel Sword\t钢剑\n", encoding="utf-8")
        self.assertEqual(self.tm.import_file(tsv), 2)
        self.assertEqual(self.tm.stats()["entries"], 2)
        out = Path(self.tmp.name) / "export.tsv"
        self.assertEqual(self.tm.export_tsv(out), 2)
        self.assertIn("铁剑", out.read_text(encoding="utf-8"))

    def test_import_tables_by_id(self):
        en = StringTable(path=Path("x"), kind="strings",
                         entries=[b"Iron Sword", b"Steel Sword", b""])
        zh = StringTable(path=Path("x"), kind="strings",
                         entries=["铁剑".encode(), "钢剑".encode(), b""])
        self.assertEqual(self.tm.import_tables(en, zh), 2)
        self.assertEqual(self.tm.lookup("Steel Sword"), "钢剑")

    def test_import_plugin_pair(self):
        tmp = Path(self.tmp.name)
        en_path = tmp / "En.esp"
        zh_path = tmp / "Zh.esp"
        en_path.write_bytes(make_plugin())
        zh_plugin = esp.Plugin.load(en_path)
        for rec in zh_plugin.iter_records():
            for sig, value in ((b"FULL", "铁剑"), (b"DESC", "一段中文描述。")):
                sub = rec.get(sig)
                if sub is not None:
                    sub.set_text(value)
        zh_path.write_bytes(zh_plugin.serialize())
        n = self.tm.import_plugin_pair(en_path, zh_path)
        self.assertGreaterEqual(n, 3)
        self.assertEqual(self.tm.lookup("Iron Sword"), "铁剑")

    def test_normalize(self):
        self.assertEqual(normalize("Iron Sword!"), normalize("iron  sword"))


class TmPipelineTests(unittest.TestCase):
    def test_tm_avoids_api_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "pack"
            root.mkdir()
            (root / "ModA.esp").write_bytes(make_plugin())
            pack = scan_pack(root, use_bsa=False, log=lambda *a: None)

            tm = TranslationMemory(Path(tmp) / "tm.sqlite")
            tm.add("Iron Sword", "铁剑")
            tm.add("Steel Sword", "钢剑")

            provider = MockProvider()
            translator = Translator(
                pack, Options(out=Path(tmp) / "out"), provider=provider, tm=tm,
                log=lambda *a: None,
            )
            plan = translator.build_plan()
            translator.translate(plan)
            self.assertEqual(translator.tm_hits, 2)
            self.assertGreater(provider.calls, 0)  # 其余文本仍然走接口

            texts = {}
            for rec in plan.plugins["moda"].iter_records():
                if rec.sig == b"WEAP":
                    texts[rec.formid] = rec.get(b"FULL").text
            self.assertEqual(texts[0x01000800], "铁剑")
            self.assertEqual(texts[0x01000801], "钢剑")
            tm.close()

    def test_workers_parallel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "pack"
            root.mkdir()
            (root / "ModA.esp").write_bytes(make_plugin())
            pack = scan_pack(root, use_bsa=False, log=lambda *a: None)
            provider = MockProvider()
            provider.batch_items = 1
            translator = Translator(
                pack, Options(out=Path(tmp) / "out", workers=4), provider=provider,
                log=lambda *a: None,
            )
            plan = translator.build_plan()
            translator.translate(plan)
            self.assertEqual(plan.stats["applied"], len(plan.units))


class QATests(unittest.TestCase):
    def _plan(self, pairs):
        plan = Plan()
        for rec_sig, sub_sig, src, dst in pairs:
            unit = Unit(uid="u", text=src, apply=lambda v: None,
                        source="Mod.esp", kind="plugin", rec_sig=rec_sig, sub_sig=sub_sig)
            plan.applied.append((unit, src, dst))
        return plan

    def test_placeholder_mismatch_is_error(self):
        plan = self._plan([("MESG", "DESC", "You have %s gold.", "你有金币。")])
        issues = check_plan(plan)
        self.assertTrue(any(i.level == ERROR and i.kind == "标记不一致" for i in issues))

    def test_alias_tag_preserved_ok(self):
        plan = self._plan([("INFO", "NAM1", "Hello <Alias=Player>.", "你好，<Alias=Player>。")])
        issues = check_plan(plan)
        self.assertFalse(any(i.kind == "标记不一致" for i in issues))

    def test_untranslated_detected(self):
        plan = self._plan([("WEAP", "FULL", "Iron Sword", "Iron Sword")])
        kinds = {i.kind for i in check_plan(plan)}
        self.assertIn("未翻译", kinds)

    def test_overlong_detected(self):
        long_text = "剑" * 200
        plan = self._plan([("WEAP", "FULL", "Iron Sword", long_text)])
        self.assertTrue(any(i.kind == "可能超长" for i in check_plan(plan)))

    def test_inconsistent_translations(self):
        plan = self._plan([
            ("MESG", "ITXT", "Yes", "是"),
            ("MESG", "ITXT", "Yes", "是的"),
        ])
        issues = check_plan(plan)
        self.assertTrue(any(i.kind == "同原文多译法" and i.level == WARN for i in issues))


if __name__ == "__main__":
    unittest.main(verbosity=2)
