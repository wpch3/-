"""主流水线：抽取 -> 词典/缓存命中 -> 翻译 -> 回写 -> 报告。"""

from __future__ import annotations

import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import esp
from .cache import Cache
from .fields import NEVER_TRANSLATE, translate_field
from .bsa import Bsa, BsaError
from .mcm import McmFile
from .pex import PexFile
from .providers import Provider, ProviderError
from .scan import Pack, PluginInfo
from .strings import StringTable
from .textfilter import (
    Glossary,
    Protector,
    contains_cjk,
    is_translatable,
    is_translated,
    split_text,
)
from .tm import TranslationMemory

LETTERS = re.compile(r"[A-Za-z\u00c0-\u024f]")
TEXT_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass
class Options:
    out: Path = Path("out")
    in_place: bool = False
    source_lang: str = "english"
    output_lang: str = "english"
    use_bsa: bool = True
    include_vanilla: bool = False
    min_chars: int = 2
    piece_chars: int = 1500
    limit: int = 0
    dry_run: bool = False
    only: List[str] = field(default_factory=list)
    exclude: List[str] = field(default_factory=list)
    export_review: bool = True
    workers: int = 1
    fuzzy: float = 0.0
    meta_ini: bool = True
    pex: bool = True
    bsa_write: bool = False


@dataclass
class Unit:
    uid: str
    text: str
    apply: Callable[[str], None]
    source: str
    kind: str
    rec_sig: str = ""
    sub_sig: str = ""
    edid: str = ""


@dataclass
class Piece:
    text: str
    sep: str


@dataclass
class Plan:
    units: List[Unit] = field(default_factory=list)
    tables: Dict[str, StringTable] = field(default_factory=dict)
    plugins: Dict[str, "esp.Plugin"] = field(default_factory=dict)
    mcm_files: List[McmFile] = field(default_factory=list)
    pex_files: List["PexFile"] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    applied: List[Tuple[Unit, str, str]] = field(default_factory=list)


def looks_like_text(text: str) -> bool:
    if not text or len(text) < 2:
        return False
    if TEXT_CONTROL_RE.search(text):
        return False
    letters = len(LETTERS.findall(text))
    if letters < 2:
        return False
    return letters / max(1, len(text)) >= 0.35


