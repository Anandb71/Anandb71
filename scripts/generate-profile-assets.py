#!/usr/bin/env python3
"""Generate stable, self-hosted GitHub profile signal cards."""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
DEFAULT_USERNAME = "Anandb71"
DEFAULT_FEATURED_OWNER = "getArbor-dev"
DEFAULT_FEATURED_REPOSITORY = "arbor"


THEMES = {
    "dark": {
        "background_a": "#07130B",
        "background_b": "#031008",
        "surface": "#091A0E",
        "surface_alt": "#06130A",
        "border": "#284A31",
        "grid": "#7CF58A",
        "text": "#F0F4E9",
        "muted": "#8FA394",
        "accent": "#7CF58A",
        "accent_soft": "#2E6A3B",
        "warning": "#D7A93B",
    },
    "light": {
        "background_a": "#F7FAF4",
        "background_b": "#EEF5EC",
        "surface": "#FFFFFF",
        "surface_alt": "#F3F7F1",
        "border": "#B7CDBA",
        "grid": "#18883D",
        "text": "#15231A",
        "muted": "#607566",
        "accent": "#18883D",
        "accent_soft": "#9ACDA6",
        "warning": "#9A6710",
    },
}


GRAPHQL_QUERY = """
query($login: String!, $owner: String!, $repository: String!) {
  user(login: $login) {
    followers {
      totalCount
    }
    repositories(privacy: PUBLIC) {
      totalCount
    }
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
          }
        }
      }
    }
  }
  repository(owner: $owner, name: $repository) {
    stargazerCount
    forkCount
  }
}
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    parser.add_argument("--fixture", type=Path)
    parser.add_argument(
        "--username",
        default=os.environ.get("PROFILE_USERNAME", DEFAULT_USERNAME),
    )
    parser.add_argument(
        "--featured-owner",
        default=os.environ.get("FEATURED_REPOSITORY_OWNER", DEFAULT_FEATURED_OWNER),
    )
    parser.add_argument(
        "--featured-repository",
        default=os.environ.get(
            "FEATURED_REPOSITORY_NAME",
            DEFAULT_FEATURED_REPOSITORY,
        ),
    )
    return parser.parse_args()


def github_graphql(
    token: str,
    username: str,
    featured_owner: str,
    featured_repository: str,
) -> dict[str, Any]:
    payload = json.dumps(
        {
            "query": GRAPHQL_QUERY,
            "variables": {
                "login": username,
                "owner": featured_owner,
                "repository": featured_repository,
            },
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        GITHUB_GRAPHQL_URL,
        data=payload,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "Anandb71-profile-assets",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"GitHub GraphQL request failed with HTTP {error.code}."
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError("GitHub GraphQL request could not connect.") from error

    if result.get("errors"):
        messages = "; ".join(
            str(item.get("message", "unknown GraphQL error"))
            for item in result["errors"]
        )
        raise RuntimeError(f"GitHub GraphQL returned errors: {messages}")

    user = result.get("data", {}).get("user")
    repository = result.get("data", {}).get("repository")
    if not user or not repository:
        raise RuntimeError("GitHub GraphQL response omitted required profile data.")

    calendar = user["contributionsCollection"]["contributionCalendar"]
    days = [
        {
            "date": day["date"],
            "count": int(day["contributionCount"]),
        }
        for week in calendar["weeks"]
        for day in week["contributionDays"]
    ]

    return {
        "username": username,
        "generatedAtUtc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "followers": int(user["followers"]["totalCount"]),
        "publicRepositories": int(user["repositories"]["totalCount"]),
        "featuredRepository": {
            "owner": featured_owner,
            "name": featured_repository,
            "stars": int(repository["stargazerCount"]),
            "forks": int(repository["forkCount"]),
        },
        "calendar": {
            "totalContributions": int(calendar["totalContributions"]),
            "days": days,
        },
    }


def read_source(args: argparse.Namespace) -> dict[str, Any]:
    if args.fixture:
        return json.loads(args.fixture.read_text(encoding="utf-8"))

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "GITHUB_TOKEN is required unless --fixture supplies public profile data."
        )

    return github_graphql(
        token,
        args.username,
        args.featured_owner,
        args.featured_repository,
    )


def normalize_days(raw_days: list[dict[str, Any]]) -> dict[date, int]:
    normalized: dict[date, int] = {}
    for item in raw_days:
        parsed = date.fromisoformat(str(item["date"]))
        count = int(item["count"])
        if count < 0:
            raise ValueError(f"Negative contribution count for {parsed}.")
        normalized[parsed] = count
    if not normalized:
        raise ValueError("Contribution calendar must contain at least one day.")
    return normalized


def streaks(day_counts: dict[date, int], today: date) -> tuple[int, int, int]:
    first_day = min(day_counts)
    last_day = min(max(day_counts), today)

    longest = 0
    running = 0
    active_days = 0
    cursor = first_day
    while cursor <= last_day:
        if day_counts.get(cursor, 0) > 0:
            running += 1
            active_days += 1
            longest = max(longest, running)
        else:
            running = 0
        cursor += timedelta(days=1)

    current_cursor = last_day
    if current_cursor == today and day_counts.get(current_cursor, 0) == 0:
        current_cursor -= timedelta(days=1)

    current = 0
    while current_cursor >= first_day and day_counts.get(current_cursor, 0) > 0:
        current += 1
        current_cursor -= timedelta(days=1)

    return current, longest, active_days


def weekly_totals(day_counts: dict[date, int], limit: int = 52) -> list[int]:
    totals: dict[tuple[int, int], int] = defaultdict(int)
    for day, count in day_counts.items():
        iso_year, iso_week, _ = day.isocalendar()
        totals[(iso_year, iso_week)] += count
    return [value for _, value in sorted(totals.items())[-limit:]]


def compact_number(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M".replace(".0M", "M")
    if value >= 10_000:
        return f"{value / 1_000:.1f}K".replace(".0K", "K")
    return f"{value:,}"


def metric_card(
    x: int,
    value: str,
    label: str,
    note: str,
    theme: dict[str, str],
    warning: bool = False,
) -> str:
    value_color = theme["warning"] if warning else theme["text"]
    return f"""
    <g transform="translate({x} 72)">
      <rect width="246" height="126" rx="15" fill="{theme['surface']}" stroke="{theme['border']}"/>
      <text x="20" y="47" fill="{value_color}" font-family="Georgia, Times New Roman, serif" font-size="40" font-weight="700">{html.escape(value)}</text>
      <text x="20" y="76" fill="{theme['accent']}" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="10" font-weight="700" letter-spacing="1.2">{html.escape(label)}</text>
      <text x="20" y="102" fill="{theme['muted']}" font-family="Inter, Segoe UI, Arial, sans-serif" font-size="11">{html.escape(note)}</text>
    </g>"""


def contribution_bars(
    values: list[int],
    theme: dict[str, str],
) -> str:
    if not values:
        values = [0]
    maximum = max(values) or 1
    chart_x = 54.0
    chart_y = 299.0
    chart_width = 1092.0
    chart_height = 54.0
    gap = 4.0
    bar_width = (chart_width - gap * (len(values) - 1)) / len(values)
    bars: list[str] = []

    for index, value in enumerate(values):
        height = max(3.0, chart_height * value / maximum)
        x = chart_x + index * (bar_width + gap)
        y = chart_y - height
        color = theme["warning"] if index == len(values) - 1 else theme["accent"]
        opacity = 0.95 if index >= len(values) - 4 else 0.52
        delay = index * 0.018
        bars.append(
            f'<rect class="bar" x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" '
            f'height="{height:.1f}" rx="2" fill="{color}" fill-opacity="{opacity}" '
            f'style="animation-delay:{delay:.3f}s"/>'
        )
    return "\n      ".join(bars)


def render_svg(
    source: dict[str, Any],
    theme_name: str,
    current_streak: int,
    longest_streak: int,
    active_days: int,
    weeks: list[int],
) -> str:
    theme = THEMES[theme_name]
    calendar = source["calendar"]
    featured = source["featuredRepository"]
    generated_date = str(source["generatedAtUtc"])[:10]
    username = str(source["username"])

    cards = [
        metric_card(
            54,
            compact_number(int(calendar["totalContributions"])),
            "CONTRIBUTIONS / LAST 12 MONTHS",
            f"{active_days} active days in the graph",
            theme,
        ),
        metric_card(
            330,
            f"{current_streak}d",
            "CURRENT STREAK",
            "Today may still be in flight",
            theme,
        ),
        metric_card(
            606,
            f"{longest_streak}d",
            "LONGEST STREAK",
            "Consecutive contribution days",
            theme,
        ),
        metric_card(
            882,
            compact_number(int(featured["stars"])),
            "ARBOR STARS",
            f"{featured['forks']} forks / graph-native core",
            theme,
            warning=True,
        ),
    ]

    return f"""<svg width="1200" height="340" viewBox="0 0 1200 340" fill="none" xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="title desc">
  <title id="title">{html.escape(username)} live builder signal</title>
  <desc id="desc">Contributions, current streak, longest streak, and Arbor stars generated by GitHub Actions.</desc>
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1200" y2="340" gradientUnits="userSpaceOnUse">
      <stop stop-color="{theme['background_a']}"/>
      <stop offset="1" stop-color="{theme['background_b']}"/>
    </linearGradient>
    <pattern id="grid" width="26" height="26" patternUnits="userSpaceOnUse">
      <path d="M26 0H0V26" stroke="{theme['grid']}" stroke-opacity=".04"/>
    </pattern>
    <style>
      .bar {{ transform-box: fill-box; transform-origin: center bottom; animation: rise .9s ease-out both; }}
      .live {{ animation: pulse 2.4s ease-in-out infinite; transform-box: fill-box; transform-origin: center; }}
      @keyframes rise {{ from {{ transform: scaleY(.08); opacity: .15; }} }}
      @keyframes pulse {{ 0%, 100% {{ opacity: .45; transform: scale(.82); }} 50% {{ opacity: 1; transform: scale(1.18); }} }}
      @media (prefers-reduced-motion: reduce) {{ .bar, .live {{ animation: none; }} }}
    </style>
  </defs>
  <rect width="1200" height="340" rx="22" fill="url(#bg)"/>
  <rect width="1200" height="340" rx="22" fill="url(#grid)"/>
  <circle class="live" cx="64" cy="42" r="5" fill="{theme['accent']}"/>
  <text x="82" y="47" fill="{theme['text']}" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="13" font-weight="700" letter-spacing="1.7">LIVE BUILDER SIGNAL / SELF-HOSTED</text>
  <text x="1146" y="47" text-anchor="end" fill="{theme['muted']}" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="10">UPDATED {generated_date} UTC</text>
  {''.join(cards)}
  <text x="54" y="222" fill="{theme['muted']}" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="10" letter-spacing="1">CONTRIBUTION VELOCITY / 52 WEEKS</text>
  <text x="1146" y="222" text-anchor="end" fill="{theme['muted']}" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="10">{int(source['publicRepositories'])} PUBLIC REPOS / {int(source['followers'])} FOLLOWERS</text>
  <path d="M54 306H1146" stroke="{theme['border']}"/>
  <g>
      {contribution_bars(weeks, theme)}
  </g>
  <rect x=".5" y=".5" width="1199" height="339" rx="21.5" stroke="{theme['border']}"/>
