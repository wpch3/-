"""命令行入口（含交互式向导）。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .cache import Cache
from .mcm import McmFile
from .pipeline import Options, Translator, write_report
from .providers import Provider, build_provider
from .scan import scan_pack
from .textfilter import Glossary

HERE = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = HERE / "config.json"
DEFAULT_GLOSSARY = HERE / "glossary_zh.tsv"

PRESETS = {
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat", "深度求索 DeepSeek"),
    "qwen": (
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen-plus",
        "阿里通义千问",
    ),
    "kimi": ("https://api.moonshot.cn/v1", "moonshot-v1-8k", "月之暗面 Kimi"),
    "zhipu": ("https://open.bigmodel.cn/api/paas/v4", "glm-4-flash", "智谱 GLM"),
    "siliconflow": (
        "https://api.siliconflow.cn/v1",
        "Qwen/Qwen2.5-7B-Instruct",
        "硅基流动",
    ),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini", "OpenAI"),
    "ollama": ("http://localhost:11434/v1", "qwen2.5:7b", "本地 Ollama"),
    "lmstudio": ("http://localhost:1234/v1", "local-model", "本地 LM Studio"),
}


# ----------------------------------------------------------------------
# 参数
# ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="skytrans",
        description="天际（Skyrim）模组整合包一键汉化工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("root", nargs="?", help="整合包目录（Data、MO2 的 mods 目录或游戏根目录）")
    p.add_argument("-o", "--out", default=None, help="输出目录（默认 ./汉化输出）")
    p.add_argument("--provider", default=None,
                   help="翻译引擎: openai/deepl/google/baidu/libre/mock/none")
    p.add_argument("--model", default=None, help="模型名（openai 兼容接口）")
    p.add_argument("--base-url", default=None, help="接口地址（openai / libre）")
    p.add_argument("--api-key", default=None, help="API Key（也可写在 config.json 里）")
    p.add_argument("--app-id", default=None, help="百度翻译 APPID")
    p.add_argument("--config", default=str(DEFAULT_CONFIG), help="配置文件路径")
    p.add_argument("--glossary", default=str(DEFAULT_GLOSSARY), help="术语表 TSV")
    p.add_argument("--cache", default=None, help="缓存数据库路径")
    p.add_argument("--scan-only", action="store_true", help="只扫描统计，不翻译")
    p.add_argument("--dry-run", action="store_true", help="不联网，仅用缓存与原文走一遍流程")
    p.add_argument("--limit", type=int, default=0, help="只翻译前 N 个片段（试跑）")
    p.add_argument("--only", default=None, help="只处理文件名包含这些关键字的插件，逗号分隔")
    p.add_argument("--exclude", default=None, help="排除包含这些关键字的插件，逗号分隔")
    p.add_argument("--source-lang", default="english", help="读取哪种语言的字符串表")
    p.add_argument("--output-lang", default="english", help="输出哪种语言的字符串表后缀")
    p.add_argument("--in-place", action="store_true", help="直接改原文件（自动备份 .orig）")
    p.add_argument("--no-bsa", action="store_true", help="不解析 BSA")
    p.add_argument("--batch-chars", type=int, default=2400, help="单次请求字符上限")
    p.add_argument("--batch-items", type=int, default=30, help="单次请求条数上限")
    p.add_argument("--piece-chars", type=int, default=1500, help="长文本切分长度")
    p.add_argument("--export-review", nargs="?", const="", default=None,
                   help="导出人工校对 CSV（可指定路径）")
    p.add_argument("--import-review", default=None, help="导入校对后的 CSV")
    p.add_argument("--wizard", action="store_true", help="强制进入交互向导")
    p.add_argument("-y", "--yes", action="store_true", help="不询问，直接开始")
    p.add_argument("--version", action="version", version="skytrans 1.0")
    return p


# ----------------------------------------------------------------------
# 配置 / 向导
# ----------------------------------------------------------------------
def load_config(path: Path) -> Dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(path: Path, config: Dict) -> None:
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def ask(prompt: str, default: str = "") -> str:
    suffix = " [%s]" % default if default else ""
    try:
        value = input("%s%s: " % (prompt, suffix)).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(1)
    return value or default


def wizard(config: Dict) -> Dict:
    print()
    print("=" * 62)
    print("  天际模组整合包 · 一键汉化")
    print("=" * 62)
    print()
    print("请把整合包的目录拖进来（也可以是 Data 或 MO2 的 mods 目录）：")
    root = ask("整合包目录").strip('"').strip("'")
    while not Path(root).is_dir():
        print("  目录不存在，请重新输入。")
        root = ask("整合包目录").strip('"').strip("'")
    config["root"] = root

    print()
    print("选择翻译引擎：")
    print("  1) DeepSeek（便宜、中文好，推荐）")
    print("  2) 通义千问 DashScope")
    print("  3) Kimi / 智谱 / 硅基流动 / 其他 OpenAI 兼容接口")
    print("  4) DeepL")
    print("  5) 谷歌翻译 API")
    print("  6) 百度翻译")
    print("  7) 本地模型（Ollama / LM Studio / LibreTranslate）")
    print("  8) 试跑模式（不联网，只验证流程）")
    choice = ask("序号", "1")

    current_key = config.get("api_key", "")
    key_hint = "（已保存，直接回车沿用）" if current_key else ""

    if choice in ("1", "2", "3"):
        preset_key = {"1": "deepseek", "2": "qwen"}.get(choice)
        if preset_key:
            base_url, model, label = PRESETS[preset_key]
            print("  使用 %s：%s / %s" % (label, base_url, model))
            base_url = ask("接口地址", config.get("base_url") or base_url)
            model = ask("模型", config.get("model") or model)
        else:
            print("  可用预设：kimi、zhipu、siliconflow、openai、ollama、lmstudio")
            name = ask("预设名（或直接回车手动填写）", "")
            if name in PRESETS:
                base_url, model, label = PRESETS[name]
                print("  %s：%s / %s" % (label, base_url, model))
            else:
                base_url = ask("接口地址", config.get("base_url") or "")
                model = ask("模型", config.get("model") or "")
            base_url = ask("接口地址", base_url)
            model = ask("模型", model)
        config.update(
            {
                "provider": "openai",
                "base_url": base_url,
                "model": model,
                "api_key": ask("API Key " + key_hint, current_key),
            }
        )
    elif choice == "4":
        config.update(
            {
                "provider": "deepl",
                "api_key": ask("DeepL API Key " + key_hint, current_key),
                "deepl_free": ask("是免费版密钥吗？(y/n)", "y").lower().startswith("y"),
            }
        )
    elif choice == "5":
        config.update(
            {"provider": "google", "api_key": ask("Google API Key " + key_hint, current_key)}
        )
    elif choice == "6":
        config.update(
            {
                "provider": "baidu",
                "app_id": ask("百度 APPID", config.get("app_id", "")),
                "api_key": ask("百度密钥 " + key_hint, current_key),
            }
        )
    elif choice == "7":
        sub = ask("o=Ollama / l=LM Studio / r=LibreTranslate", "o")
        config["provider"] = "libre" if sub == "r" else "openai"
        if sub == "r":
            config["base_url"] = ask("LibreTranslate 地址", "http://localhost:5000")
        else:
            base_url, model, _ = PRESETS["ollama" if sub == "o" else "lmstudio"]
            config["base_url"] = ask("接口地址", base_url)
            config["model"] = ask("模型", model)
        config["api_key"] = config.get("api_key", "")
    else:
        config["provider"] = "mock"

    print()
    review = ask("是否导出「人工校对表」CSV？(y/n)", "y")
    config["export_review"] = review.lower().startswith("y")
    config["output_lang"] = ask("字符串表后缀（保持不变就是 english）", "english")
    return config


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def resolve_provider(config: Dict, args, glossary: Optional[Glossary]) -> Provider:
    merged = dict(config)
    if args.provider:
        merged["provider"] = args.provider
    if args.model:
        merged["model"] = args.model
    if args.base_url:
        merged["base_url"] = args.base_url
    if args.api_key:
        merged["api_key"] = args.api_key
    if args.app_id:
        merged["app_id"] = args.app_id
    if args.batch_chars:
        merged["batch_chars"] = args.batch_chars
    if args.batch_items:
        merged["batch_items"] = args.batch_items
    return build_provider(merged, glossary)


def run(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config)
    config = load_config(config_path)

    interactive = (
        args.wizard
        or (args.root is None and sys.stdin.isatty())
        or (not args.provider and "provider" not in config and sys.stdin.isatty() and not args.scan_only)
    )

    if interactive:
        config = wizard(config)
        save_config(config_path, config)
        args.root = config.get("root") or args.root
        if not args.out:
            args.out = None
    else:
        if config.get("root") and not args.root:
            args.root = config["root"]

    if not args.root:
        print("错误：请指定整合包目录，例如：")
        print('  python run.py "D:\\MO2\\mods"')
        return 2

    root = Path(args.root).expanduser()
    if not root.is_dir():
        print("错误：目录不存在：%s" % root)
        return 2

    out = Path(args.out) if args.out else root.parent / "汉化输出"
    opts = Options(
        out=out,
        in_place=args.in_place,
        source_lang=args.source_lang,
        output_lang=args.output_lang or config.get("output_lang", "english"),
        use_bsa=not args.no_bsa,
        piece_chars=args.piece_chars,
        limit=args.limit,
        dry_run=args.dry_run,
        only=_split(args.only),
        exclude=_split(args.exclude),
        export_review=bool(args.export_review) if args.export_review is not None
        else bool(config.get("export_review", True)),
    )

    glossary = None
    glossary_path = Path(args.glossary)
    if glossary_path.exists():
        try:
            glossary = Glossary.load(glossary_path)
            print("已加载术语表：%d 条（%s）" % (len(glossary), glossary_path.name))
        except Exception as exc:  # noqa: BLE001
            print("术语表读取失败（忽略）：%s" % exc)

    provider = resolve_provider(config, args, glossary)
    print("翻译引擎：%s" % provider.describe())

    cache_path = Path(args.cache) if args.cache else out / "cache.sqlite"
    namespace = "%s|%s|%s" % (
        provider.describe(),
        glossary_digest(glossary),
        opts.output_lang,
    )
    cache = Cache(cache_path, namespace)

    # 校对回填
    if args.import_review:
        n = cache.import_csv(Path(args.import_review))
        print("已从 %s 导入 %d 条人工译文" % (args.import_review, n))

    print()
    pack = scan_pack(root, use_bsa=opts.use_bsa)
    if pack.warnings:
        for warning in pack.warnings[:20]:
            print("  提示：%s" % warning)

    translator = Translator(pack, opts, cache=cache, provider=provider, glossary=glossary)
    plan = translator.build_plan()
    plan.stats["localized_plugins"] = sum(1 for p in pack.plugins if p.localized)
    plan.stats["inline_plugins"] = sum(1 for p in pack.plugins if not p.localized)

    if not plan.units:
        print("没有需要翻译的文本。可能整个包已经是中文，或目录选错了。")
        _summary(plan, out)
        return 0

    if args.scan_only:
        _summary(plan, out)
        print("（--scan-only：未调用翻译接口）")
        return 0

    if not args.yes and not interactive and sys.stdin.isatty():
        print()
        if not ask("开始翻译？(y/n)", "y").lower().startswith("y"):
            print("已取消。")
            return 1

    translator.translate(plan)
    written = translator.write(plan)

    if args.export_review is not None or opts.export_review:
        path = Path(args.export_review) if args.export_review else out / "校对表.csv"
        n = cache.export_csv(path)
        print("已导出校对表：%s（%d 条，改完可用 --import-review 回填）" % (path, n))

    report_path = out / "汉化报告.md"
    write_report(plan, provider.describe(), report_path, translator.failures, pack.warnings + plan.warnings)

    print()
    print("=" * 62)
    print("完成！输出目录：%s" % out.resolve())
    print("  插件 %d 个 / 字符串表 %d 个 / MCM %d 个" %
          (written["plugins"], written["tables"], written["mcm"]))
    print("  安装：把该目录作为一个 mod 添加进 MO2（放在被汉化 mod 下方）")
    print("  别忘了安装中文字体 mod，否则会显示方块")
    print("  报告：%s" % report_path)
    print("=" * 62)
    cache.close()
    return 0


def glossary_digest(glossary: Optional[Glossary]) -> str:
    if not glossary or not glossary.mapping:
        return "0"
    raw = "\n".join("%s=%s" % kv for kv in sorted(glossary.mapping.items()))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]


def _split(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _summary(plan, out: Path) -> None:
    s = plan.stats
    print()
    print("统计：")
    print("  插件            : %d" % s.get("plugins", 0))
    print("  可翻译文本      : %d 条" % len(plan.units))
    print("  已是中文（跳过）: %d 条" % s.get("already_zh", 0))
    print("  非文本（跳过）  : %d 条" % s.get("not_text", 0))
    chars = sum(len(u.text) for u in plan.units)
    print("  总字符数        : %d（约 %.0f 万）" % (chars, chars / 10000))
    print("  预估输入 token  : %d" % int(chars / 3.2))
    if plan.warnings:
        print("  警告            : %d 条（详见报告）" % len(plan.warnings))


def main() -> None:
    try:
        raise SystemExit(run())
    except KeyboardInterrupt:
        print("\n已中断（已翻译的部分保存在缓存里，重跑会接着来）")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