class Translator:
    def __init__(
        self,
        pack: Pack,
        options: Options,
        cache: Optional[Cache] = None,
        provider: Optional[Provider] = None,
        glossary: Optional[Glossary] = None,
        tm: Optional[TranslationMemory] = None,
        log=print,
    ) -> None:
        self.pack = pack
        self.options = options
        self.cache = cache
        self.provider = provider
        self.glossary = glossary
        self.tm = tm
        self.log = log
        self.failures: List[Tuple[str, str]] = []
        self.soft_drops = 0
        self.tm_hits = 0
        self.cache_hits = 0
        self._last_progress_time = 0.0
        self._last_progress_percent = -100.0

    # ------------------------------------------------------------------
    # 1. 抽取
    # ------------------------------------------------------------------
    def build_plan(self) -> Plan:
        plan = Plan()
        opts = self.options
        selected = [
            p
            for p in self.pack.plugins
            if (not opts.only or any(k.lower() in p.name.lower() for k in opts.only))
            and not (opts.exclude and any(k.lower() in p.name.lower() for k in opts.exclude))
        ]

        for plugin in selected:
            try:
                if plugin.localized:
                    self._collect_tables(plugin, plan)
                else:
                    self._collect_inline(plugin, plan)
            except Exception as exc:  # noqa: BLE001
                plan.warnings.append("跳过 %s：%s" % (plugin.name, exc))

        self._collect_mcm(plan)
        self._collect_pex(plan)

        stats = plan.stats
        stats.setdefault("plugins", len(selected))
        stats.setdefault("units", len(plan.units))
        stats.setdefault("already_zh", 0)
        stats.setdefault("not_text", 0)
        plan.stats = stats
        return plan

    def _unit_ok(self, text: str, plan: Plan) -> bool:
        if is_translated(text):
            plan.stats["already_zh"] = plan.stats.get("already_zh", 0) + 1
            return False
        if not is_translatable(text, self.options.min_chars):
            plan.stats["not_text"] = plan.stats.get("not_text", 0) + 1
            return False
        return True

    def _collect_tables(self, plugin: PluginInfo, plan: Plan) -> None:
        refs = self.pack.tables_for(plugin, self.options.source_lang)
        if not refs:
            plan.warnings.append(
                "%s 是本地化插件，但没找到 %s_*.strings 字符串表（可能在未解析的 BSA 里）"
                % (plugin.name, plugin.name.rsplit(".", 1)[0])
            )
            return
        for ref in refs:
            key = ref.name.lower()
            if key in plan.tables:
                table = plan.tables[key]
            else:
                try:
                    table = ref.load()
                except Exception as exc:  # noqa: BLE001
                    plan.warnings.append("字符串表读取失败 %s：%s" % (ref.name, exc))
                    continue
                plan.tables[key] = table
                plan.stats["table_entries"] = plan.stats.get("table_entries", 0) + len(
                    table.entries
                )
            for index in range(len(table.entries)):
                text = table.text(index)
                if not self._unit_ok(text, plan):
                    continue
                plan.units.append(
                    Unit(
                        uid="%s|%s|%d" % (plugin.base, ref.kind, index),
                        text=text,
                        apply=lambda value, t=table, i=index: t.set_text(i, value),
                        source=plugin.name,
                        kind="stringtable",
                        sub_sig=ref.kind,
                    )
                )
            table.path = ref.path or Path(ref.name)

    def _collect_inline(self, plugin: PluginInfo, plan: Plan) -> None:
        loaded = esp.Plugin.load(plugin.path)
        plan.plugins[plugin.base] = loaded
        count = 0
        for record in loaded.iter_records():
            rec_sig = record.sig.decode("latin-1")
            edid = ""
            edid_sub = record.get(b"EDID")
            if edid_sub is not None:
                edid = edid_sub.text
            for sub in record.subs:
                if sub.sig in NEVER_TRANSLATE:
                    continue
                if not translate_field(rec_sig, sub.sig):
                    continue
                sub_sig = sub.sig.decode("latin-1")
                if rec_sig == "GMST" and sub_sig == "DATA":
                    # 只有字符串型 GMST 才翻（数值型 DATA 固定 4 字节）
                    if len(sub.data) <= 4:
                        continue
                text = sub.text
                if not looks_like_text(text):
                    plan.stats["not_text"] = plan.stats.get("not_text", 0) + 1
                    continue
                if not self._unit_ok(text, plan):
                    continue
                plan.units.append(
                    Unit(
                        uid="%s|%08X|%s" % (plugin.base, record.formid, sub_sig),
                        text=text,
                        apply=sub.set_text,
                        source=plugin.name,
                        kind="plugin",
                        rec_sig=rec_sig,
                        sub_sig=sub_sig,
                        edid=edid,
                    )
                )
                count += 1
        if count:
            plan.stats["inline_units"] = plan.stats.get("inline_units", 0) + count

    def _collect_mcm(self, plan: Plan) -> None:
        for path in sorted(self.pack.mcm):
            name = path.name
            lang = self._mcm_lang(name)
            if lang and lang != self.options.source_lang and lang not in ("english", "en"):
                continue
            if lang in ("chinese", "chs", "cht", "zh"):
                continue
            try:
                mcm = McmFile.load(path)
            except Exception as exc:  # noqa: BLE001
                plan.warnings.append("MCM 读取失败 %s：%s" % (name, exc))
                continue
            if not mcm.keys:
                continue
            plan.mcm_files.append(mcm)
            plan.stats["mcm_files"] = plan.stats.get("mcm_files", 0) + 1
            for index in range(len(mcm.keys)):
                text = mcm.text(index)
                if not self._unit_ok(text, plan):
                    continue
                plan.units.append(
                    Unit(
                        uid="%s|%d" % (mcm.path.stem, index),
                        text=text,
                        apply=lambda value, m=mcm, i=index: m.set_text(i, value),
                        source=name,
                        kind="mcm",
                    )
                )

    def _collect_pex(self, plan: Plan) -> None:
        """收集 .pex 脚本里的界面字符串（松散脚本 + BSA 内脚本）。

        只有当它看起来是「玩家能看到的话」时才进队列：脚本里绝大部分字符串是
        函数名/属性名/状态名，翻译它们没有好处，还可能踩到 Papyrus 的大小写不敏感匹配。
        """
        if not self.options.pex:
            return
        files: List[PexFile] = []

        for path in self.pack.scripts:
            try:
                files.append(PexFile(path=path))
            except Exception as exc:  # noqa: BLE001
                plan.warnings.append("脚本读取失败（已跳过）%s：%s" % (path.name, exc))

        for archive, entries in self.pack.bsa_scripts().items():
            try:
                with Bsa(archive) as bsa:
                    for entry in entries:
                        try:
                            files.append(
                                PexFile(
                                    data=bsa.read(entry),
                                    archive=archive,
                                    entry_path=entry.path,
                                    label=entry.basename,
                                )
                            )
                        except Exception:  # noqa: BLE001
                            continue
            except BsaError as exc:
                plan.warnings.append("脚本归档读取失败（已跳过）%s：%s" % (archive.name, exc))

        plan.pex_files = files
        plan.stats["pex_files"] = len(files)
        count = 0
        for pex in files:
            for item in pex.candidates:
                text = item.text
                if not self._unit_ok(text, plan):
                    continue
                plan.units.append(
                    Unit(
                        uid="pex|%s|%d" % (pex.origin, item.index),
                        text=text,
                        apply=lambda value, p=pex, i=item.index: p.set(i, value),
                        source=pex.origin,
                        kind="pex",
                        edid=pex.label,
                    )
                )
                count += 1
        plan.stats["pex_units"] = count

    @staticmethod
    def _mcm_lang(filename: str) -> Optional[str]:
        stem = Path(filename).stem
        if "_" not in stem:
            return None
        return stem.rsplit("_", 1)[-1].lower()

    # ------------------------------------------------------------------
    # 2. 翻译
    # ------------------------------------------------------------------
    def translate(self, plan: Plan) -> None:
        if not plan.units:
            return
        opts = self.options

        pieces_per_unit: List[List[Piece]] = []
        vocabulary: Dict[str, None] = {}
        for unit in plan.units:
            pieces = [Piece(text=t, sep=s) for t, s in split_text(unit.text, opts.piece_chars)]
            pieces_per_unit.append(pieces)
            for piece in pieces:
                vocabulary.setdefault(piece.text, None)

        unique = list(vocabulary)
        total_chars = sum(len(t) for t in unique)
        plan.stats["unique_pieces"] = len(unique)
        plan.stats["chars"] = total_chars
        self.log(
            "待翻译：%d 条文本 / %d 个唯一片段 / %d 字符（去重省掉 %.0f%%）"
            % (
                len(plan.units),
                len(unique),
                total_chars,
                100 * (1 - len(unique) / max(1, len(plan.units))),
            )
        )

        if opts.limit and len(unique) > opts.limit:
            unique = unique[: opts.limit]
            self.log("试跑模式：只翻译前 %d 个片段" % opts.limit)

        translations: Dict[str, str] = {}
        from_memory = set()  # 来自缓存/词典、本次没有真正翻译的条目

        # 2.1 先查上次的缓存（同一引擎的历史结果优先，人工校对也走这里）
        if self.cache is not None:
            translations.update(self.cache.get_many([self.cache.namespace], unique))
            self.cache_hits = len(translations)
            from_memory.update(translations)
            if self.cache_hits:
                self.log("缓存命中 %d 条（不用重新翻、也不用花钱）" % self.cache_hits)

        # 2.2 再查词典（xTranslator SST / ESP-ESM BDD 那套：翻过的不再翻）
        if self.tm is not None:
            for text in unique:
                if text in translations:
                    continue
                hit, how = self.tm.lookup_smart(text, opts.fuzzy)
                if hit is not None and hit != text:
                    translations[text] = hit
                    from_memory.add(text)
                    self.tm_hits += 1
            if self.tm_hits:
                self.log("词典命中 %d 条（精确/归一化/模糊）" % self.tm_hits)

        pending = [t for t in unique if t not in translations]
        plan.stats["cache_hits"] = self.cache_hits
        plan.stats["tm_hits"] = self.tm_hits
        plan.stats["api_items"] = len(pending)
        self.log(
            "需要调用接口：%d 条 / %d 字符"
            % (len(pending), sum(len(t) for t in pending))
        )

        if opts.dry_run or self.provider is None or self.provider.name == "none":
            self.log("扫描/试跑模式：不调用翻译接口，仅生成清单与统计。")
            self._apply(plan, pieces_per_unit, translations)
            return

        protectors: Dict[str, Protector] = {}
        protected: Dict[str, str] = {}
        for text in pending:
            protector = Protector()
            protected[text] = protector.protect(text)
            protectors[text] = protector

        batches = self._make_batches(pending, protected)
        started = time.time()
        total_pending_chars = sum(len(t) for t in pending)
        done_items = 0
        done_chars = 0
        batch_chars = [sum(len(t) for t in b) for b in batches]

        def absorb(result: Dict[str, str], size: int, chars: int) -> None:
            nonlocal done_items, done_chars
            for src, dst in result.items():
                final = self._finalize(src, dst, protectors[src])
                if final is None:
                    self.failures.append((src, "译文疑似无效（无中文或占位符丢失）"))
                    continue
                translations[src] = final
            if self.cache is not None:
                self.cache.put_many(
                    self.cache.namespace,
                    [(src, translations[src]) for src in result if src in translations],
                )
            done_items += size
            done_chars += chars
            self._progress(done_items, len(pending), done_chars, total_pending_chars, started)

        workers = max(1, int(opts.workers or 1))
        if workers > 1 and len(batches) > 1:
            self.log("并发请求：%d 线程" % workers)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(self._translate_resilient, batch, protected): idx
                    for idx, batch in enumerate(batches)
                }
                for future, idx in futures.items():
                    result = future.result()
                    absorb(result, len(batches[idx]), batch_chars[idx])
        else:
            for idx, batch in enumerate(batches):
                result = self._translate_resilient(batch, protected)
                absorb(result, len(batch), batch_chars[idx])

        if pending:
            self.log("")
            elapsed = time.time() - started
            self.log(
                "翻译完成：%d 条，用时 %.1f 分钟，接口调用 %d 次"
                % (len(pending), elapsed / 60, self.provider.calls if self.provider else 0)
            )

        # 2.4 学习：把本次结果写进词典，下次（或别的 mod）直接复用
        if self.tm is not None:
            learned = self.tm.add_pairs(
                [
                    (src, dst)
                    for src, dst in translations.items()
                    if src != dst and src not in from_memory
                ],
                "run",
            )
            if learned:
                self.log("已向词典补充 %d 条" % learned)

        self._apply(plan, pieces_per_unit, translations)

    def _translate_resilient(
        self, batch: Sequence[str], protected: Dict[str, str]
    ) -> Dict[str, str]:
        payload = [protected[t] for t in batch]
        try:
            outs = self.provider.translate(payload)
        except ProviderError as exc:
            if len(batch) <= 1:
                self.failures.append((batch[0], str(exc)))
                return {}
            mid = len(batch) // 2
            left = self._translate_resilient(batch[:mid], protected)
            right = self._translate_resilient(batch[mid:], protected)
            left.update(right)
            return left
        return dict(zip(batch, outs))

    def _finalize(self, source: str, translated: str, protector: Protector) -> Optional[str]:
        text = (translated or "").strip()
        if not text:
            return None
        restored, missing, dropped_soft = protector.restore(text)
        if missing:
            return None
        if dropped_soft:
            self.soft_drops += 1
        if len(LETTERS.findall(source)) >= 4 and not contains_cjk(restored):
            return None
        if len(restored) > max(60, len(source) * 4):
            return None
        if self.glossary is not None:
            restored = self.glossary.enforce(source, restored)
        return restored

    def _make_batches(self, texts: Sequence[str], protected: Dict[str, str]) -> List[List[str]]:
        max_chars = getattr(self.provider, "batch_chars", 2400)
        max_items = getattr(self.provider, "batch_items", 30)
        batches: List[List[str]] = []
        current: List[str] = []
        chars = 0
        for text in texts:
            size = len(protected[text])
            if current and (chars + size > max_chars or len(current) >= max_items):
                batches.append(current)
                current, chars = [], 0
            current.append(text)
            chars += size
        if current:
            batches.append(current)
        return batches

    def _progress(
        self,
        done_items: int,
        total_items: int,
        done_chars: int,
        total_chars: int,
        started: float,
    ) -> None:
        elapsed = max(0.001, time.time() - started)
        rate = done_chars / elapsed
        remaining = (total_chars - done_chars) / rate if rate > 0 else 0
        percent = 100 * done_items / max(1, total_items)
        msg = "  进度 %5.0f%%  %d/%d 条  %d/%d 字  剩余约 %.0f 分钟" % (
            percent,
            done_items,
            total_items,
            done_chars,
            total_chars,
            remaining / 60,
        )
        if sys.stderr.isatty():
            sys.stderr.write("\r" + msg)
            sys.stderr.flush()
            return
        now = time.time()
        if (
            now - self._last_progress_time >= 10
            or percent - self._last_progress_percent >= 10
            or done_items >= total_items
        ):
            self._last_progress_time = now
            self._last_progress_percent = percent
            self.log(msg)

    def _apply(
        self,
        plan: Plan,
        pieces_per_unit: List[List[Piece]],
        translations: Dict[str, str],
    ) -> None:
        applied = 0
        unchanged = 0
        for unit, pieces in zip(plan.units, pieces_per_unit):
            if len(pieces) == 1 and pieces[0].text in translations:
                value = translations[pieces[0].text]
            elif all(piece.text in translations for piece in pieces):
                value = "".join(translations[p.text] + p.sep for p in pieces)
            else:
                unchanged += 1
                continue
            if value and value != unit.text:
                unit.apply(value)
                plan.applied.append((unit, unit.text, value))
                applied += 1
        plan.stats["applied"] = applied
        plan.stats["unchanged"] = unchanged
        self.log("已写回 %d 条文本（未翻译 %d 条保留原文）" % (applied, unchanged))

    # ------------------------------------------------------------------
    # 3. 写出
    # ------------------------------------------------------------------
    def write(self, plan: Plan) -> Dict[str, int]:
        opts = self.options
        out = Path(opts.out)
        written = {"plugins": 0, "tables": 0, "mcm": 0, "pex": 0, "bsa": 0}

        plugin_by_base = {p.base: p for p in self.pack.plugins}

        for base, plugin in plan.plugins.items():
            if not plugin.dirty:
                continue
            info = plugin_by_base.get(base)
            target = (
                info.path
                if opts.in_place and info is not None
                else out / (info.name if info else base + ".esp")
            )
            if opts.in_place and info is not None:
                self._backup(info.path)
            plugin.save(target)
            written["plugins"] += 1
            self._log_write("插件", target, written["plugins"])

        for key, table in plan.tables.items():
            if not table.dirty:
                continue
            name = self._renamed_table(table.path.name if table.path else key)
            info = plugin_by_base.get(key.rsplit("_", 1)[0])
            if opts.in_place and info is not None:
                target = info.path.parent / "Strings" / name
                if not target.exists():
                    target = info.path.parent / name
                self._backup(target)
            else:
                target = out / "Strings" / name
            table.save(target)
            written["tables"] += 1
            self._log_write("字符串表", target, written["tables"])

        for mcm in plan.mcm_files:
            if not mcm.dirty:
                continue
            name = self._renamed_mcm(mcm.path.name)
            target = (
                mcm.path if opts.in_place else out / "Interface" / "Translations" / name
            )
            if opts.in_place:
                self._backup(mcm.path)
            mcm.save(target)
            written["mcm"] += 1
            self._log_write("MCM", target, written["mcm"])

        # ---- 脚本（.pex）：默认输出松散文件，覆盖 BSA 内的同名脚本 ----
        archive_patches: Dict[Path, Dict[str, bytes]] = {}
        for pex in plan.pex_files:
            if not pex.dirty:
                continue
            pex.verify()
            if pex.archive is not None:
                archive_patches.setdefault(Path(pex.archive), {})[pex.entry_path] = (
                    pex.to_bytes()
                )
            if opts.in_place and pex.archive is None and pex.path is not None:
                self._backup(pex.path)
                pex.save(pex.path)
                target = pex.path
            else:
                target = out / self._pex_target(pex)
                pex.save(target)
            written["pex"] += 1
            self._log_write("脚本", target, written["pex"])

        # ---- 可选：把改过的脚本重新打包回 BSA ----
        if archive_patches and (opts.bsa_write or opts.in_place):
            for archive, patches in archive_patches.items():
                target = archive if opts.in_place else out / archive.name
                if opts.in_place:
                    self._backup(archive)
                try:
                    with Bsa(archive) as bsa:
                        bsa.rewrite(target, patches, verify=True)
                except BsaError as exc:
                    plan.warnings.append("BSA 重打包失败（脚本仍以松散文件输出）%s：%s"
                                         % (archive.name, exc))
                    continue
                written["bsa"] += 1
                self._log_write("脚本归档", target, written["bsa"])

        if not opts.in_place:
            self._write_readme(out, written)
            if opts.meta_ini:
                self._write_meta(out)
        return written

    def _log_write(self, kind: str, target: Path, count: int) -> None:
        if count <= 6:
            self.log("  写出 %s: %s" % (kind, target))
        elif count == 7:
            self.log("  写出 %s: ……（完整列表见汉化报告）" % kind)

    def _pex_target(self, pex: "PexFile") -> Path:
        """把脚本放到输出目录里的正确位置（游戏只认 Data/Scripts 下）。"""
        if pex.archive is not None:
            parts = [p for p in pex.entry_path.split("\\") if p]
            if parts and parts[0].lower() == "scripts":
                parts = parts[1:]
            return Path("Scripts").joinpath(*parts) if parts else Path("Scripts") / pex.label
        if pex.path is not None:
            try:
                return pex.path.relative_to(self.pack.root)
            except ValueError:
                parts = list(pex.path.parts)
                if "scripts" in [p.lower() for p in parts]:
                    index = [p.lower() for p in parts].index("scripts")
                    return Path(*parts[index:])
                return Path("Scripts") / pex.path.name
        return Path("Scripts") / pex.label

    def _renamed_table(self, name: str) -> str:
        suffix = Path(name).suffix
        stem = Path(name).stem
        if "_" not in stem:
            return name
        base, _lang = stem.rsplit("_", 1)
        return "%s_%s%s" % (base, self.options.output_lang, suffix)

    def _renamed_mcm(self, name: str) -> str:
        stem = Path(name).stem
        if "_" not in stem:
            return name
        base, _lang = stem.rsplit("_", 1)
        return "%s_%s.txt" % (base, self.options.output_lang)

    def _backup(self, path: Path) -> None:
        if path.exists():
            bak = path.with_suffix(path.suffix + ".orig")
            if not bak.exists():
                bak.write_bytes(path.read_bytes())

    def _write_meta(self, out: Path) -> None:
        """MO2 的 meta.ini，让汉化包在 MO2 里显示正常（而不是「未识别 mod」）。"""
        meta = (
            "[General]\n"
            "gameName=skyrimspecialedition\n"
            "modid=0\n"
            "version=1.0\n"
            "category=0,汉化\n"
            "comments=由 skyrim-zh-translator 生成\n"
        )
        try:
            (out / "meta.ini").write_text(meta, encoding="utf-8")
        except OSError:
            pass

    def _write_readme(self, out: Path, written: Dict[str, int]) -> None:
        out.mkdir(parents=True, exist_ok=True)
        text = README_TEMPLATE.format(
            plugins=written["plugins"],
            tables=written["tables"],
            mcm=written["mcm"],
            pex=written.get("pex", 0),
            bsa=written.get("bsa", 0),
        )
        (out / "安装说明.txt").write_text(text, encoding="utf-8")


