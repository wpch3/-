"""哪些记录字段是「给人看的文本」——按记录类型的字段白名单。

非本地化插件里，字符串是直接内嵌在子记录中的 zstring，需要逐字段判断，
否则会把 .nif 路径、FormID、编辑器 ID 也当成文本翻译掉（会直接损坏模组）。

本地化插件不需要这张表：文本全在 .STRINGS 里，整表翻译即可。
"""

from __future__ import annotations

from typing import Dict, Set

# 记录签名 -> 可翻译子记录签名
RECORD_STRINGS: Dict[str, Set[str]] = {
    "ACTI": {"FULL"},
    "ALCH": {"FULL", "DESC"},
    "AMMO": {"FULL"},
    "ANIO": {"FULL"},
    "APPA": {"FULL", "DESC"},
    "ARMO": {"FULL"},
    "ARTO": {"FULL"},
    "ASPC": {"FULL"},
    "BOOK": {"FULL", "DESC", "CNAM"},
    "CELL": {"FULL"},
    "CLAS": {"FULL", "DESC"},
    "CLFM": {"FULL"},
    "CONT": {"FULL"},
    "DIAL": {"FULL"},
    "DOOR": {"FULL"},
    "ENCH": {"FULL"},
    "EXPL": {"FULL"},
    "EYES": {"FULL"},
    "FACT": {"FULL"},
    "FLOR": {"FULL"},
    "FLST": {"FULL"},
    "FURN": {"FULL"},
    "GMST": {"DATA"},  # 仅字符串型 GMST，见 pipeline 的特判
    "GRAS": {"FULL"},
    "HAZD": {"FULL"},
    "IDLE": {"FULL"},
    "INFO": {"NAM1", "RNAM"},
    "INGR": {"FULL"},
    "KEYM": {"FULL"},
    "LCTN": {"FULL"},
    "LENS": {"FULL"},
    "LIGH": {"FULL"},
    "LOAD": {"FULL", "DESC"},
    "MESG": {"FULL", "DESC", "ITXT"},
    "MGEF": {"FULL", "DESC"},
    "MISC": {"FULL"},
    "MOVT": {"FULL"},
    "MUSC": {"FULL"},
    "MUST": {"FULL"},
    "NAVM": {"FULL"},
    "NPC_": {"FULL", "SHRT"},
    "NOTE": {"FULL", "TNAM"},
    "PACK": {"FULL"},
    "PERK": {"FULL", "DESC"},
    "PROJ": {"FULL"},
    "QUST": {"FULL", "CNAM", "NNAM"},
    "RACE": {"FULL", "DESC"},
    "REFR": {"FULL"},
    "REGN": {"FULL"},
    "RELA": {"FULL"},
    "RFCT": {"FULL"},
    "SCEN": {"FULL"},
    "SCRL": {"FULL", "DESC"},
    "SHOU": {"FULL", "DESC"},
    "SLGM": {"FULL"},
    "SMBN": {"FULL"},
    "SMEN": {"FULL"},
    "SMQN": {"FULL"},
    "SOUN": {"FULL"},
    "SPEL": {"FULL", "DESC"},
    "STAT": {"FULL"},
    "TACT": {"FULL"},
    "TREE": {"FULL"},
    "TXST": {"FULL"},
    "VOLI": {"FULL"},
    "WATR": {"FULL"},
    "WEAP": {"FULL"},
    "WOOD": {"FULL"},
    "WRLD": {"FULL", "MNAM"},
    "WTHR": {"FULL"},
}

# 任何记录里都极可能是文本的字段（用于未登记记录类型的兜底，仍会做文本判断）
GENERIC_TEXT_SIGS: Set[str] = {"FULL", "DESC", "ITXT", "NAM1", "TNAM"}

# 绝对不翻译：路径、标识符、内部名称、哈希等
NEVER_TRANSLATE: Set[str] = {
    b"EDID",
    b"MODL",
    b"MODT",
    b"MODS",
    b"MOD2",
    b"MOD3",
    b"MOD4",
    b"MOD5",
    b"MO2S",
    b"MO3S",
    b"MO4S",
    b"MO5S",
    b"ICON",
    b"MICO",
    b"ICO2",
    b"NIF",
    b"KFF",
    b"SNDD",
    b"BNAM",
    b"ANAM",
    b"WNAM",
    b"RNAM",  # 多数记录里是音效/资源引用；INFO 的 RNAM 已在白名单中
    b"MAST",
    b"ONAM",
    b"INCC",
    b"INFX",
    b"DATA",  # 只有 GMST 的 DATA 例外
    b"VNAM",
    b"FNAM",
    b"GNAM",
    b"UNAM",
    b"JNAM",
    b"ENAM",
    b"XNAM",
    b"ZNAM",
    b"YNAM",
    b"TNAM",  # NOTE 的 TNAM 在记录白名单里，会先命中白名单
    b"PNAM",
    b"BTXT",
    b"ATXT",
    b"LNAM",
    b"QNAM",
    b"QOBJ",
}


def translatable_sigs(record_sig: str) -> Set[str]:
    """返回某个记录类型下的可翻译字段集合（含通用字段）。"""
    sig = record_sig.decode("latin-1") if isinstance(record_sig, bytes) else record_sig
    allowed = set(RECORD_STRINGS.get(sig, set()))
    if not allowed:
        allowed |= GENERIC_TEXT_SIGS
    return allowed
