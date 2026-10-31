"""可翻译字段白名单 + 官方内容里观察到的长度上限。

依据（都是公开的事实性数据，非代码拷贝）：
- TES4/TES5 记录结构：`xEdit` 的 wbDefinitionsTES5.pas 对每个子记录的类型定义；
- `xTranslator`（MPL-2.0）Data/Skyrim 下的记录定义与字段长度参考：
  FULL 对所有记录都是短字符串、DESC 是长字符串，(MGEF,DNAM)/(QUST,CNAM)/… 等
  需要单独登记，以及各字段在官方多语言内容里出现过的最大长度。

与旧版（手写白名单）相比修正了：
- ACTI/FLOR/INFO 的 RNAM 确实是文本（旧版误判为资源引用而跳过）；
- FACT 的 FNAM/MNAM、PERK 的 EPFD/EPF2、REGN 的 RDMP、BPTD 的 BPTN 等；
- DESC 在任意记录上都按长字符串处理（不再只翻白名单里的几种记录）。
"""

from __future__ import annotations

from typing import Dict, Optional, Set, Tuple

# (记录签名, 子记录签名) -> 字符串表类型（本地化插件用；内嵌插件统一是 zstring）
FIELD_KINDS: Dict[Tuple[str, str], str] = {
    ("MGEF", "DNAM"): "strings",
    ("INFO", "NAM1"): "ilstrings",
    ("INFO", "RNAM"): "strings",
    ("NPC_", "SHRT"): "strings",
    ("QUST", "CNAM"): "dlstrings",
    ("BOOK", "CNAM"): "dlstrings",
    ("WOOP", "TNAM"): "strings",
    ("QUST", "NNAM"): "strings",
    ("MESG", "ITXT"): "strings",
    ("REGN", "RDMP"): "strings",
    ("ACTI", "RNAM"): "strings",
    ("FLOR", "RNAM"): "strings",
    ("BPTD", "BPTN"): "strings",
    ("FACT", "MNAM"): "strings",
    ("FACT", "FNAM"): "strings",
    ("LSCR", "DESC"): "strings",
    ("GMST", "DATA"): "strings",  # 仅字符串型 GMST（pipeline 里有额外判断）
    ("PERK", "EPFD"): "strings",
    ("PERK", "EPF2"): "strings",
}

# 所有记录通用的字段（xTranslator 里 FULL=**** / DESC=**** 的 fallback）
DEFAULT_FIELDS: Dict[str, str] = {
    "FULL": "strings",
    "DESC": "dlstrings",
}

# 明确不翻译（防止极端情况下误伤；正常情况下根本进不了上面的白名单）
NEVER_TRANSLATE: Set[bytes] = {
    b"EDID", b"MODL", b"MODT", b"MODS", b"MOD2", b"MOD3", b"MOD4", b"MOD5",
    b"ICON", b"MICO", b"ICO2", b"NIF", b"KFF", b"SNDD", b"BNAM", b"ANAM",
    b"MAST", b"ONAM", b"INCC", b"INFX", b"VMAD", b"SCRI", b"NAM9",
}

# 这些字段是「游戏枚举文本」（是的/否/继续…），必须全包一致，xTranslator 会给出警告
ENUMERATED_FIELDS: Set[Tuple[str, str]] = {
    ("MESG", "ITXT"),
    ("QUST", "NNAM"),
    ("INFO", "NAM1"),
    ("QUST", "CNAM"),
}

