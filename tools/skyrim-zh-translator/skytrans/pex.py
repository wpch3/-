"""Papyrus 编译脚本 `.pex` 的字符串翻译（Skyrim LE/SE，格式 3.1 / 3.2，全大端）。

为什么要做它：不少模组把界面文字直接写在脚本里（`Debug.MessageBox("...")`、
`Notification("...")`、SkyUI MCM 的选项标题/说明），这些文字不在 ESP 里，也不在
`_english.strings` 里，不处理就会在游戏里留下英文。

做法（与 ESP-ESM Translator 的 .pex 模式思路一致，但更保守）：

* 只改「字符串表」里的**已有条目**，条数、顺序、索引全部不变；
* 指令字节、对象/属性/状态结构、调试信息**原样搬运**，因此没有任何偏移需要重算；
* 通过结构解析拿到「被当作标识符/文档引用」的索引（对象名、父类名、对象与属性的
  docstring、auto 状态名、变量名与类型名、属性名、调试信息里的函数名），这些一律不碰；
* 其余条目再用「像不像人话」的启发式过滤：Papyrus 标识符不能含空格，所以
  「含空格或句末标点」这一条已经挡掉了绝大部分标识符，再加上扩展名/路径/`$` 前缀排除。

安全阀：改写后重新解析，校验条数、索引、尾部字节完全一致。

只读不写会破坏的东西：不做增删字符串、不改头部三个 wstring（源文件名/作者/机器名）。
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set, Tuple

MAGIC = 0xFA57C0DE

# Papyrus 标识符：不能含空格，允许前导下划线
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# 路径 / 资源名 / 源文件名
PATH_RE = re.compile(
    r"[\\/]|\.(?:wav|dds|nif|psc|pex|hkx|swf|txt|xml|json|esp|esm|bsa|fuz|xwm)$",
    re.IGNORECASE,
)
# 形如 `_root.HUDMovieBaseInstance.` 的点分标识符/路径，不是人话
DOTTED_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\[\]]*(?:\.[A-Za-z0-9_\[\]]+)*\.?$")
# 控制字符（换行允许，制表/其它控制符允许保留但不算「可疑」）
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# 纯格式占位符，如 "{2}"、"%s"
ONLY_PLACEHOLDER_RE = re.compile(r"^[\s\{\}0-9%\.\,\:\-\+]*$")


class PexError(Exception):
    pass


# --------------------------------------------------------------------------
# 底层读写：wstring = uint16 长度 + 字节（大端，长度是字节数，无结尾 0）
# --------------------------------------------------------------------------
def _read_wstring(data: bytes, pos: int) -> Tuple[bytes, int]:
    if pos + 2 > len(data):
        raise PexError("字符串表越界")
    length = struct.unpack_from(">H", data, pos)[0]
    end = pos + 2 + length
    if end > len(data):
        raise PexError("字符串长度越界（%d）" % length)
    return data[pos + 2 : end], end


def _write_wstring(raw: bytes) -> bytes:
    if len(raw) > 0xFFFF:
        raise PexError("字符串过长（%d 字节），Papyrus 单条上限 65535" % len(raw))
    return struct.pack(">H", len(raw)) + raw


@dataclass
class PexString:
    index: int
    raw: bytes
    translatable: bool = False
    reason: str = ""
    identifier: bool = False

    @property
    def text(self) -> str:
        return self.raw.decode("utf-8", "replace")


def looks_translatable(text: str, strict: bool = True) -> Tuple[bool, str]:
    """判断一条脚本字符串是否值得翻译。返回 (是否翻译, 不翻的理由)。"""
    if not text or not text.strip():
        return False, "空串"
    if CONTROL_RE.search(text):
        return False, "含控制字符"
    if not any(ch.isalpha() for ch in text):
        return False, "没有字母"
    if text.startswith("$"):
        return False, "翻译键"
    if PATH_RE.search(text):
        return False, "路径/文件名"
    if IDENT_RE.match(text):
        return False, "标识符"
    if DOTTED_RE.match(text.strip()):
        return False, "点分标识符"
    if ONLY_PLACEHOLDER_RE.match(text):
        return False, "占位符"
    if strict:
        stripped = text.rstrip()
        if " " not in text.strip() and not stripped.endswith((".", "!", "?", ":", "…")):
            # 单个词多半是枚举值/状态名/按键名，风险大于收益
            return False, "单个词（保守跳过）"
    return True, ""


class PexFile:
    """一个 .pex 脚本。`strings` 之外的所有字节都原样保留。"""

    def __init__(
        self,
        path: Optional[Path] = None,
        data: Optional[bytes] = None,
        archive: Optional[Path] = None,
        entry_path: str = "",
        label: str = "",
    ) -> None:
        self.path = Path(path) if path is not None else None
        self.archive = archive  # 来自 BSA 时记录归档路径
        self.entry_path = entry_path  # BSA 内条目路径（小写、反斜杠）
        self.label = label or (self.path.name if self.path else Path(entry_path).name)
        self.major = 0
        self.minor = 0
        self.game_id = 0
        self.source_name = b""
        self.user_name = b""
        self.machine_name = b""
        self.strings: List[PexString] = []
        self.identifiers: Set[int] = set()
        self.objects: int = 0
        self.dirty = False
        self._prefix = b""  # 头部 + 三个 wstring
        self._tail = b""  # 字符串表之后的所有内容

        if data is None:
            if self.path is None:
                raise PexError("必须给 data 或 path")
            data = self.path.read_bytes()
        self._parse(data)

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------
    def _parse(self, data: bytes) -> None:
        if len(data) < 18:
            raise PexError("文件太小，不是 .pex")
        magic = struct.unpack_from(">I", data, 0)[0]
        if magic != MAGIC:
            raise PexError("magic 不符：0x%08X（不是 .pex）" % magic)
        self.major = data[4]
        self.minor = data[5]
        self.game_id = struct.unpack_from(">H", data, 6)[0]

        pos = 16
        self.source_name, pos = _read_wstring(data, pos)
        self.user_name, pos = _read_wstring(data, pos)
        self.machine_name, pos = _read_wstring(data, pos)
        self._prefix = data[:pos]

        count = struct.unpack_from(">H", data, pos)[0]
        pos += 2
        raw_strings = []
        for index in range(count):
            raw, pos = _read_wstring(data, pos)
            raw_strings.append(raw)
        self._tail = data[pos:]

        self.identifiers = self._collect_identifier_indices()

        self.strings = []
        for index, raw in enumerate(raw_strings):
            text = raw.decode("utf-8", "replace")
            item = PexString(index=index, raw=raw)
            if index in self.identifiers:
                item.identifier = True
                item.reason = "结构里被当作名称引用"
            else:
                ok, why = looks_translatable(text, strict=True)
                item.translatable = ok
                item.reason = why
            self.strings.append(item)

    # ------------------------------------------------------------------
    # 结构遍历：尽量把「被当作名称/文档引用的字符串」都找出来
    # ------------------------------------------------------------------
    @staticmethod
    def _var_data_size(body: bytes, pos: int) -> int:
        """Variable Data 的字节数（含类型字节）。"""
        if pos >= len(body):
            raise IndexError("变量数据越界")
        kind = body[pos]
        if kind == 0:
            return 1
        if kind in (1, 2):  # identifier / string：u16 索引
            return 3
        if kind == 3:
            return 5
        if kind == 4:
            return 5
        if kind == 5:
            return 2
        raise ValueError("未知变量类型 %d" % kind)

    def _walk_object(self, body: bytes) -> Set[int]:
        """解析对象体：父类、docstring、auto 状态、变量、属性（遇到函数体就停）。"""
        ids: Set[int] = set()
        try:
            if len(body) < 10:
                return ids
            parent, docstring, _flags, auto_state = struct.unpack_from(">HHIH", body, 0)
            ids.update((parent, docstring, auto_state))
            pos = 10
            # 变量
            count = struct.unpack_from(">H", body, pos)[0]
            pos += 2
            for _ in range(count):
                name, type_name = struct.unpack_from(">HH", body, pos)
                pos += 4 + 4  # name/type + userFlags
                pos += self._var_data_size(body, pos)
                ids.update((name, type_name))
            # 属性
            count = struct.unpack_from(">H", body, pos)[0]
            pos += 2
            for _ in range(count):
                name, type_name, docstring = struct.unpack_from(">HHH", body, pos)
                pos += 6 + 4  # + userFlags
                flags = body[pos]
                pos += 1
                ids.update((name, type_name, docstring))
                if flags & 4:
                    auto_var = struct.unpack_from(">H", body, pos)[0]
                    pos += 2
                    ids.add(auto_var)
                # read/write handler 是完整函数（内含指令），无法安全跳过 → 到此为止
                if (flags & 5) == 1 or (flags & 6) == 2:
                    return ids
        except (IndexError, struct.error, ValueError):
            pass
        return ids

    def _collect_identifier_indices(self) -> Set[int]:
        """从对象/调试信息里收集「被当作名称」的字符串索引。"""
        ids: Set[int] = set()
        tail = self._tail
        try:
            pos = 0
            has_debug = tail[pos]
            pos += 1
            if has_debug:
                pos += 8  # modificationTime
                fn_count = struct.unpack_from(">H", tail, pos)[0]
                pos += 2
                for _ in range(fn_count):
                    obj, state, func, _type, icount = struct.unpack_from(
                        ">HHHBH", tail, pos
                    )
                    pos += 9 + 2 * icount
                    ids.update((obj, state, func))
            flag_count = struct.unpack_from(">H", tail, pos)[0]
            pos += 2
            for _ in range(flag_count):
                name, _bit = struct.unpack_from(">HB", tail, pos)
                pos += 3
                ids.add(name)
            object_count = struct.unpack_from(">H", tail, pos)[0]
            pos += 2
            self.objects = object_count
            for _ in range(object_count):
                name_index, size = struct.unpack_from(">HI", tail, pos)
                pos += 6
                ids.add(name_index)
                body = tail[pos : pos + size - 4]
                ids.update(self._walk_object(body))
                pos += size - 4
        except (IndexError, struct.error):
            # 结构不认识时也不要紧：标识符过滤只是「多一层保险」
            pass
        return ids

    # ------------------------------------------------------------------
    # 查询 / 修改
    # ------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.strings)

    @property
    def candidates(self) -> List[PexString]:
        return [s for s in self.strings if s.translatable]

    def set(self, index: int, text: str) -> None:
        item = self.strings[index]
        raw = text.encode("utf-8")
        if len(raw) > 0xFFFF:
            raise PexError("译文过长：%d 字节" % len(raw))
        if raw != item.raw:
            item.raw = raw
            self.dirty = True

    def to_bytes(self) -> bytes:
        parts = [self._prefix, struct.pack(">H", len(self.strings))]
        for item in self.strings:
            parts.append(_write_wstring(item.raw))
        parts.append(self._tail)
        return b"".join(parts)

    def verify(self) -> None:
        """重新解析改写结果，确认结构没被破坏。"""
        rebuilt = self.to_bytes()
        check = PexFile(data=rebuilt, label=self.label)
        if len(check.strings) != len(self.strings):
            raise PexError("%s：字符串条数变化" % self.label)
        if check._tail != self._tail:
            raise PexError("%s：表后数据变化" % self.label)
        if check._prefix != self._prefix:
            raise PexError("%s：头部变化" % self.label)
        for a, b in zip(check.strings, self.strings):
            if a.raw != b.raw:
                raise PexError("%s：第 %d 条不一致" % (self.label, b.index))

    def save(self, path: Path) -> None:
        data = self.to_bytes()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    @property
    def origin(self) -> str:
        if self.archive is not None:
            return "%s :: %s" % (self.archive, self.entry_path)
        return str(self.path)

    def describe(self) -> str:
        return "%s：%d 条字符串，可翻译 %d 条" % (
            self.label,
            len(self.strings),
            len(self.candidates),
        )
