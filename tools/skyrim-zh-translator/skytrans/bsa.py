"""BSA 归档读写（v103 Oblivion / v104 Skyrim LE / v105 Skyrim SE）。

读取：很多模组把 `_english.strings`、`.pex` 打进 BSA，必须先取出来才能翻译。
内存友好：只读取文件名表，文件内容按需 seek 读取。

回写（v1.2）：`Bsa.rewrite()` 把归档重打包到**新文件**（绝不原地覆盖，除非调用方显式要求），
未替换的条目按原字节搬运、替换的条目按归档原本的压缩方式重新压缩，布局沿用原档
（目录顺序、文件名顺序、hash 全部不变），最后默认重新打开新档逐条比对内容。

LZ4（frame + block）的解压与压缩都是纯 Python 实现，无需第三方库；
`lz4_frame_compress` 的输出已用官方 lz4 库交叉校验（字节流可被 `lz4.frame.decompress` 解开）。
"""

from __future__ import annotations

import os
import struct
import tempfile
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
    hash: int = 0
    folder: str = ""
    toggle: bool = False  # 记录里的 0x40000000 位（仅在压缩存档里表示「本文件未压缩」）

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
        self._folder_records: List[dict] = []
        self._names_blob = b""
        self._stride = 16
        self._file_flags = 0
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
        folders: List[dict] = []
        for i in range(dir_count):
            rec = table[i * stride : (i + 1) * stride]
            count = struct.unpack_from("<I", rec, 8)[0]
            if stride == 24:
                stored = struct.unpack_from("<Q", rec, 16)[0]
            else:
                stored = struct.unpack_from("<I", rec, 12)[0]
            folders.append({"raw": rec, "count": count, "stored": stored})

        entries: List[BsaEntry] = []
        pos = dir_count * stride
        try:
            for index, folder_rec in enumerate(folders):
                folder_rec["start"] = dir_offset + pos
                name_len = table[pos]
                name_bytes = table[pos + 1 : pos + name_len]
                folder = name_bytes.decode("latin-1")
                folder_rec["name"] = name_bytes
                pos += name_len + 1
                for _ in range(folder_rec["count"]):
                    file_hash = struct.unpack_from("<Q", table, pos)[0]
                    raw_size = struct.unpack_from("<I", table, pos + 8)[0]
                    offset = struct.unpack_from("<I", table, pos + 12)[0]
                    toggle = bool(raw_size & 0x40000000)
                    entries.append(
                        BsaEntry(
                            path=folder.lower() + "\\",
                            size=raw_size & 0x3FFFFFFF,
                            offset=offset,
                            compressed=bool(archive_compressed and not toggle),
                            hash=file_hash,
                            toggle=toggle,
                            folder=folder,
                        )
                    )
                    pos += 16
            names_start = dir_offset + pos
            if has_names:
                for entry in entries:
                    end = table.index(b"\x00", pos)
                    entry.path += table[pos:end].decode("latin-1").lower()
                    pos = end + 1
        except (IndexError, struct.error, ValueError) as exc:
            raise BsaError("记录表损坏: %s" % exc) from exc

        self.entries = entries
        self._folder_records = folders
        self._stride = stride
        rel_names = names_start - dir_offset
        self._names_blob = table[rel_names:rel_names + file_name_len]
        self._data_start = names_start + file_name_len
        self._dir_offset = dir_offset
        self._header = header
        self._archive_compressed = archive_compressed
        self._file_flags = struct.unpack_from("<I", header, 32)[0]

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

    # ------------------------------------------------------------------
    # 回写：重打包
    # ------------------------------------------------------------------
    def rewrite(
        self,
        dst: Path,
        replacements: Optional[dict] = None,
        verify: bool = True,
    ) -> dict:
        """把归档重写到 dst（不原地覆盖）。

        replacements: {归档内路径: 新的未压缩内容}，路径大小写/斜杠不敏感。
        未替换的条目按原字节搬运；替换的条目按归档原本的压缩方式重新压缩。
        完成后默认重开 dst 校验每个条目内容，失败抛 BsaError。
        """
        if self._fh is None:
            raise BsaError("归档已关闭")
        dst = Path(dst)
        norm = {}
        for key, value in (replacements or {}).items():
            norm[key.lower().replace("/", "\\")] = value
        known = {e.path for e in self.entries}
        missing = sorted(set(norm) - known)
        if missing:
            raise BsaError("归档内不存在这些文件: %s" % ", ".join(missing[:5]))

        total = sum(f["count"] for f in self._folder_records)
        if total != len(self.entries):
            raise BsaError(
                "目录记录与条目数不一致（%d vs %d），拒绝写回" % (total, len(self.entries))
            )
        archive_compressed = self._archive_compressed
        # 1) 目录块 / 名称块 / 数据区的位置（布局与原档一致）
        cursor = self._dir_offset + len(self._folder_records) * self._stride
        block_starts = []
        for folder in self._folder_records:
            block_starts.append(cursor)
            cursor += 1 + len(folder["name"]) + 1 + 16 * folder["count"]
        names_start = cursor
        data_start = names_start + len(self._names_blob)

        # 2) 计算每个条目的数据块
        index = 0
        layout = []  # 每个条目: dict(size_field, offset, payload)
        offset = data_start
        for folder in self._folder_records:
            rows = []
            for _ in range(folder["count"]):
                entry = self.entries[index]
                index += 1
                if entry.path in norm:
                    content = norm[entry.path]
                    body = content
                    if self._embedded_names:
                        name = entry.path.encode("latin-1", "replace")
                        body = bytes([len(name) + 1]) + name + b"\x00" + content
                    if archive_compressed:
                        packed = (
                            lz4_frame_compress(body)
                            if self.version >= 105
                            else zlib.compress(body, 9)
                        )
                        block = struct.pack("<I", len(body)) + packed
                    else:
                        block = body
                    # 未压缩存档的 size 位必须为 0（第三方工具按此判断），
                    # 只有在压缩存档里「本文件不压缩」时才置位。
                    size_field = len(block)
                    payload = block
                else:
                    size_field = entry.size | (0x40000000 if entry.toggle else 0)
                    payload = None  # 原样拷贝
                rows.append(
                    {
                        "entry": entry,
                        "size_field": size_field,
                        "offset": offset,
                        "payload": payload,
                    }
                )
                offset += len(payload) if payload is not None else entry.size
            layout.append(rows)

        # 3) 写临时文件，成功后再替换目标，避免写坏原档
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_name = tempfile.mkstemp(prefix=".bsa-tmp-", dir=str(dst.parent))
        replaced = 0
        try:
            with os.fdopen(tmp_fd, "wb") as out:
                out.write(self._header)
                for folder, rows, start in zip(self._folder_records, layout, block_starts):
                    rec = bytearray(folder["raw"])
                    stored = start + len(self._names_blob)
                    if self._stride == 24:
                        struct.pack_into("<Q", rec, 16, stored)
                    else:
                        struct.pack_into("<I", rec, 12, stored)
                    out.write(rec)
                for folder, rows in zip(self._folder_records, layout):
                    out.write(bytes([len(folder["name"]) + 1]))
                    out.write(folder["name"])
                    out.write(b"\x00")
                    for row in rows:
                        entry = row["entry"]
                        out.write(
                            struct.pack(
                                "<QII", entry.hash, row["size_field"], row["offset"]
                            )
                        )
                out.write(self._names_blob)
                for rows in layout:
                    for row in rows:
                        if row["payload"] is not None:
                            out.write(row["payload"])
                            replaced += 1
                        else:
                            self._fh.seek(row["entry"].offset)
                            left = row["entry"].size
                            while left:
                                chunk = self._fh.read(min(left, 1 << 20))
                                if not chunk:
                                    raise BsaError("源数据截断: %s" % row["entry"].path)
                                out.write(chunk)
                                left -= len(chunk)
            try:
                # 保持与原档一致的权限（Windows 上无效但不报错）
                mode = self.path.stat().st_mode & 0o777 if self.path.exists() else 0o644
                os.chmod(tmp_name, mode)
            except OSError:
                pass
            os.replace(tmp_name, str(dst))
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

        # 4) 自检：重新打开新档，逐个比对内容
        if verify:
            with Bsa(dst) as check:
                fresh = {e.path: e for e in check.entries}
                if len(fresh) != len(self.entries):
                    raise BsaError("回写后条目数不一致")
                for row in (r for rows in layout for r in rows):
                    entry = row["entry"]
                    target = fresh.get(entry.path)
                    if target is None:
                        raise BsaError("回写后丢失条目: %s" % entry.path)
                    want = norm[entry.path] if entry.path in norm else self.read(entry)
                    if check.read(target) != want:
                        raise BsaError("回写校验失败: %s" % entry.path)
        return {
            "entries": len(self.entries),
            "replaced": replaced,
            "compressed": archive_compressed,
            "size": dst.stat().st_size,
        }

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


