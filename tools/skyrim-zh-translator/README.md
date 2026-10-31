# 天际整合包 · 一键汉化（skytrans）

把整个《上古卷轴5：天际》模组整合包的英文文本批量翻成简体中文，最后输出成一个
**MO2 可以直接挂载的汉化 mod 目录**——装上去就生效，删掉就还原。

纯 Python 标准库实现，**不需要 pip 安装任何东西**（Python 3.9+）。

---

## 1. 它能翻什么

| 文本来源 | 说明 | 支持 |
|---|---|---|
| `.ESP/.ESM/.ESL` 内嵌字符串（非本地化插件） | 物品名、法术名、任务名、书籍正文、对话、消息框…… | ✅ 写回插件副本 |
| `.STRINGS / .DLSTRINGS / .ILSTRINGS`（本地化插件） | 同上，但文本单独存在字符串表里 | ✅ 输出同名 `_english` 覆盖 |
| **打包在 BSA 里的**字符串表 | 很多 mod 把 strings 塞进 BSA | ✅ 自动解包读取（纯 Python LZ4） |
| MCM 菜单文本 `Interface/Translations/*_english.txt` | 模组配置菜单里的每一行文字 | ✅ 输出同名覆盖 |
| Papyrus 脚本里写死的字符串（`.pex`） | 作者直接把英文写进脚本 | ❌ 只能等作者改脚本 |
| 语音、贴图、字体 | — | ❌ 与文本无关 |

> 经验上，一个成熟整合包里 **90% 以上**的可见文本来自前四项。

---

## 2. 三十秒上手

### Windows

