"""MCM（SkyUI 模组配置菜单）翻译文件：Interface/Translations/<Mod>_english.txt

格式：
    $key<TAB>文本
    ;注释

游戏按 sLanguage 加载 <Mod>_<语言>.txt；因为中文环境通常仍保持 sLanguage=ENGLISH，
所以汉化包的做法是直接覆盖 *_english.txt。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple

LINE_RE = re.compile(r"^\s*\$([^\t]+)\t(.*)$")


@dataclass
class McmFile:
    path: Path
    lines: List[str] = field(default_factory=list)  # 原始行（保持顺序与非翻译行）
    keys: List[Tuple[int, str, str]] = field(default_factory=list)  # (行号, key, 文本)
    encoding: str = "utf-8"
    newline: str = "\n"
    dirty: bool = False

    @classmethod
    def load(cls, path: Path) -> "McmFile":
        raw = path.read_bytes()
        enc = "utf-8"
        if raw.startswith(b"\xef\xbb\xbf"):
            enc, raw = "utf-8-sig", raw[3:]
        elif raw.startswith(b"\xff\xfe"):
            enc = "utf-16"
        elif raw.startswith(b"\xfe\xff"):
            enc = "utf-16"
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            enc = "cp1252"
            text = raw.decode(enc, "replace")
        newline = "\r\n" if "\r\n" in text else "\n"
        lines = text.replace("\r\n", "\n").split("\n")
        obj = cls(path=path, lines=lines, encoding=enc, newline=newline)
        for idx, line in enumerate(lines):
            match = LINE_RE.match(line)
            if match:
                obj.keys.append((idx, match.group(1).strip(), match.group(2)))
        return obj

    def set_text(self, index: int, value: str) -> None:
        line_idx, key, _old = self.keys[index]
        self.lines[line_idx] = "$%s\t%s" % (key, value)
        self.dirty = True

    def text(self, index: int) -> str:
        return self.keys[index][2]

    @property
    def key(self) -> str:
        return self.keys[0][1] if self.keys else ""

    def serialize(self) -> bytes:
        text = self.newline.join(self.lines)
        enc = self.encoding
        if enc == "utf-8-sig":
            return b"\xef\xbb\xbf" + text.encode("utf-8")
        if enc in ("utf-16", "utf-16-le"):
            return text.encode("utf-16")
        try:
            return text.encode(enc)
        except UnicodeEncodeError:
            return text.encode("utf-8")

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.serialize())
