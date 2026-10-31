"""Skyrim 插件（.esp/.esm/.esl）解析与回写。

设计原则：**只改字符串，其余字节一字不动**。
- 未修改的记录直接复用原始字节（保证输出文件与输入逐字节一致，不会破坏模组）。
- 修改过的记录重建子记录、更新大小；子记录长度 > 65535 时自动使用 XXXX 扩展。
- 压缩记录（flag 0x40000）重新压缩。
- GRUP 大小自动重算。

这样即使某个模组有非标准字段，也不会被我们“写坏”。
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Iterator, List, Optional, Sequence, Tuple

RECORD_HEADER = 24
GROUP_HEADER = 24

FLAG_MASTER = 0x00000001
FLAG_DELETED = 0x00000020
FLAG_LOCALIZED = 0x00000080
FLAG_COMPRESSED = 0x00040000

_ENC_ORDER = ("utf-8", "cp1252", "latin-1")


class PluginError(Exception):
    """插件文件结构异常（格式不支持或文件损坏）。"""


def _u16(buf: bytes, off: int) -> int:
    return struct.unpack_from("<H", buf, off)[0]


def _u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def decode_text(data: bytes) -> str:
    """解码 zstring / lstring 原始字节（自动去掉结尾 \\x00）。"""
    if data.endswith(b"\x00"):
        data = data[:-1]
    if not data:
        return ""
    for enc in _ENC_ORDER:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", "replace")


def encode_text(text: str) -> bytes:
    """以 UTF-8 写回并以 \\x00 结尾（Skyrim SE 的字符串为 UTF-8）。"""
    return text.encode("utf-8") + b"\x00"


@dataclass
class Subrecord:
    sig: bytes
    data: bytes
    dirty: bool = False

    @property
    def text(self) -> str:
        return decode_text(self.data)

    def set_text(self, value: str) -> None:
        new = encode_text(value)
        if new != self.data:
            self.data = new
            self.dirty = True

    def serialize(self) -> bytes:
        size = len(self.data)
        if size > 0xFFFF:
            # 使用 XXXX 扩展大小：XXXX 内含真实长度，真正的子记录长度写 0
            return (
                b"XXXX"
                + struct.pack("<H", 4)
                + struct.pack("<I", size)
                + self.sig
                + b"\x00\x00"
                + self.data
            )
        return self.sig + struct.pack("<H", size) + self.data


class Record:
    __slots__ = ("sig", "header_rest", "subs", "trailing", "raw", "_dirty")

    def __init__(
        self,
        sig: bytes,
        header_rest: bytes,
        subs: List[Subrecord],
        trailing: bytes = b"",
        raw: Optional[bytes] = None,
    ) -> None:
        self.sig = sig
        self.header_rest = header_rest  # flags(4) + formid(4) + 8 字节原样保留
        self.subs = subs
        self.trailing = trailing
        self.raw = raw
        self._dirty = False

    @property
    def flags(self) -> int:
        return _u32(self.header_rest, 0)

    @property
    def formid(self) -> int:
        return _u32(self.header_rest, 4)

    @property
    def dirty(self) -> bool:
        return self._dirty or any(s.dirty for s in self.subs)

    def mark_dirty(self) -> None:
        self._dirty = True

    def get(self, sig: bytes) -> Optional[Subrecord]:
        for sub in self.subs:
            if sub.sig == sig:
                return sub
        return None

    def serialize(self) -> bytes:
        if not self.dirty and self.raw is not None:
            return self.raw
        body = b"".join(s.serialize() for s in self.subs) + self.trailing
        if self.flags & FLAG_COMPRESSED:
            body = struct.pack("<I", len(body)) + zlib.compress(body, 9)
        return self.sig + struct.pack("<I", len(body)) + self.header_rest + body


class Group:
    __slots__ = ("header_rest", "items", "raw", "_dirty")

    def __init__(self, header_rest: bytes, items: List[object], raw: Optional[bytes] = None) -> None:
        self.header_rest = header_rest  # label(4) + grouptype(4) + 8 字节原样保留
        self.items = items
        self.raw = raw
        self._dirty = False

    @property
    def dirty(self) -> bool:
        if self._dirty:
            return True
        for item in self.items:
            if item.dirty:
                return True
        return False

    def serialize(self) -> bytes:
        if not self.dirty and self.raw is not None:
            return self.raw
        body = b"".join(item.serialize() for item in self.items)
        return b"GRUP" + struct.pack("<I", GROUP_HEADER + len(body)) + self.header_rest + body


def _parse_subrecords(data: bytes) -> Tuple[List[Subrecord], bytes]:
    subs: List[Subrecord] = []
    pos = 0
    end = len(data)
    while pos + 6 <= end:
        sig = data[pos : pos + 4]
        size = _u16(data, pos + 4)
        pos += 6
        if sig == b"XXXX" and size == 4 and pos + 4 <= end:
            real = _u32(data, pos)
            pos += 4
            if pos + 6 > end:
                break
            sig2 = data[pos : pos + 4]
            size2 = _u16(data, pos + 4)
            pos += 6
            if size2 != 0:
                # 非标准写法：以第二个长度为准
                real = size2
            chunk = data[pos : pos + real]
            pos += len(chunk)
            subs.append(Subrecord(sig2, chunk))
            continue
        if pos + size > end:
            pos -= 6
            break
        subs.append(Subrecord(sig, data[pos : pos + size]))
        pos += size
    return subs, data[pos:]


def _parse_record(buf: bytes, pos: int, end: int) -> Tuple[Record, int]:
    if pos + RECORD_HEADER > end:
        raise PluginError("记录头越界")
    sig = buf[pos : pos + 4]
    size = _u32(buf, pos + 4)
    if pos + RECORD_HEADER + size > end:
        raise PluginError("记录大小越界: %s @ %d" % (sig.decode("latin-1"), pos))
    rest = buf[pos + 8 : pos + RECORD_HEADER]
    raw = buf[pos : pos + RECORD_HEADER + size]
    data = buf[pos + RECORD_HEADER : pos + RECORD_HEADER + size]
    flags = _u32(rest, 0)
    if flags & FLAG_COMPRESSED:
        if len(data) < 4:
            raise PluginError("压缩记录缺少长度前缀")
        try:
            data = zlib.decompress(data[4:])
        except zlib.error as exc:
            raise PluginError("压缩记录解压失败: %s" % exc) from exc
    subs, trailing = _parse_subrecords(data)
    return Record(sig, rest, subs, trailing, raw=raw), pos + RECORD_HEADER + size


def _parse_items(buf: bytes, pos: int, end: int) -> Tuple[List[object], int]:
    items: List[object] = []
    while pos < end:
        if pos + 8 > end:
            break
        sig = buf[pos : pos + 4]
        if sig == b"GRUP":
            size = _u32(buf, pos + 4)
            if size < GROUP_HEADER or pos + size > end:
                raise PluginError("GRUP 大小异常 @ %d" % pos)
            rest = buf[pos + 8 : pos + GROUP_HEADER]
            children, _ = _parse_items(buf, pos + GROUP_HEADER, pos + size)
            items.append(Group(rest, children, raw=buf[pos : pos + size]))
            pos += size
        else:
            rec, pos = _parse_record(buf, pos, end)
            items.append(rec)
    return items, pos


class Plugin:
    """一个 .esp/.esm/.esl 文件。"""

    def __init__(self, path, header: Record, items: List[object]) -> None:
        self.path = path
        self.header = header
        self.items = items

    # ---------- 读取 ----------
    @classmethod
    def load(cls, path) -> "Plugin":
        buf = path.read_bytes()
        if len(buf) < RECORD_HEADER or buf[:4] != b"TES4":
            raise PluginError("不是有效的 Skyrim 插件: %s" % path)
        header, pos = _parse_record(buf, 0, len(buf))
        items, _ = _parse_items(buf, pos, len(buf))
        return cls(path, header, items)

    @property
    def localized(self) -> bool:
        return bool(self.header.flags & FLAG_LOCALIZED)

    @property
    def masters(self) -> List[str]:
        out = []
        for sub in self.header.subs:
            if sub.sig == b"MAST":
                out.append(sub.text)
        return out

    def iter_records(self) -> Iterator[Record]:
        def walk(items):
            for item in items:
                if isinstance(item, Group):
                    yield from walk(item.items)
                else:
                    yield item

        yield from walk(self.items)

    # ---------- 写出 ----------
    def serialize(self) -> bytes:
        return self.header.serialize() + b"".join(item.serialize() for item in self.items)

    def save(self, path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.serialize())

    @property
    def dirty(self) -> bool:
        return self.header.dirty or any(item.dirty for item in self.items)


def peek_header(path) -> Tuple[bytes, int, bool]:
    """快速读取文件头，返回 (记录签名, flags, 是否本地化)，不做完整解析。"""
    with open(path, "rb") as fh:
        head = fh.read(RECORD_HEADER)
    if len(head) < RECORD_HEADER:
        raise PluginError("文件过小: %s" % path)
    return head[:4], _u32(head, 8), bool(_u32(head, 8) & FLAG_LOCALIZED)
