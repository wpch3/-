"""翻译记忆库（Translation Memory / 词典）。

这是 xTranslator 的 SST 词典、ESP-ESM Translator 的 BDD 数据库、mTRANSLATOR 的
本地记忆共同的核心：**已经有人翻过的句子，绝不再花钱翻第二遍**。

支持四种来源：
1. 我们自己的历史译文（每次跑完自动入库，下次直接命中）；
2. 现成词典文件：TSV / CSV（`英文<TAB>中文`），可以直接吃 xTranslator / ESP-ESM
   导出的对照表；
3. 现成汉化里的字符串表：把 `<Mod>_chinese.STRINGS` 和原版 `<Mod>_english.STRINGS`
   按 ID 一一配对（xTranslator「Build dictionaries of strings pairs」的做法）；
4. 现成汉化插件：英文 esp 与中文 esp 按 FormID + 字段配对（xTranslator 的
   EspCompare 做法）。

匹配分三级：
- 精确匹配；
- 归一化匹配（去大小写/标点/空格，数字当占位符，命中后把数字换回原文的数字，
  即 ESP-ESM Translator 的「translation without numbers」）；
- 模糊匹配（可选，默认关闭，阈值可调）。
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import esp
from .strings import StringTable
from .textfilter import contains_cjk, is_translatable, token_signature

CJK_LANGS = ("chinese", "chs", "cht", "zh", "zhcn", "zhtw", "cn", "sc", "tc")
NUM_RE = re.compile(r"\d+")


def normalize(text: str) -> str:
    """用于「同义不同写法」匹配：小写、数字归一、去掉标点与空白。"""
    s = text.lower().strip()
    s = NUM_RE.sub("#", s)
    s = re.sub(r"[^\w#]+", "", s, flags=re.UNICODE)
    return s


def _renumber(source: str, target: str) -> Optional[str]:
    """把译文里的数字替换回原文的数字；数量对不上就放弃。"""
    src_nums = NUM_RE.findall(source)
    if not src_nums:
        return target
    tgt_nums = NUM_RE.findall(target)
    if len(src_nums) != len(tgt_nums):
        return None
    it = iter(src_nums)
    return NUM_RE.sub(lambda _m: next(it), target)


class TranslationMemory:
    def __init__(self, path: Optional[Path]) -> None:
        self.path = path
        self.conn = None
        self._norm_cache: Dict[str, str] = {}
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tm (
                src TEXT NOT NULL,
                tgt TEXT NOT NULL,
                norm TEXT NOT NULL,
                origin TEXT NOT NULL DEFAULT '',
                updated REAL NOT NULL,
                uses INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (src, tgt)
            )
            """
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS tm_norm ON tm(norm)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS tm_src ON tm(src)")
        self.conn.commit()
        self.hits = 0

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def add(self, source: str, target: str, origin: str = "run") -> None:
        self.add_pairs([(source, target)], origin)

    def add_pairs(self, pairs: Iterable[Tuple[str, str]], origin: str = "run") -> int:
        if self.conn is None:
            return 0
        now = time.time()
        rows = []
        for source, target in pairs:
            if not source or not target or source == target:
                continue
            if len(source) > 2000:
                continue
            rows.append((source, target, normalize(source), origin, now))
        if not rows:
            return 0
        self.conn.executemany(
            "INSERT INTO tm(src, tgt, norm, origin, updated, uses) VALUES(?,?,?,?,?,0)"
            " ON CONFLICT(src, tgt) DO UPDATE SET updated=excluded.updated",
            rows,
        )
        self.conn.commit()
        return len(rows)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def lookup(self, text: str) -> Optional[str]:
        if self.conn is None:
            return None
        row = self.conn.execute(
            "SELECT tgt FROM tm WHERE src = ? ORDER BY uses DESC, updated DESC LIMIT 1",
            (text,),
        ).fetchone()
        if row:
            self._bump(text, row[0])
            return row[0]
        return None

    def lookup_normalized(self, text: str) -> Optional[str]:
        if self.conn is None:
            return None
        norm = normalize(text)
        if len(norm) < 4:
            return None
        rows = self.conn.execute(
            "SELECT tgt FROM tm WHERE norm = ? ORDER BY uses DESC, updated DESC LIMIT 5",
            (norm,),
        ).fetchall()
        if not rows:
            return None
        sig = token_signature(text)
        for (target,) in rows:
            if token_signature(target) != sig:
                continue
            fixed = _renumber(text, target)
            if fixed is None:
                continue
            self._bump(text, target)
            return fixed
        return None

    def lookup_fuzzy(self, text: str, threshold: float = 0.92) -> Optional[str]:
        """相似句子（默认不启用；适合 mod 更新后微调的文本）。"""
        if self.conn is None or threshold <= 0:
            return None
        norm = normalize(text)
        if len(norm) < 12 or len(norm) > 160:
            return None
        prefix = norm[:3]
        lo, hi = int(len(norm) * 0.75), int(len(norm) * 1.35) + 2
        rows = self.conn.execute(
            "SELECT tgt, norm FROM tm WHERE norm LIKE ? AND length(norm) BETWEEN ? AND ?"
            " ORDER BY uses DESC LIMIT 400",
            (prefix + "%", lo, hi),
        ).fetchall()
        if not rows:
            return None
        from difflib import SequenceMatcher

        sig = token_signature(text)
        best_score = 0.0
        best_target = None
        matcher = SequenceMatcher(None)
        for target, cand in rows:
            if token_signature(target) != sig:
                continue
            matcher.set_seq2(cand)
            matcher.set_seq1(norm)
            if matcher.real_quick_ratio() < threshold or matcher.quick_ratio() < threshold:
                continue
            score = matcher.ratio()
            if score > best_score:
                best_score, best_target = score, target
        if best_target is not None and best_score >= threshold:
            self._bump(text, best_target)
            return best_target
        return None

    def lookup_smart(self, text: str, fuzzy: float = 0.0) -> Tuple[Optional[str], str]:
        """依次尝试 精确 -> 归一化 -> 模糊，返回 (译文, 命中方式)。"""
        if self.conn is None:
            return None, ""
        hit = self.lookup(text)
        if hit is not None:
            return hit, "exact"
        hit = self.lookup_normalized(text)
        if hit is not None:
            return hit, "normalized"
        if fuzzy > 0:
            hit = self.lookup_fuzzy(text, fuzzy)
            if hit is not None:
                return hit, "fuzzy"
        return None, ""

    def _bump(self, source: str, target: str) -> None:
        if self.conn is None:
            return
        self.hits += 1
        try:
            self.conn.execute(
                "UPDATE tm SET uses = uses + 1 WHERE src = ? AND tgt = ?", (source, target)
            )
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------------
    # 导入
    # ------------------------------------------------------------------
    def import_file(self, path: Path, origin: Optional[str] = None) -> int:
        """导入 `英文<TAB>中文` 或两列 CSV。支持 BOM、自动跳过表头/注释。"""
        pairs: List[Tuple[str, str]] = []
        raw = Path(path).read_bytes()
        text = None
        for enc in ("utf-8-sig", "utf-16", "cp1252"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            return 0
        for line in text.splitlines():
            line = line.rstrip("\r")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if "\t" in line:
                parts = line.split("\t")
            else:
                parts = _split_csv(line)
            if len(parts) < 2:
                continue
            src, dst = parts[0].strip(), parts[1].strip()
            if not src or not dst:
                continue
            if src.lower() in ("原文", "source", "src", "english"):
                continue
            pairs.append((src, dst))
        return self.add_pairs(pairs, origin or Path(path).name)

    def import_tables(self, en: StringTable, zh: StringTable, origin: str = "strings") -> int:
        """按 ID 顺序配对两张字符串表（必须条数一致）。"""
        if len(en.entries) != len(zh.entries):
            return 0
        pairs = []
        for i in range(len(en.entries)):
            src = en.text(i)
            dst = zh.text(i)
            if not src or not dst or src == dst:
                continue
            if not contains_cjk(dst):
                continue
            if len(src) > 2000 or len(dst) > 2000:
                continue
            pairs.append((src, dst))
        return self.add_pairs(pairs, origin)

    def import_plugin_pair(self, en_path: Path, zh_path: Path) -> int:
        """英文插件 + 已汉化插件，按 FormID + 字段 + 出现次序配对。"""
        en_plugin = esp.Plugin.load(en_path)
        zh_plugin = esp.Plugin.load(zh_path)
        zh_index = {}
        for rec in zh_plugin.iter_records():
            seen: Dict[bytes, int] = {}
            for sub in rec.subs:
                idx = seen.get(sub.sig, 0)
                seen[sub.sig] = idx + 1
                zh_index[(rec.formid, sub.sig, idx)] = sub.text
        pairs = []
        for rec in en_plugin.iter_records():
            seen = {}
            for sub in rec.subs:
                idx = seen.get(sub.sig, 0)
                seen[sub.sig] = idx + 1
                src = sub.text
                if not src:
                    continue
                dst = zh_index.get((rec.formid, sub.sig, idx))
                if not dst or src == dst or not contains_cjk(dst):
                    continue
                if len(src) > 2000 or len(dst) > 2000:
                    continue
                pairs.append((src, dst))
        return self.add_pairs(pairs, Path(en_path).name)

    def export_tsv(self, path: Path) -> int:
        if self.conn is None:
            return 0
        rows = self.conn.execute(
            "SELECT src, tgt FROM tm WHERE length(src) BETWEEN 1 AND 500"
            " ORDER BY length(src) ASC"
        ).fetchall()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# 英文\t中文 —— skytrans 导出的词典，可直接给 xTranslator/ESP-ESM 用\n")
            for src, tgt in rows:
                fh.write("%s\t%s\n" % (src.replace("\t", " "), tgt.replace("\t", " ")))
        return len(rows)

    def stats(self) -> Dict[str, int]:
        if self.conn is None:
            return {"entries": 0, "hits": self.hits}
        total = self.conn.execute("SELECT COUNT(*) FROM tm").fetchone()[0]
        return {"entries": int(total), "hits": self.hits}

    def close(self) -> None:
        if self.conn is not None:
            self.conn.commit()
            self.conn.close()
            self.conn = None


def _split_csv(line: str) -> List[str]:
    out: List[str] = []
    cur: List[str] = []
    quoted = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            if quoted and i + 1 < len(line) and line[i + 1] == '"':
                cur.append('"')
                i += 2
                continue
            quoted = not quoted
        elif ch == "," and not quoted:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    out.append("".join(cur))
    return out


def is_cjk_lang(lang: str) -> bool:
    return lang.lower() in CJK_LANGS


def collect_chinese_tables(root: Path, suffixes: Sequence[str] = (".strings", ".dlstrings", ".ilstrings")):
    """在目录里找已汉化的字符串表（*_chinese/_zh/_chs...）。"""
    found = []
    pattern = re.compile(r"^(.+)_([A-Za-z]{2,20})\.(strings|dlstrings|ilstrings)$", re.I)
    for path in Path(root).rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        match = pattern.match(path.name)
        if match and is_cjk_lang(match.group(2)):
            found.append((path, match.group(1).lower(), match.group(3).lower()))
    return found