1. 安装 [Python 3.9+](https://www.python.org/downloads/)（安装时勾选 **Add Python to PATH**）；
2. 双击 `一键翻译.bat`；
3. 把整合包目录**拖进黑窗口**，回车；
   - MO2 用户：`...\MO2\mods`
   - Vortex / 手动用户：`...\Skyrim Special Edition\Data`
   - 也可以直接指向游戏根目录，工具会自己往下找
4. 按提示选翻译引擎、填 API Key（只需填一次，会存到 `config.json`）；
5. 等进度跑完，把生成的 **`汉化输出`** 文件夹当成一个 mod 装进 MO2。

### Linux / macOS（或在 Windows 命令行里）

```bash
./translate.sh                                   # 交互向导
python3 run.py "D:\MO2\mods" --provider openai   # 直接指定
```

### 建议先花 10 秒钟看一眼规模（不花钱）

```bash
python3 run.py "D:\MO2\mods" --scan-only
```

会告诉你：多少个插件、多少条文本、多少字符、预估 token 数。
**先确认目录选对了、字符数量级合理，再去花钱翻。**

想再试一小段效果：

```bash
python3 run.py "D:\MO2\mods" --limit 300 --provider openai
```

---

## 3. 选哪个翻译引擎

| 引擎 | 怎么填 | 适合 |
|---|---|---|
| **DeepSeek**（推荐） | 向导选 1，Key 从 [platform.deepseek.com](https://platform.deepseek.com) 拿 | 便宜、中文自然、术语跟着提示词走 |
| 通义千问 DashScope | 向导选 2 | 国内直连稳定 |
| Kimi / 智谱 GLM / 硅基流动 | 向导选 3，输入预设名 | 都兼容 OpenAI 协议 |
| 本地 Ollama / LM Studio | 向导选 7 | **完全免费离线**，显卡够用（7B 以上）效果尚可 |
| DeepL / 谷歌 / 百度 | 向导选 4/5/6 | 快、便宜，但游戏术语一致性差一些 |
| 试跑（mock） | 向导选 8 | 不联网，只验证流程、看输出结构 |

**费用**：整包通常 20 万～80 万字符，
用 DeepSeek 这类模型一般 **几毛到几块钱**；先跑 `--scan-only` 看字符数最稳。

**速度**：默认每批 2400 字符 / 30 条，一条请求 2～5 秒，40 万字符大约 20～40 分钟。
想快就调大 `--batch-chars`（模型上下文允许的话）。

配置文件长这样（`config.json`，含密钥，别分享）：

```json
{
  "provider": "openai",
  "base_url": "https://api.deepseek.com/v1",
  "model": "deepseek-chat",
  "api_key": "sk-xxxx"
}
```

---

## 4. 它是怎么做到「不把模组改坏」的

1. **扫描**：递归找插件、字符串表、MCM 文件、BSA；官方主文件（`Skyrim.esm`、`Update.esm`、
   三个 DLC、CC 内容）默认跳过。
2. **抽取**：
   - 本地化插件 → 只读它对应的 `_english.strings`（松散文件优先，找不到就去 BSA 里捞）；
   - 非本地化插件 → 按「记录类型 × 字段白名单」逐字段判断，且要求数据是
     `可打印文本 + 以 \x00 结尾` 的 zstring，路径 / FormID / EDID 一律不碰。
3. **去重**：全包按句子去重（同一句话只翻一次），常见能省 20%～50% 的费用。
4. **占位符保护**：`%s`、`%d`、`{0}`、`<font ...>`、`[PLYR]`、`$变量`、`\n` 全部替换成
   `⟦0⟧` 之类的记号再送翻，翻完还原。**必须保留的记号（printf、花括号、变量、链接）
   一旦丢失，该条直接放弃译文、保留英文**——宁可一条英文，也不要一条会让游戏显示错乱甚至
   崩溃的字符串。
5. **术语表**：`glossary_zh.tsv` 里 90 条天际地名/种族/技能官方译名，注入提示词保证前后一致；
   译文里还残留英文原词的会兜底替换。
6. **缓存**：`cache.sqlite` 按「引擎 + 模型 + 术语表版本 + 原文」存译文。
   中断了直接重跑，已翻的部分不会重复计费。
7. **回写**：
   - 未修改的记录**直接复用原始字节**，输出插件与输入逐字节一致 →
     不会因为我们的解析器不认识某个字段而损坏文件；
   - 修改过的记录重建子记录、长度 > 65535 自动用 `XXXX` 扩展、压缩记录重新压缩、GRUP 大小重算；
   - 字符串表**条目数量与顺序完全不变**，ID 引用绝不会错位。

---

## 5. 输出目录里有什么

```
汉化输出/                     ← 就当它是一个 mod
├─ ModA.esp                   ← 内嵌字符串插件的汉化副本（只在需要时生成）
├─ Strings/
│  └─ ModB_english.STRINGS    ← 本地化插件的字符串表（同名覆盖原文件）
├─ Interface/Translations/
│  └─ ModB_english.txt        ← MCM 菜单汉化（同名覆盖）
├─ 校对表.csv                 ← 原文/译文对照，可改完再回填
├─ 汉化报告.md                ← 统计、失败条目、警告
├─ cache.sqlite               ← 断点续翻缓存
└─ 安装说明.txt               ← 给这个汉化包本身看的安装说明
```

---

## 6. 装进游戏

### MO2（推荐）

1. 左侧「安装 mod」→ 选「从文件夹安装」→ 指向 `汉化输出`；
2. 把该 mod **拖到左侧列表最下方**（覆盖优先级更高）；
3. 如果 `汉化输出` 里有 `.esp`，去右侧插件列表把它勾上，并尽量排在同类补丁之后；
4. 进游戏。

### Vortex / 手动

把 `汉化输出` 里的内容整体复制进 `Data`，覆盖同名文件（先备份 `Data`）。

### 回滚

删掉这个 mod（或撤销覆盖）即可，松散文件覆盖机制不会改坏原始 mod。

> **不需要改游戏语言。** 汉化包覆盖的就是 `*_english.*` 文件，
> `Skyrim.ini` 里保持 `sLanguage=ENGLISH` 即可。

---

## 7. 中文字体：不装就是方块

Skyrim 原版字体没有中文字形，**必须**再装一个中文字体 mod（Nexus 上搜
`Chinese font` / 字体替换类，或直接用整合包作者提供的字体补丁）。
字体 mod 和本汉化包不冲突，谁先谁后都行。

判断标准：进游戏如果所有中文都是 `□□□`，就是缺字体，不是翻译没生效。

---

## 8. 人工校对（可选）

```bash
# 1. 导出（默认已经导出到 汉化输出/校对表.csv，Excel 直接打开）
# 2. 用 Excel / VS Code 改「译文」列，存回 CSV(UTF-8)
# 3. 回填缓存并重新生成输出（不会再花钱）
python3 run.py "D:\MO2\mods" --import-review "汉化输出\校对表.csv"
```

注意：回填后重跑会**重新加载原始 mod** 再套用译文，所以是幂等的。

---

## 9. 常用命令

```bash
python3 run.py <目录> --scan-only               # 只扫描统计（免费）
python3 run.py <目录> --limit 300               # 试翻 300 个片段
python3 run.py <目录> -y                        # 不问直接开跑
python3 run.py <目录> --only "SkyUI,Ordinator"  # 只翻文件名含关键字的插件
python3 run.py <目录> --exclude "Test"          # 排除
python3 run.py <目录> --no-bsa                  # 不解析 BSA（更快）
python3 run.py <目录> --dry-run                 # 不联网，只用缓存走流程
python3 run.py <目录> --in-place                # 直接改原文件（自动存 .orig 备份）
python3 run.py <目录> --batch-chars 4000        # 加大批次（更快，更吃上下文）
python3 run.py <目录> -o "D:\汉化2"             # 换输出目录
```

---

## 10. 常见问题

**Q：跑完进游戏还是英文？**
- 汉化包没启用，或被别的 mod 覆盖了 → 把它拖到左侧列表最下方；
- 内嵌字符串的插件：确保右侧插件列表里勾的是汉化版 `xxx.esp`，且排在后面；
- 该 mod 的文本在 `.pex` 脚本里 → 翻不了，属已知限制；
- MCM 文本没变：确认 `Interface/Translations/xxx_english.txt` 已覆盖（MO2 里看冲突）。

**Q：中文显示成方块 / 问号？**
装中文字体 mod（见第 7 节）。

**Q：按钮文字溢出、对话框错位？**
中文比英文短，一般不会。个别硬编码宽度的界面可能挤，属 mod 本身 UI 问题。

**Q：会不会把 mod 改坏？**
- 本地化插件：我们**完全不碰 `.esp`**，只输出 strings；
- 内嵌插件：输出的是**副本**，原文件不动；未修改的记录逐字节保留；
- 想更保守：加 `--only` 只翻某几个插件，或先 `--limit` 试翻再全量。

**Q：中途断了怎么办？**
直接重跑同一条命令，缓存命中就不会重复花钱。

**Q：整包太大，想分批？**
`--only "前缀"` 分批跑，全部输出到同一个目录即可。

**Q：翻得不准 / 专有名词乱？**
`glossary_zh.tsv` 里加词条（`英文<TAB>中文`），重跑；术语表变了缓存会自动区分。
再不满意就用 `校对表.csv` 人工修，`--import-review` 回填。

**Q：`--in-place` 和默认有什么区别？**
默认在独立目录生成汉化包（推荐，删掉就还原）；
`--in-place` 直接改原文件（会留 `.orig` 备份），适合不用 MO2 的用户。

---

## 11. 已知限制

- Papyrus 脚本内写死的字符串无法翻译。
- 少数 mod 用非标准 BSA 变体或加密包，可能读不出 strings（会在报告里提示，不影响其他 mod）。
- 非本地化插件里如果有「看起来像英文句子、实际上是内部标识」的字段，理论上可能被误翻；
  我们用「记录类型白名单 + zstring 特征 + 路径/ID 黑名单」三重过滤把风险压到很低，
  并且原文件永远不会被修改（除非你显式用 `--in-place`）。
- 机翻质量取决于引擎；量大时建议人工抽查 `校对表.csv`。

---

## 12. 目录结构与测试

```
tools/skyrim-zh-translator/
├─ run.py                入口
├─ 一键翻译.bat           Windows 双击
├─ translate.sh           Linux/macOS
├─ glossary_zh.tsv        术语表（可编辑）
├─ config.example.json    配置示例
├─ skytrans/
│  ├─ esp.py             插件解析/回写（TES4 记录、GRUP、XXXX、压缩记录）
│  ├─ strings.py         STRINGS/DLSTRINGS/ILSTRINGS
│  ├─ bsa.py             BSA v103/104/105 只读 + 纯 Python LZ4
│  ├─ mcm.py             MCM 翻译文件
│  ├─ fields.py          记录×字段白名单
│  ├─ textfilter.py      过滤/占位符保护/长文切分/术语表
│  ├─ providers.py       各翻译引擎
│  ├─ cache.py           SQLite 缓存 + 校对 CSV
│  ├─ scan.py            目录扫描
│  ├─ pipeline.py        抽取→翻译→回写→报告
│  └─ cli.py             命令行 + 交互向导
└─ tests/                20 个单元测试（含插件字节级往返、BSA/LZ4）
```

```bash
python3 -m unittest discover -s tests -v
```

---

## 13. 免责

- 汉化仅用于个人游戏体验；转载/分发他人 mod 的译文请先看作者授权。
- 默认模式**不改动任何原始文件**；`--in-place` 会改动原文件（已自动备份），请自行确认。