# --------------------------------------------------------------------------
# LZ4 frame 压缩（纯 Python，仅供 BSA 回写使用）
# --------------------------------------------------------------------------
def _rotl32(x: int, r: int) -> int:
    return ((x << r) | (x >> (32 - r))) & 0xFFFFFFFF


def xxh32(data: bytes, seed: int = 0) -> int:
    P1, P2, P3, P4, P5 = 2654435761, 2246822519, 3266489917, 668265263, 374761393
    M = 0xFFFFFFFF
    n = len(data)
    pos = 0
    if n >= 16:
        v1 = (seed + P1 + P2) & M
        v2 = (seed + P2) & M
        v3 = seed & M
        v4 = (seed - P1) & M

        def rnd(acc: int, lane: int) -> int:
            acc = (acc + lane * P2) & M
            acc = _rotl32(acc, 13)
            return (acc * P1) & M

        while pos + 16 <= n:
            v1 = rnd(v1, struct.unpack_from("<I", data, pos)[0])
            v2 = rnd(v2, struct.unpack_from("<I", data, pos + 4)[0])
            v3 = rnd(v3, struct.unpack_from("<I", data, pos + 8)[0])
            v4 = rnd(v4, struct.unpack_from("<I", data, pos + 12)[0])
            pos += 16
        h = (_rotl32(v1, 1) + _rotl32(v2, 7) + _rotl32(v3, 12) + _rotl32(v4, 18)) & M
    else:
        h = (seed + P5) & M
    h = (h + n) & M
    while pos + 4 <= n:
        h = (h + struct.unpack_from("<I", data, pos)[0] * P3) & M
        h = (_rotl32(h, 17) * P4) & M
        pos += 4
    while pos < n:
        h = (h + data[pos] * P5) & M
        h = (_rotl32(h, 11) * P1) & M
        pos += 1
    h ^= h >> 15
    h = (h * P2) & M
    h ^= h >> 13
    h = (h * P3) & M
    h ^= h >> 16
    return h & M


