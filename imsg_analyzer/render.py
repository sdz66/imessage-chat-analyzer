"""Render stats to Markdown and transcripts to an LLM-friendly condensed form."""

from __future__ import annotations

import json
from datetime import date as _date
from typing import Optional

from .parser import Message, SYSTEM

WEEKDAY_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _sender_label(sender: str, mapping: dict[str, str]) -> str:
    return mapping.get(sender, sender)


def render_condensed(messages: list[Message],
                     mapping: Optional[dict[str, str]] = None) -> str:
    """One line per message, media collapsed to tags, quoted context dropped."""
    mapping = dict(mapping or {})
    mapping.setdefault("Me", "我")
    out: list[str] = []
    cur_day = None
    for m in messages:
        if m.is_system:
            continue
        d = m.dt.date()
        if d != cur_day:
            cur_day = d
            out.append(f"\n===== {d} ({WEEKDAY_ZH[d.weekday()]}) =====")
        parts = []
        if m.images:
            parts.append(f"[图×{m.images}]")
        if m.videos:
            parts.append(f"[视频×{m.videos}]")
        if m.stickers:
            parts.append(f"[贴纸×{m.stickers}]")
        if m.dtouch:
            parts.append("[手绘]")
        if m.locations:
            parts.append("[位置]")
        parts.extend(m.text)
        body = " ".join(parts).strip()
        if not body:
            continue
        tag = "↩" if m.is_reply else " "
        out.append(f"{m.dt:%H:%M} {tag}{_sender_label(m.sender, mapping)}: {body}")
    return "\n".join(out) + "\n"


def _bar(value: int, max_value: int, width: int = 30) -> str:
    if max_value <= 0 or value <= 0:
        return ""
    return "█" * round(width * value / max_value)


def render_stats_md(stats: dict) -> str:
    if not stats.get("total_messages"):
        return "# 统计报告\n\n(没有解析到任何消息)\n"

    lines: list[str] = []
    ap = lines.append
    name = stats.get("name") or "会话"
    ap(f"# 聊天统计：{name}\n")
    ap(f"**时间跨度**: {stats['date_start']} ~ {stats['date_end']}"
       f"（{stats['active_days']} 天有消息）")
    ap(f"**总消息数**: {stats['total_messages']}\n")

    ap("## 双方数据\n")
    ap("| 指标 | " + " | ".join(stats["per_sender"]) + " |")
    ap("|---|" + "---|" * len(stats["per_sender"]))
    rows = [
        ("消息数", "messages"), ("文字消息", "text_messages"), ("打字量(字符)", "chars"),
        ("图片", "images"), ("视频", "videos"), ("贴纸", "stickers"),
        ("手绘", "digital_touch"), ("位置分享", "locations"),
        ("引用回复", "replies"), ("提问数", "questions"),
    ]
    for label, key in rows:
        ap(f"| {label} | " + " | ".join(
            str(stats["per_sender"][s][key]) for s in stats["per_sender"]) + " |")
    ap("")

    ap("### 高频 Emoji\n")
    for s, info in stats["per_sender"].items():
        tops = " ".join(f"{e}×{c}" for e, c in info["top_emoji"]) or "（无）"
        ap(f"- **{s}**: {tops}")
    ap("")

    ap("## 节奏\n")
    lat = stats["reply_latency_min"]
    for s, v in lat.items():
        if v["median"] is not None:
            ap(f"- **{s}** 回复他人：中位 {v['median']:.1f} 分钟"
               f" / 平均 {v['mean']:.1f} 分钟（n={v['n']}）")
    rd = stats["read_delay_min"]
    if rd["owner_read_other_median"] is not None:
        ap(f"- 本机读对方消息的中位延迟：{rd['owner_read_other_median']:.1f} 分钟")
    if rd["other_read_owner_median"] is not None:
        ap(f"- 对方读本机消息的中位延迟：{rd['other_read_owner_median']:.1f} 分钟")

    sess = stats["sessions"]
    ap(f"- 会话数（间隔≥{sess['gap_hours']:g}h 切分）：{sess['count']}，"
       f"平均 {sess['avg_len']:.0f} 条/会话")
    ap(f"- 开启会话：{sess['openers']}；收尾会话（最后一条）：{sess['closers']}")
    ap(f"- 深夜会话（含 23:00–02:00 消息）：{sess['night_sessions']}/{sess['count']}")
    lg = sess["longest"]
    ap(f"- 最长会话：{lg['count']} 条（{lg['start'][:10]} "
       f"{lg['start'][11:16]} → {lg['end'][11:16]}）")
    ap(f"- 每天第一条消息归属：{sess['first_message_of_day']}\n")

    ap("## 最活跃的 10 天\n")
    for d, c in stats["per_day_top"]:
        wd = WEEKDAY_ZH[_date.fromisoformat(d).weekday()]
        ap(f"- {d}（{wd}）：{c} 条")
    ap("")

    ap("## 小时分布\n")
    ap("```")
    hours = stats["hours"]
    mx = max(hours.values()) or 1
    for h in range(24):
        ap(f"{h:02d}h {_bar(hours[h], mx):<30} {hours[h]}")
    ap("```\n")

    if stats["effects"]:
        ap(f"**消息特效**: {stats['effects']}\n")
    if stats["tapbacks"]:
        ap(f"**Tapback**: {stats['tapbacks']}\n")
    return "\n".join(lines) + "\n"


def render_index(entries: list[dict]) -> str:
    lines = ["# 导出目录总览\n",
             "| 会话 | 消息数 | 时间跨度 | 最活跃一天 |",
             "|---|---|---|---|"]
    for e in sorted(entries, key=lambda x: x["total"], reverse=True):
        top = e["top_day"][0] if e["top_day"] else "-"
        lines.append(f"| {e['name']} | {e['total']} | {e['start']} ~ {e['end']} | {top} |")
    return "\n".join(lines) + "\n"


def dump_json(stats: dict) -> str:
    return json.dumps(stats, ensure_ascii=False, indent=2, default=str)
