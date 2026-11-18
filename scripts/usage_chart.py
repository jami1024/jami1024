#!/usr/bin/env python3
"""生成 README 用的「AI 用量随模型变化」堆叠面积图（SVG）。

数据源：Tokscale 公开 JSON（https://tokscale.ai/api/users/<user>）中
contributions[].clients[].models，按天聚合各模型的 token 用量。

不依赖第三方库：直接拼 SVG 字符串，便于在 GitHub Actions 里零安装运行。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import urllib.request
from collections import Counter, defaultdict

API = "https://tokscale.ai/api/users/{user}"

# 深色卡片配色，与 Tokscale 卡片风格保持一致
BG = "#0d1117"
FG = "#e6edf3"
MUTED = "#8b949e"
GRID = "#21262d"
COLORS = [
    "#2ea043", "#3b82f6", "#a855f7", "#ef4444",
    "#eab308", "#06b6d4", "#f97316", "#ec4899",
]
OTHER_COLOR = "#64748b"


def fetch(user: str) -> dict:
    req = urllib.request.Request(API.format(user=user), headers={"User-Agent": "usage-chart"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def daily_series(contribs: list[dict], days: int) -> tuple[list[dt.date], dict[str, list[int]]]:
    """按天汇总各模型 token；返回连续日期轴与「模型 -> 每日 token 数」。"""
    by_date = {c["date"]: c for c in contribs if c.get("clients")}
    if not by_date:
        raise SystemExit("no contribution data")
    end = dt.date.fromisoformat(max(by_date))
    dates = [end - dt.timedelta(days=days - 1 - i) for i in range(days)]

    series: dict[str, list[int]] = defaultdict(lambda: [0] * len(dates))
    index = {d: i for i, d in enumerate(dates)}
    for day_text, c in by_date.items():
        day = dt.date.fromisoformat(day_text)
        if day not in index:
            continue
        i = index[day]
        for client in c["clients"]:
            for model, v in (client.get("models") or {}).items():
                series[model][i] += int(v.get("tokens") or 0)
    return dates, series


def top_series(series: dict[str, list[int]], limit: int) -> list[tuple[str, list[int]]]:
    """取总量最大的若干个模型，其余合并为 other。"""
    ranked = sorted(series.items(), key=lambda kv: sum(kv[1]), reverse=True)
    head, tail = ranked[:limit], ranked[limit:]
    result = list(head)
    if tail:
        merged = [sum(vals) for vals in zip(*(v for _, v in tail))]
        result.append(("other", merged))
    return result


def human(n: float) -> str:
    """1.2B / 250M / 32.1B 这类紧凑写法。"""
    for unit, scale in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= scale:
            v = n / scale
            return f"{v:.0f}{unit}" if v >= 100 else f"{v:.1f}{unit}".replace(".0" + unit, unit)
    return str(int(n))


def clip(name: str, limit: int = 22) -> str:
    """图例里过长的模型名做主断，避免与下一列重叠。"""
    return name if len(name) <= limit else name[: limit - 1] + "…"


def nice_ceiling(v: float) -> float:
    """把峰值向上取整到 1/2/5 × 10^k，让 y 轴刻度好读。"""
    if v <= 0:
        return 1.0
    exp = 10 ** len(str(int(v))) / 10
    for mult in (1, 2, 2.5, 5, 10):
        if v <= mult * exp:
            return mult * exp
    return 10 * exp


def render(dates: list[dt.date], series: list[tuple[str, list[int]]]) -> str:
    w, h = 880, 340
    left, right = 62, 24
    top, bottom = 96, 78  # 下方留给图例
    plot_w, plot_h = w - left - right, h - top - bottom
    n = len(dates)

    totals = [sum(vals[i] for _, vals in series) for i in range(n)]
    y_max = nice_ceiling(max(totals) or 1)

    def x(i: int) -> float:
        return left + plot_w * (i / max(n - 1, 1))

    def y(v: float) -> float:
        return top + plot_h * (1 - v / y_max)

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="ui-sans-serif,-apple-system,Segoe UI,Helvetica,Arial,sans-serif">',
        f'<rect width="{w}" height="{h}" rx="12" fill="{BG}"/>',
        f'<text x="24" y="36" fill="{FG}" font-size="16" font-weight="600">AI 用量随模型变化</text>',
        f'<text x="24" y="58" fill="{MUTED}" font-size="12">近 12 个月的每日 token 用量，按模型堆叠</text>',
        f'<text x="{w - 24}" y="32" fill="{MUTED}" font-size="11" text-anchor="end">区间合计 tokens</text>',
        f'<text x="{w - 24}" y="58" fill="{FG}" font-size="20" font-weight="600" '
        f'text-anchor="end">{human(sum(totals))}</text>',
    ]

    # y 轴网格与刻度
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        value = y_max * frac
        gy = y(value)
        parts.append(f'<line x1="{left}" y1="{gy:.1f}" x2="{w - right}" y2="{gy:.1f}" stroke="{GRID}"/>')
        parts.append(
            f'<text x="{left - 8}" y="{gy + 4:.1f}" fill="{MUTED}" font-size="10" '
            f'text-anchor="end">{human(value)}</text>'
        )

    # 堆叠面积：自下而上累积
    cum = [0.0] * n
    for idx, (name, vals) in enumerate(series):
        lower, upper = cum[:], [cum[i] + vals[i] for i in range(n)]
        cum = upper
        color = OTHER_COLOR if name == "other" else COLORS[idx % len(COLORS)]
        fwd = " ".join(f"{x(i):.1f},{y(upper[i]):.1f}" for i in range(n))
        back = " ".join(f"{x(i):.1f},{y(lower[i]):.1f}" for i in range(n - 1, -1, -1))
        parts.append(
            f'<polygon points="{fwd} {back}" fill="{color}" fill-opacity="0.55" stroke="{color}" stroke-width="1"/>'
        )

    # x 轴月份刻度（首个刻度带上年份，避免跨年时两处“11 月”混淆）
    months: list[tuple[float, str]] = []
    for i, d in enumerate(dates):
        if d.day == 1 or i == 0:
            label = f"{d.year}/{d.month}" if i == 0 else f"{d.month}月"
            months.append((x(i), label))
    parts.append(f'<line x1="{left}" y1="{top + plot_h}" x2="{w - right}" y2="{top + plot_h}" stroke="{GRID}"/>')
    for mx, label in months:
        parts.append(f'<text x="{mx:.1f}" y="{top + plot_h + 16:.1f}" fill="{MUTED}" font-size="10" '
                     f'text-anchor="middle">{label}</text>')

    # 图例（两行）
    per_row = 4
    for i, (name, _) in enumerate(series):
        color = OTHER_COLOR if name == "other" else COLORS[i % len(COLORS)]
        col, row = i % per_row, i // per_row
        lx = left + col * (plot_w / per_row)
        ly = top + plot_h + 40 + row * 18
        parts.append(f'<rect x="{lx:.1f}" y="{ly - 8:.1f}" width="8" height="8" rx="2" fill="{color}"/>')
        parts.append(f'<text x="{lx + 14:.1f}" y="{ly:.1f}" fill="{MUTED}" font-size="11">{clip(name)}</text>')

    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user", default="jami1024")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--top", type=int, default=7, help="单独展示的模型数，其余合并为 other")
    ap.add_argument("--out", default="assets/usage-over-time.svg")
    args = ap.parse_args()

    data = fetch(args.user)
    dates, series = daily_series(data["contributions"], args.days)
    picked = top_series(series, args.top)
    svg = render(dates, picked)

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8")
    total = sum(sum(v) for _, v in picked)
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB, {len(dates)} 天, {total / 1e9:.2f}B tokens)")


if __name__ == "__main__":
    main()
