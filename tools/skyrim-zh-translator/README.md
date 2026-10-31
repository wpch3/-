# 天际整合包 · 一键汉化（skytrans）

把整个《上古卷轴5：天际》模组整合包的英文文本批量翻成简体中文，输出成一个
**MO2 可以直接挂载的汉化 mod**——装上去就生效，删掉就还原。

纯 Python 标准库实现，**不需要 pip 安装任何东西**（Python 3.9+）。

> v1.1 起加入了从 xTranslator / ESP-ESM Translator 学来的**词典（翻译记忆）**、
> **字段长度质检**、**并发请求**，以及按 xTranslator 记录定义重建的**字段白名单**。
> 详见第 4、6、15 节。

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

---

## 2. 三十秒上手

### Windows

1. 安装 [Python 3.9+](https://www.python.org/downloads/)（安装时勾选 **Add Python to PATH**）；
2. 双击 `一键翻译.bat`；
3. 把整合包目录**拖进黑窗口**，回车；
   - MO2 用户：`...\MO2\mods`
   - Vortex / 手动用户：`...\Skyrim Special Edition\Data`
   - 也可以直接指向游戏根目录，工具会自己往下找
4. 按提示选翻译引擎、填 API Key（只需填一次，存到 `config.json`）；
5. 等进度跑完，把生成的 **`汉化输出`** 文件夹当成一个 mod 装进 MO2。

### Linux / macOS（或命令行）

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

**费用**：整包通常 20 万～80 万字符，用 DeepSeek 这类模型一般 **几毛到几块钱**；
先跑 `--scan-only` 看字符数，再用词典把「已经有人翻过的句子」全部剔掉，剩下的才计费。

---

## 4. 词典（翻译记忆）：先复用，再花钱

这是 xTranslator 的 **SST 词典**、ESP-ESM Translator 的 **BDD 数据库**、mTRANSLATOR 的
**本地记忆**共同的核心思路：**同一句话绝不翻第二遍**。

工具会自动维护 `汉化输出/tm.sqlite`，并支持四种来源：

```bash
# ① 直接吃现成汉化目录：把 *_chinese.STRINGS 与原版 *_english.STRINGS 按 ID 配对
python3 run.py "D:\MO2\mods" --tm-from "D:\旧汉化包"

# ② 手头有「英文 esp + 已汉化 esp」：按 FormID + 字段 + 出现次序配对
python3 run.py "D:\MO2\mods" --tm-pair "OldMod.esp" "OldMod_汉化.esp"

# ③ 吃对照表：TSV / CSV（英文<TAB>中文），也能直接喂 xTranslator/ESP-ESM 导出的表
python3 run.py "D:\MO2\mods" --tm-import 我的词典.tsv

# ④ 导出我们的成果，给别的工具/别人用
python3 run.py "D:\MO2\mods" --tm-export 词典.tsv
```

匹配分三级（与 ESP-ESM Translator 一致）：

| 级别 | 规则 | 说明 |
|---|---|---|
| 精确 | 原文完全相同 | 直接套用 |
| 归一化 | 忽略大小写/标点/空格，**数字当占位符** | 命中后把译文里的数字换回原文的数字（"Deal 10 damage" ↔ "Deal 25 damage"），数量对不上就放弃 |
| 模糊 | 相似度 ≥ 阈值（`--fuzzy 0.93`，默认关闭） | 适合 mod 更新后微调过的句子 |

安全阀：**占位符集合不一致的条目绝不复用**（原文有 `%s`、译文没有 → 丢弃）。

实测：8 万条规模的整合包，第一遍翻完自动入库 4.5 万条；删掉缓存重跑，
**词典命中 4.5 万条、接口调用 0 次**。

---

## 5. 并发与速度

```bash
python3 run.py "D:\MO2\mods" --workers 8 --batch-chars 3000
```

- `--workers`：并发请求线程数（默认 1）。LLM 接口常见能扛 4～8 并发，速度线性提升；
  遇到 429 会自动退避重试，失败批次会二分拆小重试，不会整批丢。
- `--batch-chars`：单次请求字符上限。模型上下文够大就调大，减少往返开销。

---

## 6. 质检报告（跑完自动生成）

每次跑完会生成 `质检报告.md` + `质检.csv`，检查这些（都是现成工具里被证明有用的检查项）：

| 检查 | 级别 | 说明 |
|---|---|---|
| 标记不一致 | **错误** | `<Alias=...>`、`%s`、`[PLYR]`、`<font>` 等原文与译文对不上（xTranslator 的 Alias Check） |
| 空译文 | **错误** | 译成空字符串会导致游戏里该处什么都不显示 |
| 未翻译 | 警告 | 译文里没有中文（模型偷懒/接口降级） |
| 与原文相同 | 警告 | 没被翻译 |
| 可能超长 | 警告 | 超过官方内容同字段出现过的最大长度（用 xTranslator 的长度参考值），界面可能截断 |
| 同原文多译法 | 警告/提示 | 同一句出现多种译法；`MESG:ITXT`、`INFO:NAM1` 这类**枚举文本**会升级为警告 |
| 换行数变化 / 首尾空格丢失 | 提示 | 书籍、对话排版可能错位 |

`质检.csv` 可以在 Excel 里逐条过，改完用 `--import-review` 回填。

---

## 7. 它是怎么做到「不把模组改坏」的

1. **扫描**：递归找插件、字符串表、MCM 文件、BSA；官方主文件（`Skyrim.esm`、`Update.esm`、
   三个 DLC、CC 内容）默认跳过。
2. **抽取**：
   - 本地化插件 → 只读它对应的 `_english.strings`（松散文件优先，找不到就去 BSA 里捞）；
   - 非本地化插件 → 按 **记录 × 字段白名单**（`FULL` 全记录通用、`DESC` 按长字符串、
     `MGEF:DNAM`/`QUST:CNAM`/`ACTI:RNAM` 等按表登记）判断，且要求数据是
     `可打印文本 + 以 \x00 结尾` 的 zstring；路径 / FormID / EDID 一律不碰。
3. **去重**：全包按句子去重，常见能省 20%～50%。
4. **词典 / 缓存**：先白嫖（见第 4 节），剩下的才送接口。
5. **占位符保护**：`%s`、`%d`、`{0}`、`<font ...>`、`[PLYR]`、`$变量`、`\n` 全部替换成
   `⟦0⟧` 记号再送翻，翻完还原。**必须保留的记号一旦丢失，该条直接放弃译文、保留英文**。
6. **术语表**：`glossary_zh.tsv` 里 90 条天际地名/种族/技能官方译名，注入提示词保证前后一致。
7. **回写**：
   - 未修改的记录**直接复用原始字节**，输出插件与输入逐字节一致；
   - 修改过的记录重建子记录、长度 > 65535 自动用 `XXXX` 扩展、压缩记录重新压缩、GRUP 大小重算；
   - 字符串表**条目数量与顺序完全不变**，ID 引用绝不会错位。

---

## 8. 输出目录里有什么

```
汉化输出/                     ← 就当它是一个 mod
├─ meta.ini                   ← MO2 元数据（分类=汉化）
├─ ModA.esp                   ← 内嵌字符串插件的汉化副本（只在需要时生成）
├─ Strings/
│  └─ ModB_english.STRINGS    ← 本地化插件的字符串表（同名覆盖原文件）
├─ Interface/Translations/
│  └─ ModB_english.txt        ← MCM 菜单汉化（同名覆盖）
├─ 校对表.csv                 ← 原文/译文对照，可改完再回填
├─ 词典.tsv                   ← --tm-export 导出，可复用/分享
├─ 质检报告.md / 质检.csv      ← 自动质检结果
├─ 汉化报告.md                ← 统计（词典命中/缓存命中/实际计费条数）、失败条目、警告
├─ tm.sqlite                  ← 翻译记忆库
├─ cache.sqlite               ← 断点续翻缓存
└─ 安装说明.txt
```

---

## 9. 装进游戏

### MO2（推荐）

1. 左侧「安装 mod」→「从文件夹安装」→ 指向 `汉化输出`；
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

## 10. 中文字体：不装就是方块

Skyrim 原版字体没有中文字形，**必须**再装一个中文字体 mod（Nexus 上搜
`Chinese font` / 字体替换类，或直接用整合包作者提供的字体补丁）。
字体 mod 和本汉化包不冲突，谁先谁后都行。

判断标准：进游戏如果所有中文都是 `□□□`，就是缺字体，不是翻译没生效。

---

## 11. 人工校对（可选）

```bash
# 1. 跑完后 汉化输出/校对表.csv 已经生成（Excel 直接打开）
# 2. 改「译文」列，存回 CSV(UTF-8)
# 3. 回填并重新生成（不会再花钱）
python3 run.py "D:\MO2\mods" --import-review "汉化输出\校对表.csv" --tm-export 词典.tsv
```

回填优先于词典：**人工改过的内容会被缓存记住并优先生效**。

---

## 12. 常用命令

```bash
python3 run.py <目录> --scan-only               # 只扫描统计（免费）
python3 run.py <目录> --limit 300               # 试翻 300 个片段
python3 run.py <目录> -y                        # 不问直接开跑
python3 run.py <目录> --workers 8               # 并发请求
python3 run.py <目录> --tm-from "D:\旧汉化"      # 复用现成汉化
python3 run.py <目录> --tm-import 词典.tsv       # 复用词典
python3 run.py <目录> --fuzzy 0.93              # 开启模糊匹配
python3 run.py <目录> --only "SkyUI,Ordinator"   # 只翻文件名含关键字的插件
python3 run.py <目录> --exclude "Test"           # 排除
python3 run.py <目录> --no-bsa                   # 不解析 BSA（更快）
python3 run.py <目录> --dry-run                  # 不联网，只用缓存/词典走流程
python3 run.py <目录> --no-check                 # 跳过质检
python3 run.py <目录> --in-place                 # 直接改原文件（自动存 .orig 备份）
python3 run.py <目录> -o "D:\汉化2"              # 换输出目录
```

---

## 13. 常见问题

**Q：跑完进游戏还是英文？**
- 汉化包没启用，或被别的 mod 覆盖了 → 把它拖到左侧列表最下方；
- 内嵌字符串的插件：确保右侧插件列表里勾的是汉化版 `xxx.esp`，且排在后面；
- 该 mod 的文本在 `.pex` 脚本里 → 翻不了，属已知限制；
- MCM 文本没变：确认 `Interface/Translations/xxx_english.txt` 已覆盖。

**Q：中文显示成方块 / 问号？**
装中文字体 mod（见第 10 节）。

**Q：按钮文字溢出、对话框错位？**
看 `质检报告.md` 里的「可能超长」条目；中文一般比英文短，个别硬编码宽度的界面需人工调整。

**Q：会不会把 mod 改坏？**
- 本地化插件：**完全不碰 `.esp`**，只输出 strings；
- 内嵌插件：输出**副本**，原文件不动；未修改的记录逐字节保留；
- 想更保守：`--only` 只翻几个插件，或先 `--limit` 试翻。

**Q：中途断了怎么办？**
直接重跑同一条命令；缓存和词典都会命中，不会重复花钱。

**Q：换了个便宜模型，之前的译文会丢吗？**
不会。缓存按「引擎+模型」隔离，但**词典是跨引擎的**，换模型照样命中。

**Q：翻得不准 / 专有名词乱？**
`glossary_zh.tsv` 里加词条（`英文<TAB>中文`），重跑；术语表版本变化会自动区分缓存。
再不满意就用 `校对表.csv` / `质检.csv` 人工修，`--import-review` 回填。

---

## 14. 已知限制

- Papyrus 脚本内写死的字符串无法翻译（xTranslator 有 pex 反编译翻译，我们暂未实现）。
- 少数 mod 用非标准 BSA 变体或加密包，可能读不出 strings（报告里会提示，不影响其他 mod）。
- 字段白名单基于公开的记录定义整理；极端自定义字段仍可能漏翻（不会误翻，因为我们要求
  「字段在名单里 + 数据是可打印 zstring」双条件）。
- 模糊匹配默认关闭：游戏文本差一个词往往意思差很多，建议只对「mod 小版本更新」开启。
- 机翻质量取决于引擎；量大时建议抽查 `质检报告.md` 的前几十条。

---

## 15. 我们参考了哪些现成工具

这个工具不是凭空写的，字段语义、匹配策略、质检项都对着成熟工具做的。

| 参考 | 学到了什么 | 我们怎么用 |
|---|---|---|
| **[xTranslator](https://github.com/MGuffin/xTranslator)**（MPL-2.0） | ① Skyrim 记录定义：`FULL` 全记录通用、`DESC` 是长字符串、`MGEF:DNAM`/`QUST:CNAM`/`ACTI:RNAM` 等特殊字段；② 字段长度参考、枚举文本警告；③ SST 词典 + Alias 校验；④ EspCompare 按 FormID 配对 | 重写了 `fields.py`（修正了旧版把 `ACTI/FLOR/INFO:RNAM` 当资源引用的错误），`qa.py` 的标记校验与超长预警，`tm.py` 的按 ID 配对与插件配对 |
| **[ESP-ESM Translator](https://www.nexusmods.com/skyrimspecialedition/mods/921)** | 数据库（BDD）思路、「去数字/去标点」的模糊命中、枚举/重复文本一致性 | `tm.py` 的归一化匹配 + 数字回填；质检的「同原文多译法」 |
| **Arcane Translator**（Nexus 上的 AI 翻译前端） | 保留规则（XML/标签/CamelCase/下划线标识符）、术语强制、自我校验、会话续跑 | 占位符/标签保护、术语表注入与兜底、缓存续跑、质检 |
| **[mTRANSLATOR](https://github.com/starbudweiser/mTRANSLATOR)** | 自学习本地记忆 + 词典优先于机翻 | `tm.sqlite` 跑完自动学习，下次先查词典 |
| **xEdit / UESP** | TES4/GRUP/XXXX/压缩记录、STRINGS/DLSTRINGS/ILSTRINGS、BSA v105 + LZ4 格式细节 | `esp.py` / `strings.py` / `bsa.py` 的实现依据 |

> 说明：字段表与长度参考是对「哪些字段是字符串」这类**事实**的重新整理，
> 代码全部为本项目自行实现，未拷贝任何参考项目的代码。xTranslator 采用 MPL-2.0，
> 若你要把本工具再分发，请保留本节署名。

---

## 16. 目录结构与测试

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
│  ├─ fields.py          记录×字段白名单 + 字段长度参考
│  ├─ textfilter.py      过滤/占位符保护/长文切分/术语表/记号签名
│  ├─ tm.py              词典：导入（TSV/strings 配对/插件配对）、三级匹配、导出
│  ├─ qa.py              质检：标记/未翻译/超长/多译法
│  ├─ providers.py       各翻译引擎
│  ├─ cache.py           SQLite 缓存 + 校对 CSV
│  ├─ scan.py            目录扫描
│  ├─ pipeline.py        抽取→词典/缓存→翻译→回写→报告（支持并发）
│  └─ cli.py             命令行 + 交互向导
└─ tests/                35 个单元测试
```

```bash
python3 -m unittest discover -s tests -v
```

覆盖：插件字节级往返、长记录 `XXXX`、压缩记录、字符串表 ID 稳定性、BSA/LZ4、
词典三级匹配与数字回填、并发、质检各类问题、端到端 mock 流水线。

---

## 17. 免责

- 汉化仅用于个人游戏体验；转载/分发他人 mod 的译文请先看作者授权。
- 默认模式**不改动任何原始文件**；`--in-place` 会改动原文件（已自动备份），请自行确认。
