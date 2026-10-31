"""扫描整合包目录：插件、字符串表、MCM 翻译文件、BSA。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .bsa import Bsa, BsaEntry, BsaError
from .esp import PluginError, peek_header
from .strings import StringTable

# 官方主文件：默认不翻译（文本量巨大、且随游戏语言包走）
VANILLA_PLUGINS = {
    "skyrim.esm",
    "update.esm",
    "dawnguard.esm",
    "hearthfires.esm",
    "dragonborn.esm",
    "_resourcepack.esm",
}
VANILLA_PREFIXES = ("cc", "_resourcepack")

# 这些目录里不会有插件/文本，跳过以加快扫描
PRUNE_DIRS = {
    "meshes",
    "textures",
    "sound",
    "music",
    "video",
    "videos",
    "shaders",
    "seq",
    "lodsettings",
    "grass",
    "lod",
    "scripts",
    "source",
    "calientetools",
    "fomod",
    "docs",
    "__pycache__",
    ".git",
    "node_modules",
}

# 找脚本时也要进 Scripts 目录（其它重资源目录照旧跳过）
SCRIPT_PRUNE_DIRS = PRUNE_DIRS - {"scripts", "source", "docs"}

TABLE_RE = re.compile(
    r"^(?P<base>.+)_(?P<lang>[A-Za-z]{2,20})\.(?P<kind>strings|dlstrings|ilstrings)$",
    re.IGNORECASE,
)


def is_vanilla(name: str) -> bool:
    low = name.lower()
    if low in VANILLA_PLUGINS:
        return True
    return low.startswith(VANILLA_PREFIXES)


@dataclass
class PluginInfo:
    path: Path
    name: str  # 带扩展名
    base: str  # 小写、不含扩展名
    localized: bool
    size: int = 0

    @property
    def kind(self) -> str:
        return "localized" if self.localized else "inline"


@dataclass
class TableRef:
    """一个字符串表（松散文件或 BSA 内条目）。"""

    name: str  # 原始文件名，如 SkyUI_english.STRINGS
    base: str
    lang: str
    kind: str  # strings / dlstrings / ilstrings
    path: Optional[Path] = None
    archive: Optional[Path] = None
    entry: Optional[BsaEntry] = None

    def load(self) -> StringTable:
        if self.path is not None:
            return StringTable.load(self.path)
        if self.archive is None or self.entry is None:
            raise FileNotFoundError(self.name)
        with Bsa(self.archive) as bsa:
            target = None
            for candidate in bsa.entries:
                if candidate.path == self.entry.path:
                    target = candidate
                    break
            if target is None:
                target = self.entry
            data = bsa.read(target)
        return StringTable.from_bytes(self.name, data)

    @property
    def origin(self) -> str:
        if self.path is not None:
            return str(self.path)
        return "%s :: %s" % (self.archive, self.entry.path if self.entry else "?")


@dataclass
class Pack:
    root: Path
    plugins: List[PluginInfo] = field(default_factory=list)
    tables: Dict[str, List[TableRef]] = field(default_factory=dict)
    mcm: List[Path] = field(default_factory=list)
    archives: List[Path] = field(default_factory=list)
    scripts: List[Path] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    _bsa_index: Optional[Dict[str, TableRef]] = None
    _bsa_scripts: Optional[Dict[Path, List[BsaEntry]]] = None

    # ---------- 查询 ----------
    def tables_for(self, plugin: PluginInfo, prefer_lang: str = "english") -> List[TableRef]:
        loose = [t for t in self.tables.get(plugin.base, []) if t.path is not None]
        found = _pick_language(loose, prefer_lang)
        if found:
            return found
        bsa_tables = self.bsa_tables_for(plugin.base)
        return _pick_language(bsa_tables, prefer_lang)

    def bsa_tables_for(self, base: str, prefer_lang: str = "english") -> List[TableRef]:
        index = self._get_bsa_index()
        return _pick_language([t for t in index.get(base, [])], prefer_lang)

    def bsa_scripts(self) -> Dict[Path, List[BsaEntry]]:
        """BSA 里的 .pex：{归档路径: [条目]}"""
        self._get_bsa_index()
        return self._bsa_scripts or {}

    def _get_bsa_index(self) -> Dict[str, List[TableRef]]:
        if self._bsa_index is not None:
            return self._bsa_index
        index: Dict[str, List[TableRef]] = {}
        scripts: Dict[Path, List[BsaEntry]] = {}
        for archive in self.archives:
            try:
                bsa = Bsa(archive)
            except (BsaError, OSError) as exc:
                self.warnings.append("BSA 解析失败（已跳过）: %s (%s)" % (archive.name, exc))
                continue
            for entry in bsa.entries:
                if entry.path.endswith(".pex"):
                    scripts.setdefault(archive, []).append(entry)
                    continue
                match = TABLE_RE.match(entry.basename)
                if not match:
                    continue
                ref = TableRef(
                    name=entry.basename,
                    base=match.group("base").lower(),
                    lang=match.group("lang").lower(),
                    kind=match.group("kind").lower(),
                    archive=archive,
                    entry=entry,
                )
                index.setdefault(ref.base, []).append(ref)
            bsa.close()
        self._bsa_index = index
        self._bsa_scripts = scripts
        return index


def _pick_language(refs: List[TableRef], prefer: str) -> List[TableRef]:
    """优先取指定语言；否则取任意一套完整的表。"""
    if not refs:
        return []
    by_lang: Dict[str, List[TableRef]] = {}
    for ref in refs:
        by_lang.setdefault(ref.lang, []).append(ref)
    if prefer in by_lang:
        return by_lang[prefer]
    # 按 strings > dlstrings > ilstrings 的完整度挑一套
    best = max(by_lang.values(), key=lambda items: len({i.kind for i in items}))
    return best


def scan_pack(root: Path, use_bsa: bool = True, log=print) -> Pack:
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError("目录不存在: %s" % root)

    pack = Pack(root=root)
    seen_plugins: Dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d.lower() not in PRUNE_DIRS and not d.startswith(".")
        )
        here = Path(dirpath)
        for filename in sorted(filenames):
            low = filename.lower()
            ext = os.path.splitext(low)[1]
            full = here / filename
            if ext in (".esp", ".esm", ".esl"):
                if is_vanilla(filename):
                    continue
                match = TABLE_RE  # 复用正则判断表名用不到，这里仅取 stem
                base = os.path.splitext(filename)[0].lower()
                if base in seen_plugins:
                    pack.warnings.append(
                        "同名插件出现多次（后者会覆盖前者）: %s" % filename
                    )
                seen_plugins[base] = full
                try:
                    _sig, _flags, localized = peek_header(full)
                except (PluginError, OSError) as exc:
                    pack.warnings.append("插件头读取失败（已跳过）: %s (%s)" % (filename, exc))
                    continue
                pack.plugins.append(
                    PluginInfo(
                        path=full,
                        name=filename,
                        base=base,
                        localized=localized,
                        size=full.stat().st_size,
                    )
                )
            elif ext in (".strings", ".dlstrings", ".ilstrings"):
                match = TABLE_RE.match(filename)
                if not match:
                    continue
                ref = TableRef(
                    name=filename,
                    base=match.group("base").lower(),
                    lang=match.group("lang").lower(),
                    kind=match.group("kind").lower(),
                    path=full,
                )
                pack.tables.setdefault(ref.base, []).append(ref)
            elif ext == ".bsa" and use_bsa:
                pack.archives.append(full)
            elif ext == ".txt" and here.name.lower() == "translations":
                if "interface" in {p.lower() for p in here.parts}:
                    pack.mcm.append(full)

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d.lower() not in SCRIPT_PRUNE_DIRS and not d.startswith(".")
        )
        here = Path(dirpath)
        for filename in filenames:
            if filename.lower().endswith(".pex"):
                pack.scripts.append(here / filename)

    log(
        "扫描完成：%d 个插件（本地化 %d / 内嵌 %d），%d 个字符串表，%d 个 MCM 文件，%d 个 BSA，%d 个脚本"
        % (
            len(pack.plugins),
            sum(1 for p in pack.plugins if p.localized),
            sum(1 for p in pack.plugins if not p.localized),
            sum(len(v) for v in pack.tables.values()),
            len(pack.mcm),
            len(pack.archives),
            len(pack.scripts),
        )
    )
    return pack