</svg>
"""


def validate_source(source: dict[str, Any]) -> None:
    required = {
        "username",
        "generatedAtUtc",
        "followers",
        "publicRepositories",
        "featuredRepository",
        "calendar",
    }
    missing = required - source.keys()
    if missing:
        raise ValueError(f"Profile source is missing: {', '.join(sorted(missing))}")

    datetime.fromisoformat(str(source["generatedAtUtc"]).replace("Z", "+00:00"))
    if int(source["followers"]) < 0 or int(source["publicRepositories"]) < 0:
        raise ValueError("Profile counters cannot be negative.")
    if int(source["featuredRepository"]["stars"]) < 0:
        raise ValueError("Featured repository stars cannot be negative.")
    if int(source["featuredRepository"]["forks"]) < 0:
        raise ValueError("Featured repository forks cannot be negative.")


def main() -> int:
    args = parse_args()
    try:
        source = read_source(args)
        validate_source(source)
        day_counts = normalize_days(source["calendar"]["days"])
        generated_at = datetime.fromisoformat(
            str(source["generatedAtUtc"]).replace("Z", "+00:00")
        )
        current, longest, active_days = streaks(
            day_counts,
            generated_at.date(),
        )
        weeks = weekly_totals(day_counts)

        args.output.mkdir(parents=True, exist_ok=True)
        for theme_name in THEMES:
            output_path = args.output / f"profile-signal-{theme_name}.svg"
            output_path.write_text(
                render_svg(
                    source,
                    theme_name,
                    current,
                    longest,
                    active_days,
                    weeks,
                ),
                encoding="utf-8",
                newline="\n",
            )

        public_snapshot = {
            "username": source["username"],
            "generatedAtUtc": source["generatedAtUtc"],
            "followers": int(source["followers"]),
            "publicRepositories": int(source["publicRepositories"]),
            "featuredRepository": source["featuredRepository"],
            "totalContributions": int(
                source["calendar"]["totalContributions"]
            ),
            "currentStreakDays": current,
            "longestStreakDays": longest,
            "activeDays": active_days,
        }
        (args.output / "profile-data.json").write_text(
            json.dumps(public_snapshot, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except (KeyError, TypeError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"Generated profile assets in {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