README_TEMPLATE = """天际模组汉化包（由 skyrim-zh-translator 生成）
=============================================

内容：
  插件（含中文内嵌字符串） : {plugins} 个
  字符串表 (.STRINGS 等)   : {tables} 个
  MCM 菜单翻译文件         : {mcm} 个
  脚本 (.pex，界面文字)    : {pex} 个
  重打包的 BSA             : {bsa} 个（0 表示只输出松散脚本）

安装（MO2）：
  1. 把整个文件夹压缩成 zip，或用 MO2 的「从文件夹安装」指向本目录；
  2. 在左侧列表启用本 mod，放在所有被汉化 mod 的下面（覆盖优先级更高）；
  3. 若本 mod 里有 .esp，请在右侧插件列表启用它；
  4. 确认游戏语言仍为 English（Skyrim.ini 里 sLanguage=ENGLISH）。
     汉化包覆盖的就是 *_english 文件，不需要改成中文语言。

安装（Vortex / 手动）：
  把本目录里的所有内容复制到 Data 目录下，覆盖同名文件（先备份）。

必须的字体：
  Skyrim 原生字体没有中文字形，需要安装中文字体补丁（如「字体替换 / Chinese Font」
  类 mod），否则中文会显示成方块。字体 mod 与本汉化包互不冲突。

回滚：
  删除本 mod 即可完全恢复英文原文（松散文件覆盖机制）。
"""


