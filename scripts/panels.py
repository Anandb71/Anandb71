#!/usr/bin/env python3
"""Terminal-style README panels that share the neofetch card's chrome, palette and font.

Writes, for each theme (dark/light): about, work-prompt, project-<slug>, loop, graph (wraps the
contribution snake generated earlier in the same run), outro and link-<id> buttons.

  python3 scripts/panels.py --output dist            # live project stats (needs GITHUB_TOKEN)
  python3 scripts/panels.py --output dist --offline  # no network; project stats omitted
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import neofetch as nf

W = 1100  # same canvas as the card, so every panel scales with it on GitHub
PAD = 26
TITLE_H = 38
ADV, LH = nf.ADV, nf.LH


def style(theme: str, font_b64: str, extra: str = "") -> str:
    t = nf.THEMES[theme]
    return f"""<style>
@font-face{{font-family:"GM";src:url(data:font/woff2;base64,{font_b64}) format("woff2");font-weight:100 900}}
text{{font-family:"GM",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:14px;fill:{t['val']};white-space:pre}}
.k{{fill:{t['key']};font-weight:600}} .d{{fill:{t['dot']}}} .v{{fill:{t['val']}}} .h{{fill:{t['head']};font-weight:700}}
.m{{fill:{t['mut']}}} .acc{{fill:{t['accent']};font-weight:600}} .add{{fill:{t['add']}}}
.pr{{fill:{t['prompt']};font-weight:700}} .pa{{fill:{t['path']};font-weight:700}} .tt{{fill:{t['title']};font-size:12.5px}}
.b{{font-weight:700}} .big{{font-size:28px;font-weight:800}} .cur{{animation:blink 1.1s steps(1) infinite}}
@keyframes blink{{50%{{opacity:0}}}}
@media (prefers-reduced-motion:reduce){{*{{animation:none!important}}}}{extra}
</style>"""


def window(width: int, height: int, title: str, theme: str, dots: float = 6) -> str:
    t = nf.THEMES[theme]
    bar = TITLE_H if dots >= 6 else 30
    return "".join([
        f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="14" fill="{t["bg"]}" stroke="{t["border"]}"/>',
        f'<path d="M14.5 .5H{width - 14.5}a14 14 0 0 1 14 14V{bar}H.5V14.5a14 14 0 0 1 14-14z" fill="{t["bar"]}"/>',
        f'<line x1="0.5" y1="{bar}" x2="{width - 0.5}" y2="{bar}" stroke="{t["border"]}"/>',
        *(f'<circle cx="{20 + i * dots * 3.2:.1f}" cy="{bar / 2}" r="{dots}" fill="{c}"/>' for i, c in enumerate(("#FF5F57", "#FEBC2E", "#28C840"))),
        f'<text class="tt" x="{width / 2}" y="{bar / 2 + 4.5}" text-anchor="middle">{nf.esc(title)}</text>',
    ])


def doc(width: int, height: int, title: str, desc: str, body: str, theme: str, font_b64: str, extra_css: str = "") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-labelledby="t d"><title id="t">{nf.esc(title)}</title><desc id="d">{nf.esc(desc)}</desc>'
        f"{style(theme, font_b64, extra_css)}{body}</svg>"
    )


def text(x: float, y: float, segs: list[tuple[str, str]], extra: str = "") -> str:
    inner = "".join(f'<tspan class="{c}">{nf.esc(s)}</tspan>' if c else nf.esc(s) for s, c in segs)
    return f'<text x="{x:.1f}" y="{y:.1f}"{extra}>{inner}</text>'


def prompt(host: str, cmd: str) -> list[tuple[str, str]]:
    return [(host, "pr"), (":", "d"), ("~", "pa"), ("$ ", "d"), (cmd, "v")]


def highlighted(line: str, phrases: list[str]) -> list[tuple[str, str]]:
    if not phrases:
        return [(line, "v")]
    pat = re.compile("|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True)))
    out, pos = [], 0
    for m in pat.finditer(line):
        if m.start() > pos:
            out.append((line[pos:m.start()], "v"))
        out.append((m.group(0), "acc" if m.group(0) == "Arbor" else "k"))
        pos = m.end()
    if pos < len(line):
        out.append((line[pos:], "v"))
    return out


def ago(iso: str | None) -> str:
    if not iso:
        return ""
    then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    days = (datetime.now(timezone.utc) - then).days
    if days <= 0:
        return "pushed today"
    if days < 31:
        return f"pushed {days}d ago"
    if days < 365:
        return f"pushed {days // 30}mo ago"
    return f"pushed {days // 365}y ago"


# ---------------------------------------------------------------- panels
def about(cfg: dict, theme: str, font: str) -> str:
    intro = cfg["intro"]
    lines: list[list[tuple[str, str]]] = [prompt(cfg["host"], "cat about.md"), []]
    lines.append([("# ", "d"), (intro["heading"], "h")])
    lines.append([])
    for para in intro["paragraphs"]:
        for ln in textwrap.wrap(para, 104):
            lines.append(highlighted(ln, intro.get("highlight", [])))
        lines.append([])
    for ln in textwrap.wrap(intro["quote"], 100):
        lines.append([("▌ ", "acc"), (ln, "m")])
    height = TITLE_H + 26 + len(lines) * LH + 22
    body = window(W, height, f"{cfg['host']}: ~ — about.md", theme)
    body += "".join(text(PAD, TITLE_H + 30 + i * LH, segs) for i, segs in enumerate(lines) if segs)
    desc = intro["heading"] + " " + " ".join(intro["paragraphs"]) + " " + intro["quote"]
    return doc(W, height, "about.md", desc, body, theme, font)


def work_prompt(cfg: dict, theme: str, font: str) -> str:
    n = len(cfg["projects"])
    body = text(PAD, 27, prompt(cfg["host"], "ls -la ~/work"))
    body += text(W - PAD, 27, [(f"# {n} entries · click one to open it", "m")], ' text-anchor="end"')
    t = nf.THEMES[theme]
    body += f'<line x1="{PAD}" y1="41.5" x2="{W - PAD}" y2="41.5" stroke="{t["border"]}" stroke-dasharray="2 4"/>'
    return doc(W, 46, "ls -la ~/work", "Selected work", body, theme, font)


STAR = "M0,-6.5 L1.9,-2 6.6,-2 2.9,1 4.2,5.8 0,3 -4.2,5.8 -2.9,1 -6.6,-2 -1.9,-2Z"


def project(cfg: dict, i: int, p: dict, live: dict, theme: str, font: str, desc_rows: int) -> str:
    t = nf.THEMES[theme]
    pw = 540
    cols = int((pw - 2 * 20) / ADV)
    rows = textwrap.wrap(p["desc"], cols)
    height = 30 + 22 + (3 + desc_rows) * LH + 22
    body = window(pw, height, f"~/work/{p['slug']}", theme, dots=5)
    y = 30 + 28
    body += text(20, y, [(f"{i + 1:02d}", "m"), ("  ", ""), (p["title"], "h")])
    info = live.get(p["repo"]) or {}
    if info:
        stars = nf.fmt(info.get("stargazerCount", 0))
        lang = (info.get("primaryLanguage") or {}).get("name")
        x_end = pw - 20
        body += text(x_end, y, [(stars, "acc")], ' text-anchor="end"')
        star_x = x_end - len(stars) * ADV - 10  # the star sits right before the count
        body += f'<path d="{STAR}" transform="translate({star_x:.1f},{y - 5})" fill="{t["accent"]}"/>'
        if lang:
            body += text(star_x - 14, y, [(lang, "m")], ' text-anchor="end"')
    y += LH + 4
    body += text(20, y, [(p["tagline"], "k")])
    for r in rows[:desc_rows]:
        y += LH
        body += text(20, y, [(r, "v")])
    y = height - 18
    tags: list[tuple[str, str]] = []
    for tag in p["tags"]:
        tags += [("[", "d"), (tag, "pa"), ("] ", "d")]
    body += text(20, y, tags)
    if info.get("pushedAt"):
        body += text(pw - 20, y, [(ago(info["pushedAt"]), "m")], ' text-anchor="end"')
    return doc(pw, height, f"{p['title']}: {p['tagline']}", f"{p['title']} — {p['tagline']} {p['desc']} Tags: {', '.join(p['tags'])}.", body, theme, font)


def loop(cfg: dict, theme: str, font: str) -> str:
    steps = cfg["loop"]
    bw, gap = 24, 5  # box width and arrow gap in characters
    width_ch = len(steps) * bw + (len(steps) - 1) * gap
    grid: list[list[tuple[str, str]]] = [[] for _ in range(4)]
    for n, (name, line) in enumerate(steps):
        label = f" {n + 1:02d} {name.lower()} "
        top = [("╭─", "d"), (label, "h"), ("─" * (bw - 3 - len(label)) + "╮", "d")]
        wrapped = textwrap.wrap(line, bw - 4) + ["", ""]
        mid1 = [("│ ", "d"), (wrapped[0].ljust(bw - 4), "v"), (" │", "d")]
        mid2 = [("│ ", "d"), (wrapped[1].ljust(bw - 4), "v"), (" │", "d")]
        bot = [("╰" + "─" * (bw - 2) + "╯", "d")]
        last = n == len(steps) - 1
        grid[0] += top + ([] if last else [(" " * gap, "")])
        grid[1] += mid1 + ([] if last else [(" ──▶ ", "acc")])
        grid[2] += mid2 + ([] if last else [(" " * gap, "")])
        grid[3] += bot + ([] if last else [(" " * gap, "")])
    # return path: up into the first box's centre from under the last one
    left_c, right_c = bw // 2, width_ch - bw // 2 - 1
    inner = right_c - left_c - 1
    word = " repeat "
    ret_up = [(" " * left_c, ""), ("▲", "acc"), (" " * inner, ""), ("│", "acc")]
    ret = [(" " * left_c, ""), ("╰", "acc"), ("─" * ((inner - len(word)) // 2), "acc"), (word, "m"),
           ("─" * (inner - len(word) - (inner - len(word)) // 2), "acc"), ("╯", "acc")]
    lines = [prompt(cfg["host"], "cat ~/.builder-loop"), []] + grid + [ret_up, ret, []]
    plines = cfg["principles"]
    lines.append([("principles", "k"), (" ", ""), ("." * 6, "d"), (" ", "")] + sum(([("+ ", "add"), (pl, "v"), ("   ", "")] for pl in plines[:2]), []))
    lines.append([(" " * 18, "")] + sum(([("+ ", "add"), (pl, "v"), ("   ", "")] for pl in plines[2:]), []))
    lines.append([("posture", "k"), (" ", ""), ("." * 9, "d"), (" ", ""), (cfg["posture"], "v")])
    lines.append([("# " + cfg["loopRule"], "m")])
    height = TITLE_H + 26 + len(lines) * LH + 22
    x0 = (W - width_ch * ADV) / 2
    body = window(W, height, f"{cfg['host']}: ~ — builder-loop", theme)
    for i, segs in enumerate(lines):
        if not segs:
            continue
        x = PAD if i < 2 or i >= len(lines) - 4 else x0
        body += text(x, TITLE_H + 30 + i * LH, segs)
    desc = "The builder loop: " + "; ".join(f"{a}: {b}" for a, b in steps) + ". Principles: " + ", ".join(plines) + ". " + cfg["posture"]
    return doc(W, height, "builder loop", desc, body, theme, font)


def graph(cfg: dict, theme: str, font: str, snake_svg: str | None, total: int | None) -> str:
    lines_h = TITLE_H + 26 + 2 * LH
    inner_w = W - 2 * PAD
    snake = ""
    snake_h = 0.0
    if snake_svg:
        m_w = re.search(r'viewBox="([\d.\s-]+)"', snake_svg)
        vb = [float(v) for v in m_w.group(1).split()] if m_w else [0, 0, 880, 192]
        snake_h = inner_w * vb[3] / vb[2]
        inner = re.sub(r"^.*?<svg[^>]*>|</svg>\s*$", "", snake_svg.strip(), flags=re.S)
        snake = (f'<svg x="{PAD}" y="{lines_h:.1f}" width="{inner_w}" height="{snake_h:.1f}" '
                 f'viewBox="{" ".join(str(v) for v in vb)}">{inner}</svg>')
    height = int(lines_h + snake_h + 26)
    body = window(W, height, f"{cfg['host']}: ~ — git log --graph", theme)
    body += text(PAD, TITLE_H + 30, prompt(cfg["host"], 'git log --graph --since="1 year ago"'))
    if total is not None:
        body += text(W - PAD, TITLE_H + 30, [(f"# {nf.fmt(total)} contributions", "m")], ' text-anchor="end"')
    body += snake
    return doc(W, height, "contribution graph", "An animated snake eating through the last year of GitHub contributions.", body, theme, font)


def outro(cfg: dict, theme: str, font: str) -> str:
    height = TITLE_H + 26 + 5 * LH + 62
    body = window(W, height, f"{cfg['host']}: ~ — echo", theme)
    y = TITLE_H + 30
    body += text(PAD, y, prompt(cfg["host"], 'echo "$MOTTO"'))
    y += 44
    body += text(PAD, y, [(cfg["motto"][0], "v")], ' class="big"')
    y += 38
    body += text(PAD, y, [(cfg["motto"][1] + " ", "acc"), ("→ getarbor.dev", "pa")], ' class="big"')
    y += 40
    host = cfg["host"]
    body += text(PAD, y, prompt(host, ""))
    t = nf.THEMES[theme]
    body += f'<rect class="cur" x="{PAD + (len(host) + 4) * ADV:.1f}" y="{y - 12}" width="{ADV:.1f}" height="16" fill="{t["prompt"]}"/>'
    return doc(W, height, "Every change has a reach.", " ".join(cfg["motto"]) + " getarbor.dev", body, theme, font)


def link(cfg: dict, item: dict, theme: str, font: str) -> str:
    t = nf.THEMES[theme]
    label = f"{item['id']} {item['label']} ↗"
    width = int(len(label) * ADV + 30)
    height = 36
    body = (f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="9" fill="{t["bar"]}" stroke="{t["border"]}"/>'
            + text(15, 23, [(item["id"], "k"), (" ", ""), (item["label"], "v"), (" ↗", "m")]))
    return doc(width, height, f"{item['id']}: {item['label']}", f"Link to {item['label']}", body, theme, font)


# ---------------------------------------------------------------- data
def fetch_projects(cfg: dict, token: str) -> dict:
    parts = []
    for n, p in enumerate(cfg["projects"]):
        owner, name = p["repo"].split("/")
        parts.append(f'r{n}: repository(owner: "{owner}", name: "{name}") {{ stargazerCount forkCount pushedAt primaryLanguage {{ name }} }}')
    data = nf.graphql(token, "query {" + " ".join(parts) + "}", {})
    return {p["repo"]: data.get(f"r{n}") for n, p in enumerate(cfg["projects"])}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=nf.ROOT / "dist")
    ap.add_argument("--config", type=Path, default=nf.ROOT / "config" / "profile.json")
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    font = base64.b64encode((nf.ROOT / "assets" / "fonts" / "geist-mono-card.woff2").read_bytes()).decode()

    live: dict = {}
    if not args.offline:
        token = (os.environ.get("PROFILE_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
        if token:
            live = fetch_projects(cfg, token)

    total = None
    data_file = args.output / "neofetch-data.json"
    if data_file.exists():
        total = json.loads(data_file.read_text(encoding="utf-8"))["calendar"]["total"]

    desc_rows = max(len(textwrap.wrap(p["desc"], int((540 - 40) / ADV))) for p in cfg["projects"])
    args.output.mkdir(parents=True, exist_ok=True)
    written = 0
    for theme in nf.THEMES:
        snake_name = "github-contribution-grid-snake-dark.svg" if theme == "dark" else "github-contribution-grid-snake.svg"
        snake_path = args.output / snake_name
        snake = snake_path.read_text(encoding="utf-8") if snake_path.exists() else None
        out = {
            f"about-{theme}.svg": about(cfg, theme, font),
            f"work-{theme}.svg": work_prompt(cfg, theme, font),
            f"loop-{theme}.svg": loop(cfg, theme, font),
            f"graph-{theme}.svg": graph(cfg, theme, font, snake, total),
            f"outro-{theme}.svg": outro(cfg, theme, font),
        }
        for n, p in enumerate(cfg["projects"]):
            out[f"project-{p['slug']}-{theme}.svg"] = project(cfg, n, p, live, theme, font, desc_rows)
        for item in cfg["links"]:
            out[f"link-{item['id']}-{theme}.svg"] = link(cfg, item, theme, font)
        for name, svg in out.items():
            (args.output / name).write_text(svg, encoding="utf-8")
            written += 1
    print(f"panels: {written} files, live project stats for {sum(1 for v in live.values() if v)} repos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
