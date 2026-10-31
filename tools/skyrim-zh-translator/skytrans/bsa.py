"""BSA 归档只读支持（v103 Oblivion / v104 Skyrim LE / v105 Skyrim SE）。

只读取、不写回：汉化结果一律以「松散文件」输出，松散文件会覆盖 BSA 内的同名文件。

很多模组把 `_english.strings` 打进 BSA，必须先取出来才能翻译。
LZ4 解压为纯 Python 实现（SE 用的 LZ4 frame + 部分工具用的 block），无需第三方库。

内存友好：只读取文件名表，文件内容按需 seek 读取。
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List, Optional

LZ4_MAGIC = 0x184D2204


class BsaError(Exception):
    pass


# --------------------------------------------------------------------------
# LZ4（block / frame）
# --------------------------------------------------------------------------
def lz4_block_decompress_into(out: bytearray, src: bytes) -> None:
    pos = 0
    n = len(src)
    while pos < n:
        token = src[pos]
        pos += 1

        lit_len = token >> 4
        if lit_len == 15:
            while True:
                if pos >= n:
                    raise BsaError("LZ4 数据截断")
                b = src[pos]
                pos += 1
                lit_len += b
                if b != 255:
                    break
        if lit_len:
            if pos + lit_len > n:
                raise BsaError("LZ4 字面量越界")
            out += src[pos : pos + lit_len]
            pos += lit_len

        if pos >= n:
            break
        if pos + 2 > n:
            raise BsaError("LZ4 数据截断")

        offset = src[pos] | (src[pos + 1] << 8)
        pos += 2
        if offset == 0 or offset > len(out):
            raise BsaError("LZ4 匹配偏移非法")

        match_len = token & 0x0F
        if match_len == 15:
            while True:
                if pos >= n:
                    raise BsaError("LZ4 数据截断")
                b = src[pos]
                pos += 1
                match_len += b
                if b != 255:
                    break
        match_len += 4

        start = len(out) - offset
        for i in range(match_len):
            out.append(out[start + i])


def lz4_block_decompress(src: bytes, uncompressed_size: int = 0) -> bytes:
    out = bytearray()
    lz4_block_decompress_into(out, src)
    return bytes(out)


def lz4_frame_decompress(src: bytes) -> bytes:
    if len(src) < 7 or struct.unpack_from("<I", src, 0)[0] != LZ4_MAGIC:
        raise BsaError("不是 LZ4 frame")
    pos = 4
    flg = src[pos]
    pos += 1
    pos += 1  # BD
    if (flg >> 6) & 0x03 != 1:
        raise BsaError("不支持的 LZ4 frame 版本")
    block_checksum = bool((flg >> 4) & 1)
    content_size_flag = bool((flg >> 3) & 1)
    dict_id_flag = bool(flg & 1)
    if content_size_flag:
        pos += 8
    if dict_id_flag:
        pos += 4
    pos += 1  # HC

    out = bytearray()
    while pos + 4 <= len(src):
        size = struct.unpack_from("<I", src, pos)[0]
        pos += 4
        if size == 0:
            break
        raw_block = bool(size & 0x80000000)
        size &= 0x7FFFFFFF
        block = src[pos : pos + size]
        if len(block) < size:
            raise BsaError("LZ4 块截断")
        pos += size
        if raw_block:
            out += block
        else:
            lz4_block_decompress_into(out, block)
        if block_checksum:
            pos += 4
    return bytes(out)


def _decompress_lz4(data: bytes, original_size: int) -> bytes:
    try:
        return lz4_frame_decompress(data)
    except Exception:
        pass
    out = lz4_block_decompress(data, original_size)
    if original_size and len(out) != original_size:
        raise BsaError("LZ4 解压长度不符")
    return out


def _decompress_legacy(data: bytes) -> bytes:
    for wbits in (15, -15, 47):
        try:
            return zlib.decompress(data, wbits)
        except zlib.error:
            continue
    raise BsaError("zlib 解压失败")


# --------------------------------------------------------------------------
# BSA
# --------------------------------------------------------------------------
@dataclass
class BsaEntry:
    path: str  # 小写，反斜杠分隔
    size: int
    offset: int
    compressed: bool

    @property
    def basename(self) -> str:
        return self.path.rsplit("\\", 1)[-1]


class Bsa:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.version = 0
        self.flags = 0
        self.entries: List[BsaEntry] = []
        self._embedded_names = False
        self._fh = None
        try:
            self._open()
        except Exception:
            self.close()
            raise

    def _open(self) -> None:
        try:
            self._fh = open(self.path, "rb")
        except OSError as exc:
            raise BsaError(str(exc)) from exc

        header = self._fh.read(36)
        if len(header) < 36 or header[:4] != b"BSA\x00":
            raise BsaError("不是 BSA: %s" % self.path)
        version = struct.unpack_from("<I", header, 4)[0]
        if version not in (103, 104, 105):
            raise BsaError("不支持的 BSA 版本 %d" % version)
        dir_offset = struct.unpack_from("<I", header, 8)[0]
        flags = struct.unpack_from("<I", header, 12)[0]
        dir_count = struct.unpack_from("<I", header, 16)[0]
        file_count = struct.unpack_from("<I", header, 20)[0]
        folder_name_len = struct.unpack_from("<I", header, 24)[0]
        file_name_len = struct.unpack_from("<I", header, 28)[0]

        stride = 24 if version == 105 else 16
        est = dir_count * (stride + 1) + folder_name_len + file_count * 16 + file_name_len + 64
        self._fh.seek(dir_offset)
        table = self._fh.read(est)

        self.version = version
        self.flags = flags
        self._embedded_names = bool(flags & 0x100) and version >= 104
        archive_compressed = bool(flags & 0x4)
        has_names = bool(flags & 0x2)

        pos = 0
        counts = []
        for _ in range(dir_count):
            counts.append(struct.unpack_from("<I", table, pos + 8)[0])
            pos += stride

        entries: List[BsaEntry] = []
        try:
            for count in counts:
                name_len = table[pos]
                folder = table[pos + 1 : pos + name_len].decode("latin-1").rstrip("\x00")
                pos += name_len + 1
                for _ in range(count):
                    raw_size = struct.unpack_from("<I", table, pos + 8)[0]
                    offset = struct.unpack_from("<I", table, pos + 12)[0]
                    toggle = bool(raw_size & 0x40000000)
                    entries.append(
                        BsaEntry(
                            path=folder.lower() + "\\",
                            size=raw_size & 0x3FFFFFFF,
                            offset=offset,
                            compressed=archive_compressed != toggle,
                        )
                    )
                    pos += 16
            if has_names:
                for entry in entries:
                    end = table.index(b"\x00", pos)
                    entry.path += table[pos:end].decode("latin-1").lower()
                    pos = end + 1
        except (IndexError, struct.error, ValueError) as exc:
            raise BsaError("记录表损坏: %s" % exc) from exc

        self.entries = entries

    # -- 读取 --
    def read(self, entry: BsaEntry) -> bytes:
        if self._fh is None:
            raise BsaError("归档已关闭")
        self._fh.seek(entry.offset)
        block = self._fh.read(entry.size)
        if len(block) < entry.size:
            raise BsaError("数据越界: %s" % entry.path)
        pos = 0
        if self._embedded_names:
            # 数据块开头是完整路径的 bstring（长度字节含结尾 0）
            pos += block[0] + 1
        if not entry.compressed:
            return block[pos:]
        original_size = struct.unpack_from("<I", block, pos)[0]
        pos += 4
        payload = block[pos:]
        if self.version == 105:
            return _decompress_lz4(payload, original_size)
        return _decompress_legacy(payload)

    def find(self, suffixes: Iterable[str]) -> List[BsaEntry]:
        wanted = tuple(s.lower() for s in suffixes)
        return [e for e in self.entries if e.path.endswith(wanted)]

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "Bsa":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def iter_archives(root: Path) -> Iterator[Path]:
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() == ".bsa":
            yield path
