"""文本处理：过滤不值得翻译的串、保护占位符、长文本切分、术语表。"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
LETTER_RE = re.compile(r"[A-Za-z\u00c0-\u024f]")

# 需要原样保留的片段：(正则, 是否"必须保留")
# 必须保留的（printf 占位符、格式化花括号、变量、链接）一旦丢失就放弃该条译文，回退英文；
# 次要的（富文本标签、方括号标记、\n）丢了只记录，不影响采用译文。
PROTECT_PATTERNS = [
    (re.compile(r"%[-+ 0#]*\d*(?:\.\d+)?[sdifxeEgGc]"), True),
    (re.compile(r"\{\d*(?::[^{}\n]*)?\}"), True),
    (re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*"), True),
    (re.compile(r"https?://\S+"), True),
    (re.compile(r"<[^<>\n]{1,160}>"), False),
    (re.compile(r"\[[^\[\]\n]{1,80}\]"), False),
    (re.compile(r"\\[nrt]"), False),
]

MARK_L = "\u27e6"  # ⟦
MARK_R = "\u27e7"  # ⟧
MARK_RE = re.compile(r"[\u27e6\[【]\s*(\d+)\s*[\u27e7\]】]")

FILE_EXT_RE = re.compile(
    r"^[\w\-.\\/]+\.(nif|dds|wav|xwm|fuz|hkx|pex|esp|esm|esl|bsa|ba2|json|xml|kf|"
    r"tri|egm|dat|txt|swf|lip|psc|seq|ini|toml|yaml|yml|png|tga|mp3|ogg|xma|dlod)$",
    re.IGNORECASE,
)
IDENT_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
SPACE_ONLY_RE = re.compile(r"^[\s0-9.,:;%+\-/()[\]{}<>|*#=~_\"'`!?]*$")


def contains_cjk(text: str) -> bool:
    return bool(CJK_RE.search(text))


def cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    return len(CJK_RE.findall(text)) / max(1, len(text))


def is_translated(text: str, ratio: float = 0.12) -> bool:
    """看起来已经是中文了。"""
    return cjk_ratio(text) >= ratio


def is_translatable(text: str, min_chars: int = 2) -> bool:
    """是否值得送去翻译（保守判断，宁可漏也不乱翻）。"""
    s = text.strip()
    if len(s) < min_chars:
        return False
    if is_translated(s):
        return False
    if not LETTER_RE.search(s):
        return False
    if SPACE_ONLY_RE.match(s):
        return False
    if FILE_EXT_RE.match(s):
        return False
    if IDENT_RE.match(s) and (("_" in s) or ("." in s)):
        return False
    # 形如 "0x1F" / "1.0" / "12" 的东西
    if re.fullmatch(r"[0-9a-fA-FxX\s.,:%+-]{1,12}", s):
        return False
    # 纯大写短代码（如 "MCM" / "DLC1"）
    if re.fullmatch(r"[A-Z0-9_]{2,8}", s) and len(s) <= 4:
        return False
    # 至少要有 2 个字母，避免 "A" "I" 这类
    if len(LETTER_RE.findall(s)) < 2:
        return False
    return True


class Protector:
    """把占位符换成 ⟦n⟧，翻译后再换回来。"""

    def __init__(self) -> None:
        self._store: List[str] = []
        self._hard: List[bool] = []

    def protect(self, text: str) -> str:
        self._store = []
        self._hard = []
        out = text
        for pattern, hard in PROTECT_PATTERNS:
            def repl(match: re.Match, is_hard: bool = hard) -> str:
                self._store.append(match.group(0))
                self._hard.append(is_hard)
                return "%s%d%s" % (MARK_L, len(self._store) - 1, MARK_R)

            out = pattern.sub(repl, out)
        return out

    def restore(self, text: str) -> Tuple[str, List[int], List[int]]:
        """返回 (还原后的文本, 丢失的必须保留项, 丢失的次要项)。"""
        seen = set()
        missing: List[int] = []

        def repl(match: re.Match) -> str:
            idx = int(match.group(1))
            seen.add(idx)
            if 0 <= idx < len(self._store):
                return self._store[idx]
            missing.append(idx)
            return ""

        out = MARK_RE.sub(repl, text)
        dropped_hard = [
            i
            for i in range(len(self._store))
            if i not in seen and self._hard[i]
        ]
        dropped_soft = [
            i
            for i in range(len(self._store))
            if i not in seen and not self._hard[i]
        ]
        return out, dropped_hard + missing, dropped_soft

    @property
    def count(self) -> int:
        return len(self._store)


def split_text(text: str, limit: int = 1800) -> List[Tuple[str, str]]:
    """把超长文本切成若干段，返回 [(片段, 分隔符)]，拼接后可还原原文。

    优先按换行切，其次按句末标点，实在不行硬切。
    """
    if len(text) <= limit:
        return [(text, "")]

    pieces: List[Tuple[str, str]] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = -1
        sep = ""
        for pat in ("\n", ". ", "! ", "? ", "; ", ", "):
            idx = window.rfind(pat)
            if idx > limit // 3:
                cut = idx
                sep = pat
                break
        if cut < 0:
            cut, sep = limit, ""
        pieces.append((rest[:cut], sep))
        rest = rest[cut + len(sep) :]
    pieces.append((rest, ""))
    return pieces


class Glossary:
    """术语表：保证专有名词前后一致。"""

    def __init__(self, mapping: Optional[Dict[str, str]] = None) -> None:
        self.mapping: Dict[str, str] = {}
        if mapping:
            for src, dst in mapping.items():
                if src and dst:
                    self.mapping[src.strip()] = dst.strip()
        # 长词优先匹配
        self._keys = sorted(self.mapping, key=len, reverse=True)
        self._re = re.compile(
            r"(?<![A-Za-z])(" + "|".join(re.escape(k) for k in self._keys) + r")(?![A-Za-z])"
        ) if self._keys else None

    def __len__(self) -> int:
        return len(self.mapping)

    @classmethod
    def load(cls, path) -> "Glossary":
        mapping: Dict[str, str] = {}
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    parts = line.split(",")
                if len(parts) >= 2:
                    mapping[parts[0].strip()] = parts[1].strip()
        return cls(mapping)

    def prompt_block(self, max_terms: int = 80, text: str = "") -> str:
        """生成提示词里的术语表片段（只挑文本中出现的词，省 token）。"""
        if not self.mapping:
            return ""
        if text and self._re is not None:
            hits = {m.group(1) for m in self._re.finditer(text)}
            terms = [(k, self.mapping[k]) for k in self._keys if k in hits]
        else:
            terms = [(k, self.mapping[k]) for k in self._keys]
        if not terms:
            return ""
        terms = terms[:max_terms]
        return "术语表（必须使用）：\n" + "\n".join("%s = %s" % (k, v) for k, v in terms)

    def enforce(self, source: str, translation: str) -> str:
        """兜底：译文里如果还留着英文原词，替换成术语表里的中文。

        只在译文已经是中文（含 CJK）时生效，避免把「整段没翻」的英文强行混搭；
        过短的词（如 HP、MCM）不处理。
        """
        if self._re is None or not translation:
            return translation
        if not CJK_RE.search(translation):
            return translation
        out = translation
        for match in self._re.finditer(source):
            term = match.group(1)
            if len(term) < 3:
                continue
            target = self.mapping.get(term)
            if not target:
                continue
            if target in out:
                continue
            # 译文里仍原样留着英文词 -> 直接替换
            if term in out:
                out = out.replace(term, target)
        return out
