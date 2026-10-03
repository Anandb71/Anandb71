#!/usr/bin/env python3
"""Render the neofetch-style profile card (dark + light SVG) from live GitHub data.

The ASCII portrait is generated from the GitHub avatar on every run: background removed by
flood fill, colours quantised to the avatar's own palette, glyphs chosen by edge direction
and lit shading. Stats come from the GitHub GraphQL and REST APIs.

  python3 scripts/neofetch.py --output dist                 # live (needs GITHUB_TOKEN)
  python3 scripts/neofetch.py --output dist --fixture tests/fixtures/neofetch-data.json --avatar a.png
"""

from __future__ import annotations

import argparse
import base64
import colorsys
import html
import io
import json
import math
import os
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
GRAPHQL = "https://api.github.com/graphql"
REST = "https://api.github.com"
PREVIOUS_DATA_URL = "https://raw.githubusercontent.com/{user}/{user}/output/neofetch-data.json"

# ---------------------------------------------------------------- layout
FONT_PX = 14
ADV = 8.4  # Geist Mono advance is 600/1000 em
LH = 17
COLS, ROWS = 80, 40  # portrait cells
ART_PX = 10.0  # portrait font: 80 * 6.0 == 40 * 12.0, so the circle stays round
A_ADV, A_LH = ART_PX * 0.6, ART_PX * 1.2
PANEL = 62  # info panel width in characters
PAD = 26
TITLE_H = 38
WIDTH = 1100
ART_X = PAD + 6
INFO_X = ART_X + COLS * A_ADV + 30

RAMP = " .:-=+*#%@"
SPARK = "▁▂▃▄▅▆▇█"

THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#0B0F14", "bar": "#11161D", "border": "#232B35", "title": "#7D8590",
        "key": "#F5A524", "head": "#5CC8F0", "val": "#E6EDF3", "dot": "#3A434F",
        "mut": "#7D8590", "add": "#56D364", "del": "#F47067", "accent": "#7CF58A",
        "prompt": "#7CF58A", "path": "#5CC8F0", "shadow": "#000000",
        "spark": "#1F6F35,#2EA043,#56D364,#7CF58A",
    },
    "light": {
        "bg": "#FFFFFF", "bar": "#F6F8FA", "border": "#D0D7DE", "title": "#656D76",
        "key": "#A85A00", "head": "#0A6F9E", "val": "#1F2328", "dot": "#C2C9D1",
        "mut": "#656D76", "add": "#1A7F37", "del": "#CF222E", "accent": "#18883D",
        "prompt": "#18883D", "path": "#0A6F9E", "shadow": "#8C959F",
        "spark": "#9BE9A8,#40C463,#30A14E,#216E39",
    },
}


