#!/usr/bin/env python3
"""生成 README 用的「AI 用量」等距 3D 卡（每周一列 × 7 行，每天一根立方体柱）。

数据源：Tokscale 公开 JSON（https://tokscale.ai/api/users/<user>）中的
contributions（逐日 token）与 stats / user.rank（汇总统计）。

柱子高度按日用量开方压缩：普通日子和峰值差两个数量级，线性缩放会让绝大多数
日子几乎贴地，看不出形状。不依赖第三方库，便于在 GitHub Actions 里零安装运行。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import urllib.request

API = "https://tokscale.ai/api/users/{user}?period=all"

BG, FG, MUTED, GRID = "#0d1117", "#e6edf3", "#8b949e", "#21262d"
EMPTY = "#141b24"
LEVELS = ["#123c24", "#1c6b39", "#2ea043", "#4ac26b", "#8ee6b4"]


def fetch(user: str) -> dict:
    req = urllib.request.Request(API.format(user=user), headers={"User-Agent": "usage-card"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def daily_totals(contribs: list[dict], days: int) -> list[tuple[dt.date, int, float]]:
    """取最近 days 天的连续日期轴（含无数据的 0 值日），带每天的 token 与成本。"""
    totals = {
        c["date"]: (int(c["totals"]["tokens"]), float(c["totals"]["cost"])) for c in contribs
    }
    if not totals:
        raise SystemExit("no contribution data")
    end = max(dt.date.fromisoformat(d) for d in totals)
    return [
        (d, *totals.get(d.isoformat(), (0, 0.0)))
        for d in (end - dt.timedelta(days=days - 1 - i) for i in range(days))
    ]


def ratio(tokens: int, peak: int) -> float:
    return (tokens / peak) ** 0.5 if tokens > 0 else 0.0


def level(tokens: int, peak: int) -> int:
    return 0 if tokens <= 0 else min(4, 1 + int(ratio(tokens, peak) * 3.999))


def human_tokens(n: float) -> str:
    if n >= 1e9:
        return f"{n / 1e9:.1f}B"
    return f"{n / 1e6:.1f}M"


def render(days: list[tuple[dt.date, int, float]], rank: int) -> str:
    w, h = 880, 440
    tw, th = 40, 20                     # 立方体顶面的宽 / 高（等距菱形）
    peak_h = 120                        # 最高柱子的像素高度
    area = (40, 76, w - 40, 352)        # 绘制区 (x0, y0, x1, y1)
    peak = max(t for _, t, _ in days) or 1

    cells = []
    for idx, (_, tokens, _) in enumerate(days):
        week, dow = idx // 7, idx % 7
        cx = (week - dow) * tw / 2
        cy = (week + dow) * th / 2      # 地面菱形中心
        cells.append((week + dow, cx, cy, ratio(tokens, peak) * peak_h if tokens else 0.0, tokens))
    cells.sort(key=lambda c: c[0])      # 由远及近绘制，保证遮挡关系正确

    # 按实际几何算包围盒，等比缩放居中到绘制区
    xs = [c[1] for c in cells]
    tops = [c[2] - c[3] - th / 2 for c in cells]
    bottoms = [c[2] + th / 2 for c in cells]
    bbox_w = max(xs) - min(xs) + tw
    bbox_h = max(bottoms) - min(tops)
    scale = min((area[2] - area[0]) / bbox_w, (area[3] - area[1]) / bbox_h)
    tx = area[0] + ((area[2] - area[0]) - bbox_w * scale) / 2 - (min(xs) - tw / 2) * scale
    ty = area[1] + ((area[3] - area[1]) - bbox_h * scale) / 2 - min(tops) * scale

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'font-family="ui-sans-serif,-apple-system,Segoe UI,Helvetica,Arial,sans-serif">',
        f'<rect width="{w}" height="{h}" rx="12" fill="{BG}"/>',
        f'<text x="28" y="42" fill="{FG}" font-size="15" font-weight="600">@jami1024</text>',
        f'<text x="{w - 28}" y="42" fill="{MUTED}" font-size="12" text-anchor="end">'
        f'{days[0][0].year}/{days[0][0].month} → {days[-1][0].year}/{days[-1][0].month}</text>',
        f'<g transform="translate({tx:.1f},{ty:.1f}) scale({scale:.4f})">',
    ]

    for _, cx, cy, height, tokens in cells:
        lv = level(tokens, peak)
        if lv == 0:
            out.append(
                f'<polygon points="{cx:.1f},{cy - th / 2:.1f} {cx + tw / 2:.1f},{cy:.1f} '
                f'{cx:.1f},{cy + th / 2:.1f} {cx - tw / 2:.1f},{cy:.1f}" fill="{EMPTY}"/>'
            )
            continue
        color = LEVELS[lv]
        ty_cell = cy - height           # 柱顶面中心
        out.append(
            f'<polygon points="{cx - tw / 2:.1f},{ty_cell:.1f} {cx:.1f},{ty_cell + th / 2:.1f} '
            f'{cx:.1f},{cy + th / 2:.1f} {cx - tw / 2:.1f},{cy:.1f}" fill="{color}" opacity="0.62"/>'
        )
        out.append(
            f'<polygon points="{cx + tw / 2:.1f},{ty_cell:.1f} {cx:.1f},{ty_cell + th / 2:.1f} '
            f'{cx:.1f},{cy + th / 2:.1f} {cx + tw / 2:.1f},{cy:.1f}" fill="{color}" opacity="0.85"/>'
        )
        out.append(
            f'<polygon points="{cx:.1f},{ty_cell - th / 2:.1f} {cx + tw / 2:.1f},{ty_cell:.1f} '
            f'{cx:.1f},{ty_cell + th / 2:.1f} {cx - tw / 2:.1f},{ty_cell:.1f}" fill="{color}"/>'
        )
    out.append("</g>")

    fy = h - 34
    out.append(f'<line x1="28" y1="{fy - 46}" x2="{w - 28}" y2="{fy - 46}" stroke="{GRID}"/>')
    # 前三项取本图窗口内的合计，和柱状图自洽；rank 是全局排名，只有累计口径
    items = [
        ("Tokens", human_tokens(sum(t for _, t, _ in days))),
        ("Cost", f'${sum(c for _, _, c in days) / 1e3:.1f}K'),
        ("Rank", f"#{rank}"),
        ("Active days", str(sum(1 for _, t, _ in days if t > 0))),
    ]
    for i, (label, value) in enumerate(items):
        x = 28 + i * (w - 56) / 4
        out.append(f'<text x="{x:.1f}" y="{fy}" fill="{MUTED}" font-size="11">{label}</text>')
        out.append(
            f'<text x="{x:.1f}" y="{fy + 21}" fill="{FG}" font-size="17" font-weight="600">{value}</text>'
        )
    out.append("</svg>")
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user", default="jami1024")
    ap.add_argument("--weeks", type=int, default=24)
    ap.add_argument("--out", default="assets/usage-overview.svg")
    args = ap.parse_args()

    data = fetch(args.user)
    days = daily_totals(data["contributions"], args.weeks * 7)
    svg = render(days, data["user"]["rank"])

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB, {len(days)} 天, "
          f"{sum(t for _, t, _ in days) / 1e9:.2f}B tokens, "
          f"{sum(1 for _, t, _ in days if t > 0)} active days)")


if __name__ == "__main__":
    main()
