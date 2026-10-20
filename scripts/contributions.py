#!/usr/bin/env python3
"""Render the public contribution signal from a checked-in GitHub snapshot.

Every chart node is a real pull request; states and repository counts come only
from the input. No network or third-party Python packages are required.

  python scripts/contributions.py --data config/contributions.json --output assets/oss
"""

from __future__ import annotations

import argparse
import base64
import html
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WIDTH, HEIGHT = 960, 700
STATES = ("MERGED", "OPEN", "CLOSED")

# The profile's neofetch palette, with GitHub's purple/green/red state semantics.
THEMES = {
    "dark": {
        "bg": "#0B0F14", "bar": "#11161D", "border": "#232B35",
        "text": "#E6EDF3", "muted": "#9BA7B4", "subtle": "#18222C",
        "cyan": "#5CC8F0", "lime": "#7CF58A", "merged": "#C5A3FF",
        "open": "#7CF58A", "closed": "#F47067",
    },
    "light": {
        "bg": "#FFFFFF", "bar": "#F6F8FA", "border": "#D0D7DE",
        "text": "#1F2328", "muted": "#57606A", "subtle": "#EAEFF3",
        "cyan": "#0A6F9E", "lime": "#18883D", "merged": "#8250DF",
        "open": "#18883D", "closed": "#CF222E",
    },
}


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def summary(data: dict) -> dict:
    """Count the exact snapshot, rejecting malformed or duplicate PR records."""
    prs = data["pullRequests"]
    if not isinstance(prs, list):
        raise ValueError("pullRequests must be an array")
    seen = set()
    states: Counter = Counter()
    repos: Counter = Counter()
    latest: dict[str, str] = {}
    for pr in prs:
        state = pr["state"].upper()
        if state not in STATES:
            raise ValueError(f"Unknown pull request state: {state}")
        key = pr["url"]
        if key in seen:
            raise ValueError(f"Duplicate pull request: {key}")
        seen.add(key)
        repo = pr["repository"]["nameWithOwner"]
        if "/" not in repo:
            raise ValueError(f"Invalid repository name: {repo}")
        states[state] += 1
        repos[repo] += 1
        latest[repo] = max(latest.get(repo, ""), pr.get("createdAt") or "")
    ranking = sorted(repos, key=str.casefold)
    # Recency breaks volume ties; sorting twice keeps counts the primary key.
    ranking.sort(key=lambda repo: latest[repo], reverse=True)
    ranking.sort(key=lambda repo: repos[repo], reverse=True)
    return {"total": len(prs), "states": states, "repos": repos, "ranking": ranking}


def text(x: float, y: float, label: object, size: float = 32, color: str = "",
         weight: int = 500, anchor: str = "start", extra: str = "") -> str:
    fill = f' fill="{color}"' if color else ""
    return (f'<text x="{x:g}" y="{y:g}" font-size="{size:g}" font-weight="{weight}"'
            f' text-anchor="{anchor}"{fill}{extra}>{esc(label)}</text>')


def short(label: str, limit: int) -> str:
    return label if len(label) <= limit else label[:limit - 3] + "..."