# 字段长度参考值：官方多语言内容里出现过的最大字符数（用于质检提示，不是硬限制）
# 0 表示不限（书籍、描述等长文本）
FIELD_MAX_LEN: Dict[Tuple[str, str], int] = {
    ("ACTI", "FULL"): 47, ("ACTI", "RNAM"): 36,
    ("ALCH", "FULL"): 43,
    ("AMMO", "DESC"): 105, ("AMMO", "FULL"): 41,
    ("APPA", "FULL"): 30,
    ("ARMO", "DESC"): 165, ("ARMO", "FULL"): 65,
    ("AVIF", "DESC"): 214, ("AVIF", "FULL"): 27,
    ("BOOK", "CNAM"): 80, ("BOOK", "DESC"): 12000, ("BOOK", "FULL"): 62,
    ("BPTD", "BPTN"): 20,
    ("CELL", "FULL"): 73,
    ("CLAS", "FULL"): 38,
    ("CLFM", "FULL"): 26,
    ("COLL", "DESC"): 173,
    ("CONT", "FULL"): 54,
    ("DIAL", "FULL"): 129,
    ("DOOR", "FULL"): 39,
    ("ENCH", "FULL"): 50,
    ("EXPL", "FULL"): 37,
    ("EYES", "FULL"): 18,
    ("FACT", "FNAM"): 19, ("FACT", "FULL"): 101, ("FACT", "MNAM"): 44,
    ("FLOR", "FULL"): 32, ("FLOR", "RNAM"): 27,
    ("FURN", "FULL"): 30,
    ("GMST", "DATA"): 242,
    ("HAZD", "FULL"): 31,
    ("HDPT", "FULL"): 41,
    ("INFO", "NAM1"): 248, ("INFO", "RNAM"): 123,
    ("INGR", "FULL"): 36,
    ("KEYM", "FULL"): 58,
    ("LCTN", "FULL"): 50,
    ("LIGH", "FULL"): 16,
    ("LSCR", "DESC"): 386,
    ("MESG", "DESC"): 1950, ("MESG", "FULL"): 82, ("MESG", "ITXT"): 37,
    ("MGEF", "DNAM"): 204, ("MGEF", "FULL"): 80,
    ("MISC", "FULL"): 50,
    ("NPC_", "FULL"): 50, ("NPC_", "SHRT"): 21,
    ("PERK", "DESC"): 179, ("PERK", "EPF2"): 27, ("PERK", "EPFD"): 17,
    ("PERK", "FULL"): 39,
    ("PROJ", "FULL"): 41,
    ("QUST", "CNAM"): 628, ("QUST", "FULL"): 115, ("QUST", "NNAM"): 154,
    ("RACE", "DESC"): 361, ("RACE", "FULL"): 37,
    ("REFR", "FULL"): 50,
    ("REGN", "RDMP"): 14,
    ("SCRL", "DESC"): 131, ("SCRL", "FULL"): 43,
    ("SHOU", "DESC"): 129, ("SHOU", "FULL"): 38,
    ("SLGM", "FULL"): 31,
    ("SNCT", "FULL"): 42,
    ("SPEL", "DESC"): 230, ("SPEL", "FULL"): 58,
    ("TACT", "FULL"): 30,
    ("TREE", "FULL"): 26,
    ("WATR", "FULL"): 19,
    ("WEAP", "DESC"): 155, ("WEAP", "FULL"): 52,
    ("WOOP", "FULL"): 22, ("WOOP", "TNAM"): 14,
    ("WRLD", "FULL"): 50,
}


def _sig(value) -> str:
    return value.decode("latin-1") if isinstance(value, (bytes, bytearray)) else value


def translate_field(record_sig, sub_sig) -> bool:
    """这个 (记录, 字段) 是不是给人看的文本？"""
    rec = _sig(record_sig)
    field = _sig(sub_sig)
    if (rec, field) in FIELD_KINDS:
        return True
    if field in DEFAULT_FIELDS:
        return True
    return False


def field_kind(record_sig, sub_sig) -> Optional[str]:
    rec = _sig(record_sig)
    field = _sig(sub_sig)
    if (rec, field) in FIELD_KINDS:
        return FIELD_KINDS[(rec, field)]
    return DEFAULT_FIELDS.get(field)


def max_length(record_sig, sub_sig) -> int:
    return FIELD_MAX_LEN.get((_sig(record_sig), _sig(sub_sig)), 0)


def is_enumerated(record_sig, sub_sig) -> bool:
    return (_sig(record_sig), _sig(sub_sig)) in ENUMERATED_FIELDS


def always_translate_sigs() -> Set[str]:
    return set(DEFAULT_FIELDS)
