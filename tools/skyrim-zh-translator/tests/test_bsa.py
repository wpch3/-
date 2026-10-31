"""BSA v105 解析测试（合成归档 + 真实 LZ4 frame 常量）。"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skytrans.bsa import (  # noqa: E402
    Bsa,
    BsaError,
    lz4_block_compress,
    lz4_block_decompress,
    lz4_frame_compress,
    lz4_frame_decompress,
    xxh32,
)
from skytrans.strings import StringTable  # noqa: E402

# 由 lz4 官方实现生成的 frame，内容为 132 字节的 MCM 文本（3 次重复）
FRAME_HEX = (
    "04224d1860408237000000ff1d244d6f644e616d65094d792054657374204d6f64"
    "0a24546f67676c6509456e61626c6520666561747572650a2c00405074757265"
    "0a00000000"
)
FRAME_TEXT = b"$ModName\tMy Test Mod\n$Toggle\tEnable feature\n" * 3


def _payload(data: bytes, compress: bool) -> bytes:
    """返回 (payload, 是否置 0x40000000 位)。

    真实约定：该位只在「压缩存档里本文件不压缩」时才置位；压缩存档里被压缩的
    文件该位必须为 0，未压缩存档里所有文件也都为 0。
    """
    if compress:
        return struct.pack("<I", len(data)) + lz4_frame_compress(data), False
    return data, False


def build_bsa(files, compress=False, embedded=False) -> bytes:
    """构造最小 v105 BSA。files: {r"strings\\x.strings": bytes}"""
    folders = {}
    for path, data in files.items():
        folder, name = path.rsplit("\\", 1)
        folders.setdefault(folder, []).append((name, data))

    dir_count = len(folders)
    file_count = sum(len(v) for v in folders.values())
    folder_name_len = sum(len(f) + 1 for f in folders)
    file_name_len = sum(len(n) + 1 for v in folders.values() for n, _ in v)

    flags = 0x3
    if compress:
        flags |= 0x4  # 压缩存档
    if embedded:
        flags |= 0x100

    header = (
        b"BSA\x00"
        + struct.pack("<I", 105)
        + struct.pack("<I", 36)
        + struct.pack("<I", flags)
        + struct.pack("<I", dir_count)
        + struct.pack("<I", file_count)
        + struct.pack("<I", folder_name_len)
        + struct.pack("<I", file_name_len)
        + struct.pack("<I", 0x100)
    )
    assert len(header) == 36

    # 先算好每个文件的数据块，才能填 offset
    payloads = []
    offsets = []
    for folder, items in folders.items():
        for name, data in items:
            payload, is_compressed = _payload(data, compress)
            if embedded:
                full = folder + "\\" + name
                payload = bytes([len(full) + 1]) + full.encode() + b"\x00" + payload
            payloads.append((payload, is_compressed))
            offsets.append(None)

    # 目录块起点（紧跟在目录记录之后），用于填真实的 name_offset
    header_len = 36
    record_len = dir_count * 24
    block_starts = []
    cursor = header_len + record_len
    for folder, items in folders.items():
        block_starts.append(cursor)
        cursor += 1 + len(folder) + 1 + 16 * len(items)

    folder_records = b""
    for folder, items, start in zip(folders, folders.values(), block_starts):
        folder_records += (
            struct.pack("<Q", 0)
            + struct.pack("<I", len(items))
            + struct.pack("<I", 0)
            + struct.pack("<Q", start + file_name_len)
        )

    blocks = bytearray()
    for folder, items in folders.items():
        blocks += bytes([len(folder) + 1]) + folder.encode() + b"\x00"
        for _ in items:
            blocks += struct.pack("<Q", 0) + struct.pack("<I", 0) + struct.pack("<I", 0)

    names = b"".join(n.encode() + b"\x00" for v in folders.values() for n, _ in v)
    data_start = 36 + len(folder_records) + len(blocks) + len(names)

    cursor = data_start
    for index, (payload, is_compressed) in enumerate(payloads):
        offsets[index] = (cursor, is_compressed)
        cursor += len(payload)

    # 回填 file records
    out = bytearray(blocks)
    pos = 0
    index = 0
    for folder, items in folders.items():
        pos += len(folder) + 2
        for _ in items:
            offset, is_compressed = offsets[index]
            size = len(payloads[index][0]) | (0x40000000 if is_compressed else 0)
            out[pos : pos + 16] = (
                struct.pack("<Q", 0) + struct.pack("<I", size) + struct.pack("<I", offset)
            )
            pos += 16
            index += 1

    return header + folder_records + bytes(out) + names + b"".join(p for p, _ in payloads)


class Lz4Tests(unittest.TestCase):
    def test_xxh32_vector(self):
        self.assertEqual(xxh32(b""), 0x02CC5D05)

    def test_frame_roundtrip(self):
        payload = (
            b"$ModName\tMy Test Mod\n$Toggle\tEnable feature\n" * 40
            + bytes(range(256)) * 8
        )
        for size in (0, 1, 17, 65535, 65536, 200000):
            data = payload[:size]
            self.assertEqual(lz4_frame_decompress(lz4_frame_compress(data)), data)

    def test_frame_starts_with_standard_header(self):
        frame = lz4_frame_compress(b"hello world" * 100)
        self.assertEqual(frame[:4], b"\x04\x22\x4d\x18")
        self.assertEqual(frame[4], 0x60)  # 版本 01 + 独立块
        self.assertEqual(frame[-4:], b"\x00\x00\x00\x00")

    def test_block_roundtrip(self):
        data = (b"papyrus" * 500) + os.urandom(2000)
        packed = lz4_block_compress(data)
        self.assertLess(len(packed), len(data))
        self.assertEqual(lz4_block_decompress(packed, len(data)), data)


class BsaTests(unittest.TestCase):
    def test_frame_constant_decodes(self):
        self.assertEqual(lz4_frame_decompress(bytes.fromhex(FRAME_HEX)), FRAME_TEXT)

    def test_read_uncompressed_entries(self):
        table = StringTable(
            path=Path("x"), kind="strings", entries=[b"Iron Sword", b"Steel Sword"]
        ).serialize()
        archive = build_bsa({"Strings\\Mod_english.STRINGS": table})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Mod.bsa"
            path.write_bytes(archive)
            bsa = Bsa(path)
            entries = bsa.find([".strings"])
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].basename.lower(), "mod_english.strings")
            data = bsa.read(entries[0])
            self.assertEqual(data, table)
            parsed = StringTable.from_bytes("Mod_english.STRINGS", data)
            self.assertEqual(parsed.text(0), "Iron Sword")
            bsa.close()

    def test_read_embedded_names(self):
        table = StringTable(path=Path("x"), kind="strings", entries=[b"Hello"]).serialize()
        archive = build_bsa({"Strings\\Mod_english.STRINGS": table}, embedded=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Mod.bsa"
            path.write_bytes(archive)
            bsa = Bsa(path)
            entry = bsa.find([".strings"])[0]
            self.assertEqual(bsa.read(entry), table)
            bsa.close()

    def test_read_lz4_compressed(self):
        table = StringTable(
            path=Path("x"),
            kind="dlstrings",
            entries=[b"A long description.", b"Second line."],
        ).serialize()
        archive = build_bsa({"Strings\\Mod_english.DLSTRINGS": table}, compress=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Mod.bsa"
            path.write_bytes(archive)
            bsa = Bsa(path)
            entry = bsa.find([".dlstrings"])[0]
            self.assertEqual(bsa.read(entry), table)
            bsa.close()

    def test_rewrite_identity(self):
        """不替换任何文件时，重打包结果应与原档逐字节相同。"""
        raw = build_bsa({r"strings\\SkyUI_english.STRINGS": b"$a\tHello\n" * 10})
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "In.bsa"
            dst = Path(tmp) / "Out.bsa"
            src.write_bytes(raw)
            with Bsa(src) as bsa:
                stats = bsa.rewrite(dst)
            self.assertEqual(stats["entries"], 1)
            self.assertEqual(stats["replaced"], 0)
            self.assertEqual(dst.read_bytes(), raw)

    def test_rewrite_replaces_content(self):
        files = {
            r"strings\\SkyUI_english.STRINGS": b"$a\tHello\n",
            r"scripts\\Foo.pex": b"\xde\xc0\x57\xfa" + b"\x00" * 20,
        }
        raw = build_bsa(files)
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "In.bsa"
            dst = Path(tmp) / "Out.bsa"
            src.write_bytes(raw)
            new_pex = b"\xde\xc0\x57\xfa" + b"\x11" * 40
            with Bsa(src) as bsa:
                stats = bsa.rewrite(dst, {r"scripts\\foo.pex": new_pex})
            self.assertEqual(stats["replaced"], 1)
            with Bsa(dst) as check:
                found = {e.path: check.read(e) for e in check.entries}
            self.assertEqual(found[r"scripts\\foo.pex"], new_pex)
            self.assertEqual(
                found[r"strings\\skyui_english.strings"], files[r"strings\\SkyUI_english.STRINGS"]
            )

    def test_rewrite_compressed_archive(self):
        files = {r"scripts\\Bar.pex": b"payload-" * 200}
        raw = build_bsa(files, compress=True)
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "In.bsa"
            dst = Path(tmp) / "Out.bsa"
            src.write_bytes(raw)
            with Bsa(src) as bsa:
                self.assertTrue(bsa.entries[0].compressed)
                new_data = b"translated-" * 120
                bsa.rewrite(dst, {r"scripts\\bar.pex": new_data})
            with Bsa(dst) as check:
                entry = check.entries[0]
                self.assertTrue(entry.compressed)
                self.assertEqual(check.read(entry), new_data)

    def test_rewrite_embedded_names(self):
        raw = build_bsa({r"scripts\Foo.pex": b"AAAA" * 10}, embedded=True)
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "In.bsa"
            dst = Path(tmp) / "Out.bsa"
            src.write_bytes(raw)
            with Bsa(src) as bsa:
                self.assertTrue(bsa._embedded_names)
                bsa.rewrite(dst, {r"scripts\foo.pex": b"BBBB" * 5})
            with Bsa(dst) as check:
                self.assertTrue(check._embedded_names)
                self.assertEqual(check.read(check.entries[0]), b"BBBB" * 5)

    def test_rewrite_unknown_entry_rejected(self):
        raw = build_bsa({r"scripts\\Foo.pex": b"x"})
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "In.bsa"
            dst = Path(tmp) / "Out.bsa"
            src.write_bytes(raw)
            with Bsa(src) as bsa:
                with self.assertRaises(BsaError):
                    bsa.rewrite(dst, {r"scripts\\Missing.pex": b"y"})

    def test_rewrite_keeps_other_entries(self):
        files = {
            r"scripts\\A.pex": b"a" * 100,
            r"scripts\\B.pex": b"b" * 50,
            r"meshes\\x.nif": b"\x00" * 200,
        }
        raw = build_bsa(files)
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "In.bsa"
            dst = Path(tmp) / "Out.bsa"
            src.write_bytes(raw)
            with Bsa(src) as bsa:
                before = {e.path: bsa.read(e) for e in bsa.entries}
                bsa.rewrite(dst, {r"scripts\\a.pex": b"z" * 10})
            with Bsa(dst) as check:
                after = {e.path: check.read(e) for e in check.entries}
            self.assertEqual(before.keys(), after.keys())
            self.assertEqual(after[r"scripts\\a.pex"], b"z" * 10)
            for key in (r"scripts\\b.pex", r"meshes\\x.nif"):
                self.assertEqual(after[key], before[key])

    def test_non_bsa_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Bad.bsa"
            path.write_bytes(b"NOTABSA!" + b"\x00" * 64)
            with self.assertRaises(Exception):
                Bsa(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