def rail(state: str, count: int, y: int, colors: dict) -> str:
    """A marker per PR, preserving exact counts even for dense histories."""
    color = colors[state.lower()]
    x1, x2 = 342, 914
    out = [
        text(44, y + 10, state, 30, color, 700),
        text(284, y + 10, count, 32, color, 700, "end"),
        f'<path d="M312 {y - 12}v24" stroke="{colors["border"]}" stroke-width="2"/>',
        f'<path d="M{x1} {y}H{x2}" stroke="{colors["border"]}" stroke-width="3"/>',
    ]
    if not count:
        return "".join(out)
    # Multiple rows retain distinct nodes if the archive grows beyond 120 PRs
    # in a single state. The visual stays at its fixed height.
    tracks = max(1, (count + 119) // 120)
    per_track = (count + tracks - 1) // tracks
    span = x2 - x1 - 8
    step = span / max(per_track - 1, 1)
    radius = min(5, step * 0.33, 10 / tracks)
    for i in range(count):
        track, col = divmod(i, per_track)
        x = x1 + 4 + (span / 2 if per_track == 1 else col * step)
        cy = y + (track - (tracks - 1) / 2) * (18 / tracks)
        if state == "MERGED":
            out.append(f'<circle cx="{x:.2f}" cy="{cy:.2f}" r="{radius:.2f}" fill="{color}"/>')
        elif state == "OPEN":
            out.append(f'<circle cx="{x:.2f}" cy="{cy:.2f}" r="{radius:.2f}" fill="{colors["bg"]}" stroke="{color}" stroke-width="1.8"/>')
        else:
            out.append(f'<path d="M{x-radius:.2f} {cy-radius:.2f}l{radius*2:.2f} {radius*2:.2f}m0 {-radius*2:.2f}l{-radius*2:.2f} {radius*2:.2f}" stroke="{color}" stroke-width="1.8" stroke-linecap="round"/>')
    # This travelling highlight describes flow; the data markers remain still.
    out.append(f'<path class="packet" d="M{x1} {y}H{x2}" fill="none" stroke="{color}" stroke-width="3" stroke-dasharray="16 572"/>')
    return "".join(out)


def render(data: dict, theme: str, font_b64: str, static: bool = False) -> str:
    colors = THEMES[theme]
    stats = summary(data)
    counts, repos = stats["states"], stats["repos"]
    as_of = str(data.get("asOf", "date unavailable"))[:10]
    username = data["username"]
    chosen = stats["ranking"][:4]
    draft_count = sum(bool(pr.get("isDraft")) and pr["state"].upper() == "OPEN" for pr in data["pullRequests"])
    repo_desc = "; ".join(f"{repo}: {repos[repo]} PRs" for repo in stats["ranking"])
    desc = (f"Public upstream pull requests by {username}, snapshot {as_of}. "
            f"{stats['total']} pull requests across {len(repos)} repositories: "
            f"{counts['MERGED']} merged, {counts['OPEN']} open, "
            f"{counts['CLOSED']} closed without merge. "
            f"The open count includes {draft_count} draft pull requests. "
            "Each chart marker represents one pull request, grouped by its recorded state. "
            "The four repositories with most PRs are displayed below the chart; "
            "volume ties use the latest PR creation date. All individual PRs are linked in the accompanying ledger. "
            f"Complete repository breakdown: {repo_desc}.")
    static_css = ".packet{display:none}" if static else ""
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" fill="{colors["text"]}" role="img" aria-labelledby="oss-title oss-desc">',
        f'<title id="oss-title">{esc(username)} / PATCH SIGNAL / public contributions</title>',
        f'<desc id="oss-desc">{esc(desc)}</desc>',
        f'''<style>
@font-face{{font-family:GM;src:url(data:font/woff2;base64,{font_b64}) format("woff2");font-weight:100 900}}
text{{font-family:GM,ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}
.packet{{animation:flow 7s linear infinite;opacity:.65}}
@keyframes flow{{from{{stroke-dashoffset:588}}to{{stroke-dashoffset:0}}}}
@media(prefers-reduced-motion:reduce){{.packet{{display:none;animation:none}}}}
{static_css}
</style>''',
        '<defs><clipPath id="window"><rect x=".5" y=".5" width="959" height="699" rx="16"/></clipPath></defs>',
        f'<rect x=".5" y=".5" width="959" height="699" rx="16" fill="{colors["bg"]}" stroke="{colors["border"]}"/>',
        '<g clip-path="url(#window)">',
        f'<path d="M0 0H960V44H0Z" fill="{colors["bar"]}"/>',
        f'<path d="M0 44H960" stroke="{colors["border"]}"/>',
    ]
    for i, color in enumerate(("#FF5F57", "#FEBC2E", "#28C840")):
        out.append(f'<circle cx="{24 + i * 20}" cy="22" r="6" fill="{color}"/>')
    out += [
        text(916, 30, "~/public-contributions", 21, colors["muted"], 500, "end"),
        text(44, 101, "PUBLIC / UPSTREAM", 30, colors["cyan"], 650),
        text(916, 101, as_of, 24, colors["muted"], 500, "end"),
        text(40, 184, "PATCH", 84, colors["text"], 850, extra=' letter-spacing="-4"'),
        text(326, 184, "SIGNAL", 84, colors["lime"], 850, extra=' letter-spacing="-4"'),
        # A branching git motif is the only purely decorative illustration.
        f'<g fill="{colors["bg"]}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round">'
        f'<path d="M823 131v50M881 144v-13M881 144c0 20-58 0-58 25" stroke="{colors["cyan"]}" fill="none"/>'
        f'<circle cx="823" cy="119" r="11" stroke="{colors["lime"]}"/>'
        f'<circle cx="823" cy="193" r="11" stroke="{colors["merged"]}"/>'
        f'<circle cx="881" cy="119" r="11" stroke="{colors["cyan"]}"/></g>',
    ]
    metrics = (
        (44, stats["total"], "PRs", colors["cyan"]),
        (270, counts["MERGED"], "MERGED", colors["merged"]),
        (496, counts["OPEN"], "OPEN", colors["open"]),
        (722, len(repos), "REPOS", colors["text"]),
    )
    for x, value, label, color in metrics:
        size = min(72, 170 / max(len(str(value)), 1) / 0.6)
        out.append(text(x, 260, value, size, color, 800, extra=' letter-spacing="-3"'))
        out.append(text(x + 2, 297, label, 30, colors["muted"], 600))
    out += [
        f'<path d="M44 321H916" stroke="{colors["border"]}" stroke-width="2"/>',
        # The bent rail ties the three recorded outcomes into one contribution history.
        f'<path d="M300 350c15 0 0 88 15 88" stroke="{colors["subtle"]}" stroke-width="2" fill="none"/>',
    ]
    for state, y in zip(STATES, (350, 394, 438)):
        out.append(rail(state, counts[state], y, colors))
    out += [
        text(44, 492, "REPO HOTSPOTS", 30, colors["cyan"], 650),
        text(916, 492, "by PR volume", 24, colors["muted"], 500, "end"),
        f'<path d="M44 507H916" stroke="{colors["border"]}"/>',
    ]
    for i, repo in enumerate(chosen):
        x = 44 + (i % 2) * 450
        y = 546 + (i // 2) * 70
        owner, name = repo.split("/", 1)
        out += [
            f'<path d="M{x} {y-15}h10" stroke="{colors["lime"]}" stroke-width="3"/>',
            text(x + 20, y - 5, short(name, 19), 30, colors["text"], 650),
            text(x + 418, y - 5, f"{repos[repo]:02d}", 30, colors["merged"], 700, "end"),
            text(x + 20, y + 23, short(owner, 24), 24, colors["muted"]),
        ]
    remaining = len(repos) - len(chosen)
    tail = f"+ {remaining} more repos / complete PR ledger below" if remaining else "Complete PR ledger below"
    out += [
        f'<path d="M44 652H916" stroke="{colors["border"]}"/>',
        text(44, 682, tail, 25, colors["muted"]),
        text(916, 681, "++", 30, colors["lime"], 800, "end"),
        "</g></svg>",
    ]
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True, help="curated GitHub snapshot JSON")
    parser.add_argument("--output", type=Path, default=ROOT / "assets" / "oss")
    parser.add_argument("--static", action="store_true", help="disable the decorative travelling highlight")
    args = parser.parse_args()
    data = json.loads(args.data.read_text(encoding="utf-8"))
    font = base64.b64encode((ROOT / "assets" / "fonts" / "geist-mono-card.woff2").read_bytes()).decode("ascii")
    stats = summary(data)
    args.output.mkdir(parents=True, exist_ok=True)
    for theme in THEMES:
        (args.output / f"oss-{theme}.svg").write_text(render(data, theme, font, args.static), encoding="utf-8")
    print(f"contributions: {stats['total']} PRs / {len(stats['repos'])} repositories / "
          f"{stats['states']['MERGED']} merged / {stats['states']['OPEN']} open / "
          f"{stats['states']['CLOSED']} closed without merge; 2 SVGs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