def write_report(
    plan: Plan,
    provider_name: str,
    path: Path,
    failures,
    warnings,
    tm_stats: Optional[Dict[str, int]] = None,
) -> None:
    s = plan.stats
    lines = [
        "# 汉化报告",
        "",
        "- 翻译引擎：`%s`" % provider_name,
        "- 插件：%d 个（本地化 %d / 内嵌 %d）"
        % (
            s.get("plugins", 0),
            s.get("localized_plugins", 0),
            s.get("inline_plugins", 0),
        ),
        "- 抽取到可翻译文本：%d 条" % s.get("units", 0),
        "- 去重后唯一片段：%d 条 / %d 字符" % (s.get("unique_pieces", 0), s.get("chars", 0)),
        "- 词典命中：%d 条；缓存命中：%d 条；实际调用接口：%d 条"
        % (s.get("tm_hits", 0), s.get("cache_hits", 0), s.get("api_items", 0)),
        "- 已写回：%d 条；保留原文：%d 条" % (s.get("applied", 0), s.get("unchanged", 0)),
        "- 脚本（.pex）：%d 个文件，其中界面文字 %d 条"
        % (s.get("pex_files", 0), s.get("pex_units", 0)),
        "- 判定已是中文而跳过：%d 条" % s.get("already_zh", 0),
        "- 判定非文本而跳过：%d 条" % s.get("not_text", 0),
        "",
    ]
    if tm_stats:
        lines += [
            "- 词典条目总数：%d" % tm_stats.get("entries", 0),
            "",
        ]
    if failures:
        lines += ["## 失败条目（%d）" % len(failures), ""]
        for text, reason in failures[:50]:
            lines.append("- `%s` — %s" % (text[:80].replace("\n", " "), reason))
        lines.append("")
    if warnings:
        lines += ["## 警告（%d）" % len(warnings), ""]
        lines += ["- %s" % w for w in warnings[:80]]
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
