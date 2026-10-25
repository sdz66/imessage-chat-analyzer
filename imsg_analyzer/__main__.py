"""CLI: python3 -m imsg_analyzer PATH [PATH ...] [-o OUT]

PATH may be a single transcript file or a directory of *.txt exports
(imessage-exporter default output: ~/imessage_export).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .parser import parse_file
from .render import dump_json, render_condensed, render_index, render_stats_md
from .stats import compute_stats


def _parse_map(pairs: list[str] | None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--map 需要 A=B 形式，得到: {pair}")
        k, v = pair.split("=", 1)
        mapping[k.strip()] = v.strip()
    return mapping


def analyze_one(path: Path, out_dir: Path, gap: float, top: int,
                mapping: dict[str, str], name: str | None = None) -> dict:
    messages = parse_file(str(path))
    stats = compute_stats(messages, gap_hours=gap, top_emoji=top,
                          name=name or path.stem)
    stem = path.stem.replace("/", "_")
    (out_dir / f"{stem}.stats.md").write_text(render_stats_md(stats), encoding="utf-8")
    (out_dir / f"{stem}.stats.json").write_text(dump_json(stats), encoding="utf-8")
    (out_dir / f"{stem}.condensed.txt").write_text(
        render_condensed(messages, mapping), encoding="utf-8")
    per_day = stats.get("per_day_top") or []
    return {
        "name": path.stem,
        "total": stats.get("total_messages", 0),
        "start": stats.get("date_start", "-"),
        "end": stats.get("date_end", "-"),
        "top_day": [d for d, _ in per_day[:1]],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="imsg-analyzer",
        description="统计并精简 imessage-exporter 导出的 txt 聊天记录（纯标准库）",
    )
    ap.add_argument("paths", nargs="+", help="导出的 txt 文件或包含 txt 的目录")
    ap.add_argument("-o", "--out", default="imsg_analysis", help="输出目录")
    ap.add_argument("--gap", type=float, default=4.0,
                    help="会话切分阈值（小时），默认 4")
    ap.add_argument("--top", type=int, default=10, help="高频 emoji 展示数，默认 10")
    ap.add_argument("--map", action="append", metavar="原名=显示名",
                    help="转写中的发送者别名，可重复")
    ap.add_argument("--name", help="报告标题（默认用文件名）")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)

    mapping = _parse_map(args.map)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    files: list[Path] = []
    for p in (Path(p).expanduser() for p in args.paths):
        if p.is_dir():
            found = sorted(p.glob("*.txt"))
            if not found:
                print(f"警告: 目录 {p} 下没有 txt 文件", file=sys.stderr)
            files.extend(found)
        elif p.exists():
            files.append(p)
        else:
            print(f"警告: 路径不存在 {p}", file=sys.stderr)
    if not files:
        ap.error("没有可分析的文件")

    entries = []
    for f in files:
        try:
            entries.append(analyze_one(f, out_dir, args.gap, args.top, mapping,
                                       args.name if len(files) == 1 else None))
            print(f"完成: {f.name} -> {out_dir}/")
        except Exception as exc:  # keep going on odd files
            print(f"失败: {f.name}: {exc}", file=sys.stderr)

    if len(entries) > 1:
        (out_dir / "index.md").write_text(render_index(entries), encoding="utf-8")
        print(f"总览: {out_dir}/index.md ({len(entries)} 个会话)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
