"""BSA v105 解析测试（合成归档 + 真实 LZ4 frame 常量）。"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skytrans.bsa import Bsa, lz4_frame_decompress  # noqa: E402
from skytrans.strings import StringTable  # noqa: E402

# 由 lz4 官方实现生成的 frame，内容为 132 字节的 MCM 文本（3 次重复）
FRAME_HEX = (
    "04224d1860408237000000ff1d244d6f644e616d65094d792054657374204d6f64"
    "0a24546f67676c6509456e61626c6520666561747572650a2c00405074757265"
    "0a00000000"
)
FRAME_TEXT = b"$ModName\tMy Test Mod\n$Toggle\tEnable feature\n" * 3


def _payload(data: bytes, compress: bool) -> bytes:
    """返回 (payload, 状态位)。"""
    if compress:
        import lz4.frame

        frame = lz4.frame.compress(data)
        return struct.pack("<I", len(data)) + frame, True
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

    folder_records = b""
    for folder, items in folders.items():
        folder_records += (
            struct.pack("<Q", 0)
            + struct.pack("<I", len(items))
            + struct.pack("<I", 0)
            + struct.pack("<I", 0)
            + struct.pack("<I", 0)
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
        try:
            import lz4.frame  # noqa: F401
        except ImportError:
            self.skipTest("需要 lz4 生成测试数据")
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

    def test_non_bsa_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Bad.bsa"
            path.write_bytes(b"NOTABSA!" + b"\x00" * 64)
            with self.assertRaises(Exception):
                Bsa(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
