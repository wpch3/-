"""质检（QA）：把机翻结果里最容易出问题的地方挑出来。

参考了三个现成工具都在做的事：
- xTranslator 的 Alias Check：原文与译文里的 `<Alias=...>` 等标记必须一致；
- ESP-ESM Translator 的「禁止字符 / 空格标点」检查；
- 我们额外用 xTranslator 的字段长度参考值做「超长截断」预警。
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

from .fields import is_enumerated, max_length
from .pipeline import Plan
from .textfilter import contains_cjk, token_signature

ERROR = "错误"
WARN = "警告"
INFO = "提示"


@dataclass
class Issue:
    level: str
    kind: str
    context: str
    source: str
    target: str
    detail: str = ""


def _clip(text: str, n: int = 60) -> str:
    text = text.replace("\n", " / ")
    return text if len(text) <= n else text[: n - 1] + "…"


def check_plan(plan: Plan) -> List[Issue]:
    issues: List[Issue] = []
    by_source: Dict[str, Counter] = defaultdict(Counter)

    for unit, source, target in plan.applied:
        ctx = "%s [%s]" % (unit.source, unit.uid)

        # 1) 占位符 / 富文本标记一致性（<Alias=...>、%s、[PLYR]…）
        sig_src = token_signature(source)
        sig_dst = token_signature(target)
        if sig_src != sig_dst:
            issues.append(
                Issue(
                    ERROR,
                    "标记不一致",
                    ctx,
                    source,
                    target,
                    "原文 %s / 译文 %s" % (list(sig_src), list(sig_dst)),
                )
            )

        # 2) 没翻（译文里没有中文）
        if not contains_cjk(target):
            issues.append(Issue(WARN, "未翻译", ctx, source, target))

        # 3) 空译文 / 与原文相同
        if not target.strip():
            issues.append(Issue(ERROR, "空译文", ctx, source, target))
        elif target.strip() == source.strip():
            issues.append(Issue(WARN, "与原文相同", ctx, source, target))

        # 4) 超长（用官方内容里同字段的最大长度做参考）
        limit = max_length(unit.rec_sig, unit.sub_sig) if unit.rec_sig else 0
        if limit and limit < 12000 and len(target) > limit:
            issues.append(
                Issue(
                    WARN,
                    "可能超长",
                    ctx,
                    source,
                    target,
                    "译文 %d 字 > 官方同字段最长 %d 字，界面可能截断" % (len(target), limit),
                )
            )

        # 5) 换行数量变化（书籍/对话排版）
        if source.count("\n") != target.count("\n"):
            issues.append(
                Issue(
                    INFO,
                    "换行数变化",
                    ctx,
                    source,
                    target,
                    "原文 %d 个换行，译文 %d 个" % (source.count("\n"), target.count("\n")),
                )
            )

        # 6) 首尾空格丢失（部分 UI 靠空格排版）
        if source != source.strip() and target == target.strip():
            issues.append(Issue(INFO, "首尾空格丢失", ctx, source, target))

        by_source[source][target] += 1

    # 7) 同一句原文出现多种译法（术语/枚举文本不一致）
    for source, targets in by_source.items():
        if len(targets) <= 1:
            continue
        unit_sig = next(
            ((u.rec_sig, u.sub_sig) for u, s, _t in plan.applied if s == source), ("", "")
        )
        level = WARN if is_enumerated(*unit_sig) else INFO
        detail = "；".join("%s ×%d" % (_clip(t, 40), c) for t, c in targets.most_common(4))
        issues.append(
            Issue(level, "同原文多译法", "全包", source, targets.most_common(1)[0][0], detail)
        )

    return issues


def summarize(issues: List[Issue]) -> Dict[str, int]:
    counter = Counter(issue.kind for issue in issues)
    counter["总计"] = len(issues)
    counter["错误"] = sum(1 for i in issues if i.level == ERROR)
    counter["警告"] = sum(1 for i in issues if i.level == WARN)
    return dict(counter)


def write_report(issues: List[Issue], path: Path, stats: Dict[str, int] | None = None) -> None:
    summary = summarize(issues)
    lines = [
        "# 汉化质检报告",
        "",
        "- 检查条目：%d 条译文" % (stats.get("applied", 0) if stats else 0),
        "- 错误：%d；警告：%d；提示：%d"
        % (summary.get("错误", 0), summary.get("警告", 0), summary.get("总计", 0) - summary.get("错误", 0) - summary.get("警告", 0)),
        "",
    ]
    kinds = sorted(
        ((kind, count) for kind, count in summary.items() if kind not in ("总计", "错误", "警告")),
        key=lambda kv: -kv[1],
    )
    if kinds:
        lines += ["| 类型 | 数量 |", "| --- | --- |"]
        lines += ["| %s | %d |" % (kind, count) for kind, count in kinds]
        lines.append("")

    shown = 0
    for level in (ERROR, WARN, INFO):
        group = [i for i in issues if i.level == level]
        if not group:
            continue
        lines += ["## %s（%d）" % (level, len(group)), ""]
        for issue in group[:40]:
            lines.append(
                "- **[%s]** `%s`\n  - 原文：%s\n  - 译文：%s\n  - %s"
                % (issue.kind, issue.context, _clip(issue.source, 80), _clip(issue.target, 80), issue.detail)
            )
            shown += 1
        if len(group) > 40:
            lines.append("- ……其余 %d 条见 CSV" % (len(group) - 40))
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_csv(issues: List[Issue], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["级别", "类型", "位置", "原文", "译文", "说明"])
        for issue in issues:
            writer.writerow([issue.level, issue.kind, issue.context, issue.source, issue.target, issue.detail])
    return len(issues)