def lz4_block_compress(src: bytes, min_match: int = 4) -> bytes:
    """贪心哈希表压缩，产出标准 LZ4 block（末 5 字节必为字面量，可被 lz4.block 解开）。"""
    n = len(src)
    out = bytearray()
    table = {}
    anchor = 0
    i = 0
    match_limit = n - 12  # 标准要求最后一个匹配至少在块末 12 字节前开始

    def emit_length(extra: int) -> None:
        while extra >= 255:
            out.append(255)
            extra -= 255
        out.append(extra)

    def emit_sequence(lit_start: int, lit_end: int, offset: int, match_len: int) -> None:
        lit_len = lit_end - lit_start
        ml = match_len - min_match
        out.append(((lit_len if lit_len < 15 else 15) << 4) | (ml if ml < 15 else 15))
        if lit_len >= 15:
            emit_length(lit_len - 15)
        out.extend(src[lit_start:lit_end])
        out.extend(struct.pack("<H", offset))
        if ml >= 15:
            emit_length(ml - 15)

    while i < match_limit:
        key = src[i : i + 4]
        cand = table.get(key)
        table[key] = i
        if cand is None or i - cand > 65535:
            i += 1
            continue
        max_len = n - 5 - i
        length = min_match
        while length < max_len and src[cand + length] == src[i + length]:
            length += 1
        if length < min_match or length > max_len:
            i += 1
            continue
        emit_sequence(anchor, i, i - cand, length)
        i += length
        anchor = i

    # 收尾：剩下的全部作为字面量
    lit_len = n - anchor
    out.append((lit_len if lit_len < 15 else 15) << 4)
    if lit_len >= 15:
        emit_length(lit_len - 15)
    out.extend(src[anchor:])
    return bytes(out)


def lz4_frame_compress(data: bytes, block_size: int = 1 << 16) -> bytes:
    """产出标准 LZ4 frame（独立块、无校验），与 Skyrim SE 的 BSA 兼容。"""
    code = {1 << 16: 4, 1 << 18: 5, 1 << 20: 6, 1 << 22: 7}[block_size]
    flg = 0x60  # version=01, block independence=1
    bd = code << 4
    header = struct.pack("<I", LZ4_MAGIC) + bytes([flg, bd])
    header += bytes([(xxh32(bytes([flg, bd])) >> 8) & 0xFF])
    out = bytearray(header)
    for pos in range(0, len(data), block_size):
        chunk = data[pos : pos + block_size]
        packed = lz4_block_compress(chunk)
        if len(packed) >= len(chunk):
            out += struct.pack("<I", len(chunk) | 0x80000000)
            out += chunk
        else:
            out += struct.pack("<I", len(packed))
            out += packed
    out += struct.pack("<I", 0)
    return bytes(out)