# ---------------------------------------------------------------- data
def _request(url: str, token: str, body: dict | None = None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method="POST" if body is not None else "GET",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "Anandb71-neofetch-card",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as err:
        return err.code, None


def graphql(token: str, query: str, variables: dict) -> dict:
    status, result = _request(GRAPHQL, token, {"query": query, "variables": variables})
    if status != 200 or not result:
        raise RuntimeError(f"GraphQL HTTP {status}")
    if result.get("errors"):
        raise RuntimeError("GraphQL errors: " + "; ".join(e.get("message", "?") for e in result["errors"]))
    return result["data"]


PROFILE_Q = """
query($login: String!, $owner: String!, $name: String!, $cursor: String) {
  user(login: $login) {
    createdAt
    avatarUrl(size: 460)
    followers { totalCount }
    publicRepos: repositories(privacy: PUBLIC, ownerAffiliations: OWNER) { totalCount }
    repositoriesContributedTo(privacy: PUBLIC, includeUserRepositories: false,
      contributionTypes: [COMMIT, PULL_REQUEST, REPOSITORY]) { totalCount }
    owned: repositories(privacy: PUBLIC, ownerAffiliations: OWNER, first: 100, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      nodes { name isFork stargazerCount }
    }
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
  repository(owner: $owner, name: $name) { stargazerCount forkCount }
}
"""

YEAR_Q = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) { totalCommitContributions }
  }
}
"""

ORG_Q = """
query($org: String!) {
  organization(login: $org) {
    repositories(privacy: PUBLIC, first: 100) { nodes { name isFork } }
  }
}
"""


def fetch_live(cfg: dict, token: str, previous: dict) -> dict:
    login = cfg["username"]
    feat = cfg["featured"]
    owned: list[dict] = []
    cursor = None
    first = None
    while True:
        data = graphql(token, PROFILE_Q, {"login": login, "owner": feat["owner"], "name": feat["name"], "cursor": cursor})
        first = first or data
        page = data["user"]["owned"]
        owned += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    user = first["user"]
    created = datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)

    commits = 0
    for year in range(created.year, now.year + 1):
        start = max(created, datetime(year, 1, 1, tzinfo=timezone.utc))
        end = min(now, datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc))
        yd = graphql(token, YEAR_Q, {"login": login, "from": start.isoformat(), "to": end.isoformat()})
        commits += int(yd["user"]["contributionsCollection"]["totalCommitContributions"])

    loc_repos = [(login, r["name"]) for r in owned if not r["isFork"]]
    for org in cfg.get("extraLocOwners", []):
        od = graphql(token, ORG_Q, {"org": org})
        loc_repos += [(org, r["name"]) for r in od["organization"]["repositories"]["nodes"] if not r["isFork"]]
    loc = lines_of_code(token, login, loc_repos, previous.get("locByRepo", {}))

    cal = user["contributionsCollection"]["contributionCalendar"]
    return {
        "username": login,
        "generatedAtUtc": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "createdAt": user["createdAt"],
        "avatarUrl": user["avatarUrl"],
        "followers": user["followers"]["totalCount"],
        "publicRepos": user["publicRepos"]["totalCount"],
        "contributedTo": user["repositoriesContributedTo"]["totalCount"],
        "stars": sum(int(r["stargazerCount"]) for r in owned),
        "commits": commits,
        "featured": {**feat, "stars": first["repository"]["stargazerCount"], "forks": first["repository"]["forkCount"]},
        "calendar": {
            "total": cal["totalContributions"],
            "days": [{"date": d["date"], "count": d["contributionCount"]} for w in cal["weeks"] for d in w["contributionDays"]],
        },
        "locByRepo": loc,
    }


def lines_of_code(token: str, login: str, repos: list[tuple[str, str]], cache: dict) -> dict:
    """Per-repo additions/deletions authored by `login` (REST stats/contributors, 202 = still computing)."""
    out: dict[str, list[int]] = {}
    pending = list(repos)
    for attempt in range(4):
        still = []
        for owner, name in pending:
            status, body = _request(f"{REST}/repos/{owner}/{name}/stats/contributors", token)
            key = f"{owner}/{name}"
            if status == 200 and isinstance(body, list):
                a = d = 0
                for entry in body:
                    if (entry.get("author") or {}).get("login", "").lower() == login.lower():
                        a += sum(w["a"] for w in entry["weeks"])
                        d += sum(w["d"] for w in entry["weeks"])
                out[key] = [a, d]
            elif status == 204:
                out[key] = [0, 0]  # empty repository
            else:
                still.append((owner, name))
        pending = still
        if not pending:
            break
        time.sleep(4 + 4 * attempt)
    for owner, name in pending:  # keep the last good number rather than dropping the repo
        key = f"{owner}/{name}"
        if key in cache:
            out[key] = cache[key]
    return out


def load_previous(username: str) -> dict:
    try:
        with urllib.request.urlopen(PREVIOUS_DATA_URL.format(user=username), timeout=20) as r:
            return json.load(r)
    except Exception:  # first run or offline: start without a cache
        return {}


# ---------------------------------------------------------------- portrait
def load_avatar(source: str) -> Image.Image:
    if source.startswith("http"):
        with urllib.request.urlopen(source, timeout=30) as r:
            return Image.open(io.BytesIO(r.read())).convert("RGB")
    return Image.open(source).convert("RGB")


def palette(pixels: np.ndarray, k: int = 6, iters: int = 12) -> np.ndarray:
    """Deterministic k-means with farthest-point seeding, so small distinct colours (eyes) keep a centre."""
    sample = pixels[:: max(1, len(pixels) // 20000)]
    quant = np.round(sample * 15) / 15
    values, counts = np.unique(quant, axis=0, return_counts=True)
    centres = [values[np.argmax(counts)]]
    for _ in range(k - 1):
        dist = np.min(((sample[:, None, :] - np.array(centres)[None]) ** 2).sum(-1), axis=1)
        centres.append(sample[np.argmax(dist)])
    centres = np.array(centres, dtype=np.float32)
    for _ in range(iters):
        lab = np.argmin(((sample[:, None, :] - centres[None]) ** 2).sum(-1), axis=1)
        for i in range(k):
            if np.any(lab == i):
                centres[i] = sample[lab == i].mean(0)
    # drop centres that hold almost nothing (anti-aliasing fringes)
    lab = np.argmin(((sample[:, None, :] - centres[None]) ** 2).sum(-1), axis=1)
    keep = [i for i in range(k) if (lab == i).mean() > 0.004]
    return centres[keep]


def portrait(img: Image.Image) -> dict:
    """Luminance-mapped ASCII (dark -> dense) carrying each cell's real colour.

    Black outline, eye band and nose come out as dense '@' masses and the lit face as light
    '=' / '-', which is what makes the avatar recognisable; colour then does the rest."""
    img = img.resize((COLS * 8, ROWS * 16), Image.LANCZOS)
    w, h = img.size
    marked = img.copy()
    for xy in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        ImageDraw.floodfill(marked, xy, (255, 0, 255), thresh=70)
    rgb = np.asarray(img).astype(np.float32) / 255.0
    yy, xx = np.mgrid[0:h, 0:w]
    inside = np.hypot((xx + 0.5) / w - 0.5, (yy + 0.5) / h - 0.5) <= 0.5
    fg = ~np.all(np.asarray(marked) == np.array([255, 0, 255]), axis=-1) & inside
    lum = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]

    pal = palette(rgb[fg])
    labels = np.argmin(((rgb.reshape(-1, 3)[:, None, :] - pal[None]) ** 2).sum(-1), axis=1).reshape(h, w)
    bg_col = rgb[~fg & inside].mean(0) if (~fg & inside).any() else None
    roles: dict[int, str] = {}
    for i, col in enumerate(pal):
        hh, ss, vv = colorsys.rgb_to_hsv(*col)
        if bg_col is not None and float(np.linalg.norm(np.asarray(col) - bg_col)) < 0.30:
            roles[i] = "bg"
        elif vv < 0.22:
            roles[i] = "ink"
        elif 0.5 < hh < 0.72 and ss > 0.5:
            roles[i] = "iris"
        else:
            roles[i] = "skin" if ss > 0.35 else "light"
    code = {"bg": 0, "ink": 1, "iris": 2, "skin": 3, "light": 4}
    R = np.array([code[roles[i]] for i in range(len(pal))])[labels]
    fg &= R != 0  # anti-aliased background fringe

    cw, ch = w // COLS, h // ROWS
    cells = []
    for r in range(ROWS):
        row = []
        for c in range(COLS):
            sl = (slice(r * ch, (r + 1) * ch), slice(c * cw, (c + 1) * cw))
            m = fg[sl]
            cov = float(m.mean())
            if cov < 0.35:
                row.append(None)
                continue
            labs = Counter(labels[sl][m].ravel().tolist())
            row.append({
                "lab": int(labs.most_common(1)[0][0]),
                "rgb": rgb[sl][m].mean(0).tolist(),
                "lum": float(lum[sl][m].mean()),
                "cov": cov,
            })
        cells.append(row)
    skin = max((i for i in roles if roles[i] == "skin"), key=lambda i: int((labels == i).sum()), default=None)
    eyes = eye_boxes(cells, roles)
    return {"cells": cells, "palette": pal.tolist(), "roles": roles, "skin": skin, "eyes": eyes}


def eye_boxes(cells: list[list[dict | None]], roles: dict) -> list[tuple[int, int, int, int]]:
    seen = set()
    boxes = []
    for r in range(ROWS):
        for c in range(COLS):
            cell = cells[r][c]
            if not cell or (r, c) in seen or roles.get(cell["lab"]) != "iris":
                continue
            stack, comp = [(r, c)], []
            seen.add((r, c))
            while stack:
                y, x = stack.pop()
                comp.append((y, x))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < ROWS and 0 <= nx < COLS and (ny, nx) not in seen:
                        nc = cells[ny][nx]
                        if nc and roles.get(nc["lab"]) == "iris":
                            seen.add((ny, nx))
                            stack.append((ny, nx))
            if len(comp) >= 8:
                ys, xs = [p[0] for p in comp], [p[1] for p in comp]
                boxes.append((min(ys), min(xs), max(ys), max(xs)))
    return sorted(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)[:2]


# luminance -> glyph. Ink is light on a dark terminal and dark on a light one, so the mapping flips:
# on dark, the lit face is solid and the black outline is a faint texture (it reads as black);
# on light, black is the densest mass, as in a classic ASCII conversion.
STEPS = {
    "dark": [(0.10, "."), (0.20, ":"), (0.32, "="), (0.42, "*"), (0.58, "#"), (0.66, "%")],
    "light": [(0.10, "@"), (0.20, "%"), (0.50, "#"), (0.64, "*"), (0.72, "+")],
}
TOP = {"dark": "@", "light": "="}


def glyph_for(cell: dict, role: str, theme: str) -> str:
    lum = cell["lum"]
    if cell.get("cov", 1.0) < 0.6:  # anti-alias the silhouette
        return ":" if theme == "dark" else "*"
    for limit, g in STEPS[theme]:
        if lum < limit:
            return g
    return TOP[theme]


def themed_color(rgb: list[float], role: str, lum: float, theme: str) -> str:
    h, l, s = colorsys.rgb_to_hls(*rgb)
    if theme == "dark":
        if lum < 0.16:  # the black outline: a dim graphite texture that still traces the shapes
            k = 1.0 + lum * 4.0
            return "#%02X%02X%02X" % (round(52 * k), round(60 * k), round(70 * k))
        l = min(0.72, max(l, 0.50))
        s = min(1.0, s * 1.08)
    else:
        if lum < 0.16:
            return "#1F2328"
        l = max(0.22, min(l, 0.46))
        s = min(1.0, s * 1.1)
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return "#%02X%02X%02X" % (round(r * 255), round(g * 255), round(b * 255))


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def fmt(n: int) -> str:
    return f"{n:,}"


def uptime(created: str, today: date) -> str:
    start = datetime.fromisoformat(created.replace("Z", "+00:00")).date()
    years = today.year - start.year - ((today.month, today.day) < (start.month, start.day))
    anchor = start.replace(year=start.year + years)
    months = (today.year - anchor.year) * 12 + today.month - anchor.month - (today.day < anchor.day)
    m_anchor_y, m_anchor_m = divmod(anchor.month - 1 + months, 12)
    m_anchor = date(anchor.year + m_anchor_y, m_anchor_m + 1, min(anchor.day, 28))
    days = (today - m_anchor).days
    part = lambda n, u: f"{n} {u}{'' if n == 1 else 's'}"
    return ", ".join([part(years, "year"), part(months, "month"), part(days, "day")])


def kv(key: str, value: str, width: int, value_cls: str = "v") -> list[tuple[str, str]]:
    dots = max(2, width - len(key) - 2 - len(value) - 1)
    return [(key, "k"), (": ", "d"), ("." * dots, "d"), (" ", "d"), (value, value_cls)]


def rule(label: str, width: int) -> list[tuple[str, str]]:
    return [("— ", "h"), (label, "h"), (" " + "─" * max(0, width - len(label) - 3), "rl")]


def streaks(days: list[dict], today: date) -> tuple[int, int]:
    counts = {date.fromisoformat(d["date"]): int(d["count"]) for d in days}
    longest = run = 0
    for day in sorted(counts):
        run = run + 1 if counts[day] > 0 else 0
        longest = max(longest, run)
    cur, cursor = 0, today
    if counts.get(cursor, 0) == 0:
        cursor -= timedelta(days=1)
    while counts.get(cursor, 0) > 0:
        cur += 1
        cursor -= timedelta(days=1)
    return cur, longest


def weekly(days: list[dict], n: int) -> list[int]:
    tot: dict[tuple[int, int], int] = defaultdict(int)
    for d in days:
        y, w, _ = date.fromisoformat(d["date"]).isocalendar()
        tot[(y, w)] += int(d["count"])
    return [v for _, v in sorted(tot.items())][-n:]


# ---------------------------------------------------------------- svg
def info_lines(cfg: dict, data: dict, today: date) -> list[list[tuple[str, str]]]:
    W = PANEL
    lines: list[list[tuple[str, str]]] = []
    host = f"{data['username'].lower()}@github"
    lines.append([(host, "h"), (" " + "─" * (W - len(host) - 1), "rl")])
    lines.append(kv("Uptime", uptime(data["createdAt"], today), W))
    for k, v in cfg["about"]:
        lines.append(kv(k, v, W))
    lines.append([])
    lines.append(rule("Contact", W))
    for k, v in cfg["contact"]:
        lines.append(kv(k, v, W))
    lines.append([])
    lines.append(rule("GitHub Stats", W))
    half_l, half_r = 33, W - 33 - 3
    left = kv("Repos", f"{fmt(data['publicRepos'])} {{Contributed: {fmt(data['contributedTo'])}}}", half_l)
    right = kv("Stars", fmt(data["stars"]), half_r)
    lines.append(left + [(" | ", "d")] + right)
    left = kv("Commits", fmt(data["commits"]), half_l)
    right = kv("Followers", fmt(data["followers"]), half_r)
    lines.append(left + [(" | ", "d")] + right)
    add = sum(v[0] for v in data["locByRepo"].values())
    dele = sum(v[1] for v in data["locByRepo"].values())
    loc_tail = f" ( {fmt(add)}++, {fmt(dele)}-- )"
    lines.append(kv("Lines of Code", fmt(add - dele), W - len(loc_tail)) + [(" ( ", "d"), (f"{fmt(add)}++", "add"), (", ", "d"), (f"{fmt(dele)}--", "del"), (" )", "d")])
    f = data["featured"]
    lines.append(kv(f"{f['label']} (OSS)", f"{fmt(f['stars'])} stars · {fmt(f['forks'])} forks", W, "acc"))
    cur, best = streaks(data["calendar"]["days"], today)
    lines.append(kv("Contributions", f"{fmt(data['calendar']['total'])} in the last year", W))
    lines.append(kv("Streak", f"{cur}d current · {best}d best", W))
    weeks = weekly(data["calendar"]["days"], 30)
    peak = max(weeks) or 1
    spark = [(SPARK[min(7, round(v / peak * 7))], f"s{min(3, int(v / peak * 4))}") for v in weeks]
    label = "Activity (30w)"
    lines.append([(label, "k"), (": ", "d"), ("." * (W - len(label) - 2 - len(weeks) - 1), "d"), (" ", "d")] + spark)
    return lines


def render(cfg: dict, data: dict, art: dict, theme: str, font_b64: str, today: date, static: bool = False) -> str:
    t = THEMES[theme]
    static_css = " *{animation:none!important}" if static else ""
    info = info_lines(cfg, data, today)
    body_top = TITLE_H + 34 + LH + 14  # title bar, prompt line, gap
    info_h = (len(info) + 1.4) * LH  # + the swatch row
    art_h = ROWS * A_LH
    body_h = max(info_h, art_h)
    art_y0 = body_top + (body_h - art_h) / 2
    height = int(body_top + body_h + LH + 40)

    pal = art["palette"]
    roles = art["roles"]
    eye_cells = {(r, c) for (r0, c0, r1, c1) in art["eyes"] for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)}

    def cell_span(cell: dict) -> tuple[str, str]:
        role = roles[cell["lab"]]
        return glyph_for(cell, role, theme), themed_color(cell["rgb"], role, cell["lum"], theme)

    def row_text(r: int, cols: range, y: float, cls: str, delay: float, override=None) -> str:
        # every run of same-coloured glyphs gets an explicit x, so no renderer can collapse the
        # leading/inner spaces that position the portrait
        runs: list[tuple[int, str, str]] = []  # (start column, glyphs, colour)
        for c in cols:
            cell = art["cells"][r][c]
            if override is not None:
                g, col = override(r, c)
            elif cell is None or ((r, c) in eye_cells and cls == "ar"):
                g, col = " ", None
            else:
                g, col = cell_span(cell)
            if col is None or g == " ":
                continue
            if runs and runs[-1][2] == col and runs[-1][0] + len(runs[-1][1]) == c:
                runs[-1] = (runs[-1][0], runs[-1][1] + g, col)
            else:
                runs.append((c, g, col))
        if not runs:
            return ""
        inner = "".join(f'<tspan x="{ART_X + c0 * A_ADV:.1f}" fill="{col}">{esc(g)}</tspan>' for c0, g, col in runs)
        return f'<text class="{cls}" y="{y:.1f}" style="animation-delay:{delay:.2f}s">{inner}</text>'

    out = []
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" '
        f'role="img" aria-labelledby="t d" xml:space="preserve">'
    )
    out.append(f"<title id=\"t\">{esc(data['username'])} · neofetch</title>")
    total_loc = sum(v[0] - v[1] for v in data["locByRepo"].values())
    out.append(
        f"<desc id=\"d\">Terminal-style profile card for Anand B: an ASCII portrait generated from the GitHub avatar, "
        f"profile facts, and live GitHub stats ({fmt(data['publicRepos'])} public repos, {fmt(data['stars'])} stars, "
        f"{fmt(data['commits'])} commits, {fmt(data['followers'])} followers, {fmt(total_loc)} net lines of code), "
        f"updated {data['generatedAtUtc'][:10]}.</desc>"
    )
    spark_cols = t["spark"].split(",")
    out.append(f"""<style>
