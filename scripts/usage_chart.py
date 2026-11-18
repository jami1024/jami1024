#!/usr/bin/env python3
"""生成 README 用的「AI 用量」总览卡（单张 SVG，三栏并排）。

左栏累计、中栏最近 7 天、右栏近 12 个月按模型堆叠的趋势图。
数据源：Tokscale 公开 JSON（https://tokscale.ai/api/users/<user>），
统计值取自 stats，趋势取自 contributions[].clients[].models。

不依赖第三方库：直接拼 SVG 字符串，便于在 GitHub Actions 里零安装运行。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import urllib.request
from collections import defaultdict

API = "https://tokscale.ai/api/users/{user}?period={period}"

# 深色卡片配色，与 Tokscale 卡片风格保持一致
BG = "#0d1117"
FG = "#e6edf3"
MUTED = "#8b949e"
GRID = "#21262d"
COLORS = ["#2ea043", "#3b82f6", "#a855f7", "#ef4444", "#eab308"]
OTHER_COLOR = "#64748b"


def fetch(user: str, period: str) -> dict:
    req = urllib.request.Request(
        API.format(user=user, period=period), headers={"User-Agent": "usage-overview"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def daily_series(contribs: list[dict], days: int) -> tuple[list[dt.date], dict[str, list[int]]]:
    """按天汇总各模型 token；返回连续日期轴与「模型 -> 每日 token 数」。"""
    by_date = {c["date"]: c for c in contribs if c.get("clients")}
    if not by_date:
        raise SystemExit("no contribution data")
    end = max(dt.date.fromisoformat(d) for d in by_date)
    dates = [end - dt.timedelta(days=days - 1 - i) for i in range(days)]
    index = {d: i for i, d in enumerate(dates)}

    series: dict[str, list[int]] = defaultdict(lambda: [0] * len(dates))
    for day_text, c in by_date.items():
        i = index.get(dt.date.fromisoformat(day_text))
        if i is None:
            continue
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
        result.append(("other", [sum(vals) for vals in zip(*(v for _, v in tail))]))
    return result


def tokens_text(n: float) -> str:
    for unit, scale in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= scale:
            v = n / scale
            return f"{v:.0f}{unit}" if v >= 100 else f"{v:.1f}{unit}".replace(".0" + unit, unit)
    return str(int(n))


def money_text(n: float) -> str:
    return f"${n / 1e3:.1f}K" if n >= 1e3 else f"${n:,.2f}"


def clip(name: str, limit: int) -> str:
    return name if len(name) <= limit else name[: limit - 1] + "…"


def stat_panel(x: int, w: int, label: str, stats: dict, rank: int) -> list[str]:
    """左侧统计栏：标题、主数值、cost / rank 两行、活跃天数。"""
    right = x + w
    value = tokens_text(stats["totalTokens"])
    rows = [
        f'<text x="{x}" y="40" fill="{MUTED}" font-size="12">{label}</text>',
        f'<text x="{x}" y="78" fill="{FG}" font-size="30" font-weight="600">{value}</text>',
        f'<text x="{x + 18 * len(value) + 8}" y="78" fill="{MUTED}" font-size="11">tokens</text>',
        f'<line x1="{x}" y1="96" x2="{right}" y2="96" stroke="{GRID}"/>',
    ]
    for i, (name, v) in enumerate(
        [("cost", money_text(stats["totalCost"])), ("rank", f"#{rank}")]
    ):
        y = 122 + i * 24
        rows.append(f'<text x="{x}" y="{y}" fill="{MUTED}" font-size="11">{name}</text>')
        rows.append(
            f'<text x="{right}" y="{y}" fill="{FG}" font-size="13" font-weight="600" '
            f'text-anchor="end">{v}</text>'
        )
    rows.append(
        f'<text x="{x}" y="176" fill="{MUTED}" font-size="10">{stats["activeDays"]} active days</text>'
    )
    return rows


def trend_panel(x: int, w: int, dates: list[dt.date], series: list[tuple[str, list[int]]]) -> list[str]:
    """右侧趋势栏：堆叠面积图 + 3 个时间刻度 + 精简图例。"""
    top, bottom = 72, 146  # 绘图区上下边界
    n = len(dates)
    totals = [sum(vals[i] for _, vals in series) for i in range(n)]
    y_max = max(totals) or 1

    def px(i: int) -> float:
        return x + w * (i / max(n - 1, 1))

    def py(v: float) -> float:
        return bottom - (bottom - top) * (v / y_max)

    out = [
        f'<text x="{x}" y="40" fill="{FG}" font-size="12" font-weight="600">用量趋势</text>',
        f'<text x="{x}" y="56" fill="{MUTED}" font-size="10">近 12 个月 · 按模型堆叠</text>',
    ]
    for frac in (0.5, 1.0):
        gy = py(y_max * frac)
        out.append(f'<line x1="{x}" y1="{gy:.1f}" x2="{x + w}" y2="{gy:.1f}" stroke="{GRID}"/>')
    out.append(f'<line x1="{x}" y1="{bottom}" x2="{x + w}" y2="{bottom}" stroke="{GRID}"/>')

    cum = [0.0] * n
    for idx, (name, vals) in enumerate(series):
        lower, upper = cum[:], [cum[i] + vals[i] for i in range(n)]
        cum = upper
        color = OTHER_COLOR if name == "other" else COLORS[idx % len(COLORS)]
        fwd = " ".join(f"{px(i):.1f},{py(upper[i]):.1f}" for i in range(n))
        back = " ".join(f"{px(i):.1f},{py(lower[i]):.1f}" for i in range(n - 1, -1, -1))
        out.append(f'<polygon points="{fwd} {back}" fill="{color}" fill-opacity="0.6"/>')

    # 只标首/中/末三个时间点，窄栏里放不下更多
    for i in (0, n // 2, n - 1):
        d = dates[i]
        anchor = "start" if i == 0 else ("end" if i == n - 1 else "middle")
        out.append(
            f'<text x="{px(i):.1f}" y="{bottom + 16}" fill="{MUTED}" font-size="10" '
            f'text-anchor="{anchor}">{d.year}/{d.month}</text>'
        )

    # 图例：两行三列
    for i, (name, _) in enumerate(series):
        color = OTHER_COLOR if name == "other" else COLORS[i % len(COLORS)]
        lx = x + (i % 3) * (w / 3)
        ly = bottom + 44 + (i // 3) * 16
        out.append(f'<rect x="{lx:.1f}" y="{ly - 8:.1f}" width="7" height="7" rx="2" fill="{color}"/>')
        out.append(
            f'<text x="{lx + 12:.1f}" y="{ly:.1f}" fill="{MUTED}" font-size="10">{clip(name, 13)}</text>'
        )
    return out


def render(all_data: dict, week_data: dict, dates: list[dt.date], series: list[tuple[str, list[int]]]) -> str:
    w, h = 880, 232
    pad = 24
    stat_w = 240
    gap = 24
    trend_w = 256

    x1 = pad
    x2 = x1 + stat_w + gap
    x3 = w - pad - trend_w
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="ui-sans-serif,-apple-system,Segoe UI,Helvetica,Arial,sans-serif">',
        f'<rect width="{w}" height="{h}" rx="12" fill="{BG}"/>',
    ]
    parts += stat_panel(x1, stat_w, "累计", all_data["stats"], all_data["user"]["rank"])
    parts += stat_panel(x2, stat_w, "近 7 天", week_data["stats"], week_data["user"]["rank"])
    parts += trend_panel(x3, trend_w, dates, series)

    for divider in ((x2 - gap / 2), (x3 - gap / 2)):
        parts.append(
            f'<line x1="{divider:.1f}" y1="28" x2="{divider:.1f}" y2="{h - 28}" stroke="{GRID}"/>'
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user", default="jami1024")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--top", type=int, default=4, help="趋势图单独展示的模型数，其余合并为 other")
    ap.add_argument("--out", default="assets/usage-overview.svg")
    args = ap.parse_args()

    all_data = fetch(args.user, "all")
    week_data = fetch(args.user, "week")
    dates, series = daily_series(all_data["contributions"], args.days)
    picked = top_series(series, args.top)

    svg = render(all_data, week_data, dates, picked)
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8")
    total = sum(sum(v) for _, v in picked)
    print(f'wrote {out} ({out.stat().st_size / 1024:.0f} KB, {len(dates)} 天, {total / 1e9:.2f}B tokens, '
          f'7d {tokens_text(week_data["stats"]["totalTokens"])})')


if __name__ == "__main__":
    main()
