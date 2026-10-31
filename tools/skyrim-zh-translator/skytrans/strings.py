"""本地化插件使用的字符串表：.STRINGS / .DLSTRINGS / .ILSTRINGS。

格式（Skyrim LE / SE 相同）：
- .strings    : 每条为 UTF-8 文本 + \\x00，按顺序编号（= 插件里引用的 ID）
- .dlstrings  : 每条为 uint32 长度（含结尾 \\x00）+ 文本 + \\x00
- .ilstrings  : 同 .dlstrings（INFO 对话文本）

我们保持条目数量和顺序完全不变，只替换内容，因此 ID 引用不会错乱。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from .esp import decode_text

KIND_BY_SUFFIX = {
    ".strings": "strings",
    ".dlstrings": "dlstrings",
    ".ilstrings": "ilstrings",
}


@dataclass
class StringTable:
    path: Path
    kind: str
    entries: List[bytes] = field(default_factory=list)
    dirty: bool = False

    # ---------- 读取 ----------
    @classmethod
    def load(cls, path: Path) -> "StringTable":
        raw = path.read_bytes()
        kind = KIND_BY_SUFFIX.get(path.suffix.lower())
        if kind is None:
            raise ValueError("不是字符串表文件: %s" % path)
        entries: List[bytes] = []
        if kind == "strings":
            parts = raw.split(b"\x00")
            if parts and parts[-1] == b"":
                parts.pop()
            entries = parts
        else:
            pos = 0
            end = len(raw)
            while pos + 4 <= end:
                size = struct.unpack_from("<I", raw, pos)[0]
                pos += 4
                if size == 0:
                    entries.append(b"")
                    continue
                chunk = raw[pos : pos + size]
                pos += size
                if chunk.endswith(b"\x00"):
                    chunk = chunk[:-1]
                entries.append(chunk)
        return cls(path=path, kind=kind, entries=entries)

    @classmethod
    def from_bytes(cls, name: str, raw: bytes) -> "StringTable":
        """从 BSA 中解出的字节构造（不落盘）。name 用于判断类型。"""
        path = Path(name)
        kind = KIND_BY_SUFFIX.get(path.suffix.lower(), "strings")
        entries: List[bytes] = []
        if kind == "strings":
            parts = raw.split(b"\x00")
            if parts and parts[-1] == b"":
                parts.pop()
            entries = parts
        else:
            pos = 0
            end = len(raw)
            while pos + 4 <= end:
                size = struct.unpack_from("<I", raw, pos)[0]
                pos += 4
                if size == 0:
                    entries.append(b"")
                    continue
                chunk = raw[pos : pos + size]
                pos += size
                if chunk.endswith(b"\x00"):
                    chunk = chunk[:-1]
                entries.append(chunk)
        return cls(path=path, kind=kind, entries=entries)

    # ---------- 文本 ----------
    def text(self, index: int) -> str:
        return decode_text(self.entries[index])

    def set_text(self, index: int, value: str) -> None:
        new = value.encode("utf-8")
        if new != self.entries[index]:
            self.entries[index] = new
            self.dirty = True

    # ---------- 写出 ----------
    def serialize(self) -> bytes:
        if self.kind == "strings":
            return b"".join(entry + b"\x00" for entry in self.entries)
        out = bytearray()
        for entry in self.entries:
            out += struct.pack("<I", len(entry) + 1)
            out += entry + b"\x00"
        return bytes(out)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.serialize())

    @property
    def target_suffix(self) -> str:
        return {"strings": ".STRINGS", "dlstrings": ".DLSTRINGS", "ilstrings": ".ILSTRINGS"}[
            self.kind
        ]