@font-face{{font-family:"GM";src:url(data:font/woff2;base64,{font_b64}) format("woff2");font-weight:100 900}}
text{{font-family:"GM",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:{FONT_PX}px;fill:{t["val"]};white-space:pre}}
.k{{fill:{t['key']};font-weight:600}} .d{{fill:{t['dot']}}} .v{{fill:{t['val']}}} .h{{fill:{t['head']};font-weight:700}}
.rl{{fill:{t['dot']}}} .add{{fill:{t['add']}}} .del{{fill:{t['del']}}} .acc{{fill:{t['accent']};font-weight:600}}
.s0{{fill:{spark_cols[0]}}} .s1{{fill:{spark_cols[1]}}} .s2{{fill:{spark_cols[2]}}} .s3{{fill:{spark_cols[3]}}}
.pr{{fill:{t['prompt']};font-weight:700}} .pa{{fill:{t['path']};font-weight:700}} .tt{{fill:{t['title']};font-size:12.5px}}
.ar,.il,.eo{{animation:rise .5s cubic-bezier(.16,1,.3,1) backwards}}
.ar,.eo,.lidrow{{font-size:{ART_PX}px}} .lid{{opacity:0}}
@keyframes rise{{from{{opacity:0;transform:translateY(6px)}}to{{opacity:1;transform:none}}}}
@keyframes type{{from{{transform:translateX(0)}}to{{transform:translateX({len('neofetch --live') * ADV:.1f}px)}}}}
@keyframes blink{{50%{{opacity:0}}}}
@keyframes eyes{{0%,91%,97%,100%{{opacity:1}}93%,95%{{opacity:0}}}}
@keyframes lids{{0%,91%,97%,100%{{opacity:0}}93%,95%{{opacity:1}}}}
.cover{{transform:translateX({len('neofetch --live') * ADV:.1f}px);animation:type .45s steps({len('neofetch --live')}) .08s backwards}}
.cur{{animation:blink 1.1s steps(1) infinite}}
.eyes{{animation:eyes 6s 2.4s infinite}} .lid{{animation:lids 6s 2.4s infinite}}
@media (prefers-reduced-motion:reduce){{*{{animation:none!important}}}}{static_css}
</style>""")
    # window
    out.append(f'<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="14" fill="{t["bg"]}" stroke="{t["border"]}"/>')
    out.append(f'<path d="M14.5 .5H{WIDTH - 14.5}a14 14 0 0 1 14 14V{TITLE_H}H.5V14.5a14 14 0 0 1 14-14z" fill="{t["bar"]}"/>')
    out.append(f'<line x1="0.5" y1="{TITLE_H}" x2="{WIDTH - 0.5}" y2="{TITLE_H}" stroke="{t["border"]}"/>')
    for i, col in enumerate(("#FF5F57", "#FEBC2E", "#28C840")):
        out.append(f'<circle cx="{24 + i * 20}" cy="{TITLE_H / 2}" r="6" fill="{col}"/>')
    title = f"{cfg['host']}: ~ — neofetch"
    out.append(f'<text class="tt" x="{WIDTH / 2}" y="{TITLE_H / 2 + 4.5}" text-anchor="middle">{esc(title)}</text>')

    # prompt + typed command
    py = TITLE_H + 34
    ps1 = f"{cfg['host']}"
    out.append(
        f'<text x="{PAD}" y="{py}"><tspan class="pr">{esc(ps1)}</tspan><tspan class="d">:</tspan>'
        f'<tspan class="pa">~</tspan><tspan class="d">$ </tspan>neofetch --live</text>'
    )
    cmd_x = PAD + (len(ps1) + 4) * ADV
    out.append(
        f'<rect class="cover" x="{cmd_x:.1f}" y="{py - 13}" width="{len("neofetch --live") * ADV + 2:.1f}" height="17" fill="{t["bg"]}"/>'
    )

    # portrait rows
    for r in range(ROWS):
        y = art_y0 + r * A_LH + 9.5
        out.append(row_text(r, range(COLS), y, "ar", 0.55 + r * 0.018))
    # eyes (open) and lids (closed), animated against each other
    iris_rgb = next((c for i, c in enumerate(art["palette"]) if roles[i] == "iris"), [0.1, 0.5, 0.9])
    eo, lid = [], []
    for (r0, c0, r1, c1) in art["eyes"]:
        mid = (r0 + r1) // 2
        for r in range(r0, r1 + 1):
            y = art_y0 + r * A_LH + 9.5
            eo.append(row_text(r, range(c0, c1 + 1), y, "eo", 0.55 + r * 0.018))

            def lid_glyph(rr: int, cc: int, mid=mid, iris=iris_rgb):
                # closed eye: the dark eye band runs straight through, with the lash line in iris blue
                if art["cells"][rr][cc] is None:
                    return " ", None
                if rr == mid:
                    return "=", themed_color(iris, "iris", 0.45, theme)
                return ("." if theme == "dark" else "@"), themed_color([0.0, 0.0, 0.0], "ink", 0.02, theme)

            lid.append(row_text(r, range(c0, c1 + 1), y, "lidrow", 0, override=lid_glyph))
    out.append(f'<g class="eyes">{"".join(eo)}</g>')
    out.append(f'<g class="lid">{"".join(lid)}</g>')

    # info panel
    info_y0 = body_top + (body_h - info_h) / 2
    for i, segs in enumerate(info):
        if not segs:
            continue
        y = info_y0 + i * LH + 12
        inner = "".join(f'<tspan class="{cls}">{esc(txt)}</tspan>' for txt, cls in segs)
        out.append(f'<text class="il" x="{INFO_X:.1f}" y="{y:.1f}" style="animation-delay:{0.6 + i * 0.022:.2f}s">{inner}</text>')

    # palette strip (avatar colours + brand) and closing prompt
    sy = info_y0 + (len(info) + 0.6) * LH + 11
    swatches = [themed_color(c, roles[i], 0.75, theme) for i, c in enumerate(pal) if roles[i] != "ink"]
    swatches += [t["accent"], t["head"], t["key"], t["val"]]
    for i, col in enumerate(swatches[:10]):
        out.append(f'<rect class="il" style="animation-delay:{1.15 + i * 0.02:.2f}s" x="{INFO_X + i * 3 * ADV:.1f}" y="{sy - 11}" width="{3 * ADV - 2:.1f}" height="14" rx="2" fill="{col}"/>')
    fy = body_top + body_h + LH + 16
    out.append(
        f'<text x="{PAD}" y="{fy}"><tspan class="pr">{esc(ps1)}</tspan><tspan class="d">:</tspan>'
        f'<tspan class="pa">~</tspan><tspan class="d">$ </tspan></text>'
    )
    out.append(f'<rect class="cur" x="{PAD + (len(ps1) + 4) * ADV:.1f}" y="{fy - 12}" width="{ADV:.1f}" height="16" fill="{t["prompt"]}"/>')
    stamp = f"updated {data['generatedAtUtc'][:10]} · live via GitHub Actions"
    out.append(f'<text class="tt" x="{WIDTH - PAD}" y="{fy}" text-anchor="end">{esc(stamp)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=ROOT / "dist")
    ap.add_argument("--config", type=Path, default=ROOT / "config" / "profile.json")
    ap.add_argument("--fixture", type=Path, help="offline data instead of the GitHub API")
    ap.add_argument("--avatar", help="avatar file or URL (default: the account's avatar)")
    ap.add_argument("--today", help="YYYY-MM-DD override for reproducible renders")
    ap.add_argument("--static", action="store_true", help="final frame only (previews and thumbnails)")
    args = ap.parse_args()

    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    if args.fixture:
        data = json.loads(args.fixture.read_text(encoding="utf-8"))
    else:
        token = (os.environ.get("PROFILE_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
        if not token:
            raise SystemExit("PROFILE_TOKEN or GITHUB_TOKEN is required (or pass --fixture)")
        data = fetch_live(cfg, token, load_previous(cfg["username"]))
    today = date.fromisoformat(args.today) if args.today else datetime.now(timezone.utc).date()

    art = portrait(load_avatar(args.avatar or data["avatarUrl"]))
    font_b64 = base64.b64encode((ROOT / "assets" / "fonts" / "geist-mono-card.woff2").read_bytes()).decode()

    args.output.mkdir(parents=True, exist_ok=True)
    for theme in THEMES:
        svg = render(cfg, data, art, theme, font_b64, today, args.static)
        (args.output / f"neofetch-{theme}.svg").write_text(svg, encoding="utf-8")
    (args.output / "neofetch-data.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    loc = sum(v[0] - v[1] for v in data["locByRepo"].values())
    print(f"neofetch card: repos {data['publicRepos']}, stars {data['stars']}, commits {data['commits']}, "
          f"followers {data['followers']}, loc {loc} ({len(data['locByRepo'])} repos), eyes {len(art['eyes'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
