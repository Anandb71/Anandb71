#!/usr/bin/env python3
"""Rebuild the contribution ledger and artwork from its checked-in public snapshot.

    python scripts/update_contributions.py
    python scripts/update_contributions.py --refresh  # authenticated GitHub CLI

Only public PRs outside the personal and founder-owned repositories are collected.
The existing bug-report selection is refreshed by URL; it does not count as PRs.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import html
import json
from pathlib import Path
import re
import subprocess
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
START, END = "<!-- CONTRIBUTIONS:START -->", "<!-- CONTRIBUTIONS:END -->"
QUERY = """query($searchQuery: String!, $endCursor: String) {
  search(query: $searchQuery, type: ISSUE, first: 100, after: $endCursor) {
    issueCount pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number title url state isDraft createdAt updatedAt mergedAt closedAt
        repository { nameWithOwner url isPrivate owner { login } }
      }
    }
  }
}"""

HIGHLIGHTS = [
    ("vitessio/vitess", 21424, "Database internals", "Keep schema diffs unchanged while applying table changes."),
    ("kestra-io/blueprints", 4359, "Workflow forensics", "Bisect a failed S3 batch into a verified one-record reproducer."),
    ("privacyidea/privacyidea", 6084, "Operational safety", "Return HTTP 503 when the HSM is not ready."),
    ("videojs/video.js", 9510, "Playback that responds", "Update the video frame while dragging the seek slider."),
    ("maplibre/maplibre-gl-js", 8773, "Maps, without the edge case", "Recognize .cjs worker URLs with a query or fragment."),
    ("pylint-dev/pylint", 11577, "Less noisy tooling", "Fix a used-before-assignment false positive for lambda parameters."),
]


def gh(*args: str) -> dict | list:
    result = subprocess.run(["gh", *args], check=True, capture_output=True, encoding="utf-8")
    return json.loads(result.stdout)


def refresh(data: dict) -> dict:
    user = data["username"]
    excluded = {name.casefold() for name in data["excludedOwners"]}
    pages = gh("api", "graphql", "--paginate", "--slurp", "-f", f"query={QUERY}",
               "-F", f"searchQuery=author:{user} is:pr is:public sort:created-desc")
    all_prs = []
    for page in pages:
        if page.get("errors"):
            raise RuntimeError(page["errors"])
        search = page["data"]["search"]
        if search["issueCount"] > 1000:
            raise RuntimeError("GitHub search exceeds 1,000 results; split the query by date before refreshing.")
        all_prs.extend(search["nodes"])
    prs = [pr for pr in all_prs if pr and not pr["repository"]["isPrivate"]
           and pr["repository"]["owner"]["login"].casefold() not in excluded]
    if len({pr["url"] for pr in prs}) != len(prs):
        raise RuntimeError("Duplicate PRs returned by GitHub; keep the previous snapshot.")
    # Fetch every selected report before writing, so a failed refresh leaves the snapshot intact.
    reports = []
    for report in data.get("bugReports", []):
        repo = report["repository"]["nameWithOwner"]
        current = gh("api", f"repos/{repo}/issues/{report['number']}")
        reports.append({**report, "title": current["title"], "state": current["state"].upper(),
                        "updatedAt": current["updated_at"], "closedAt": current["closed_at"]})
    return {**data, "asOf": datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat(),
            "pullRequests": prs, "bugReports": reports}


def safe(value: str) -> str:
    return html.escape(value, quote=False).replace("|", "&#124;").replace("\n", " ")


def status(pr: dict) -> str:
    return {"MERGED": "🟣 Merged", "OPEN": "🟢 Draft" if pr.get("isDraft") else "🟢 Open",
            "CLOSED": "⚪ Closed, unmerged"}[pr["state"]]


def section(data: dict) -> str:
    prs = data["pullRequests"]
    reports = data.get("bugReports", [])
    by_repo = defaultdict(list)
    for pr in prs:
        by_repo[pr["repository"]["nameWithOwner"]].append(pr)
    counts = Counter(pr["state"] for pr in prs)
    date_label = datetime.fromisoformat(data["asOf"]).strftime("%d %b %Y").lstrip("0")
    total = len(prs)
    rows = [START, "", "## Open source & public contributions", "",
            '<picture><source media="(prefers-color-scheme: dark)" srcset="assets/oss/oss-dark.svg">'
            '<source media="(prefers-color-scheme: light)" srcset="assets/oss/oss-light.svg">'
            f'<img src="assets/oss/oss-light.svg" width="100%" alt="Patch Signal: {total} public pull requests '
            f'across {len(by_repo)} repositories. {counts["MERGED"]} merged, {counts["OPEN"]} open, '
            f'{counts["CLOSED"]} closed without merge. Snapshot {date_label}."></picture>', "",
            "**Find the edge case. Write the regression test. Send the patch.**", "",
            f"{total} PRs across {len(by_repo)} public repositories, from database internals and supply-chain "
            "security to developer tools, video, maps, and AI. OSS projects and public collaborations; "
            "personal and Arbor repositories excluded.", "",
            "### Recently merged", "", "<table>"]
    indexed = {(pr["repository"]["nameWithOwner"], pr["number"]): pr for pr in prs}
    picks = [(indexed[key], label, desc) for repo, number, label, desc in HIGHLIGHTS
             if (key := (repo, number)) in indexed and indexed[key]["state"] == "MERGED"]
    for i in range(0, len(picks), 2):
        rows.append("<tr>")
        for pr, label, desc in picks[i:i + 2]:
            repo = pr["repository"]["nameWithOwner"]
            rows.append('<td width="50%" valign="top">'
                        f'<sub>{safe(label.upper())}</sub><br><br><strong>{safe(repo)}</strong><br>'
                        f'{safe(desc)}<br><br><a href="{pr["url"]}">↗ PR #{pr["number"]}</a>'
                        f' &nbsp; <sub>MERGED · {pr["mergedAt"][:10]}</sub></td>')
        rows.append("</tr>")
    rows += ["</table>", "", "### The complete patch log", "",
             f"🟣 **{counts['MERGED']} merged** &nbsp; 🟢 **{counts['OPEN']} open** &nbsp; "
             f"⚪ **{counts['CLOSED']} closed without merge**", "",
             "<details>", f"<summary><strong>All {total} pull requests · {len(by_repo)} repositories</strong> — expand the full ledger</summary>", "",
             f"All-time public PR history, ordered by each repository's latest contribution. Statuses checked {date_label}.", ""]
    order = sorted(by_repo, key=lambda repo: max(pr["createdAt"] for pr in by_repo[repo]), reverse=True)
    for repo in order:
        entries = sorted(by_repo[repo], key=lambda pr: pr["createdAt"], reverse=True)
        rows += [f"#### [{safe(repo)}]({entries[0]['repository']['url']})", ""]
        for pr in entries:
            rows.append(f"- {status(pr)} · [#{pr['number']}]({pr['url']}) · {safe(pr['title'])}")
        rows.append("")
    rows += ["</details>", "", "<details>",
             f"<summary><strong>Beyond the patch · {len(reports)} bug reports</strong> — reproductions, root causes, and proposed fixes</summary>", "",
             "Reported issues are tracked separately from the PR totals above.", ""]
    for report in sorted(reports, key=lambda item: item["createdAt"], reverse=True):
        repo = report["repository"]["nameWithOwner"]
        label = "🟢 Open" if report["state"] == "OPEN" else "⚪ Closed"
        rows.append(f"- {label} · [{safe(repo)} #{report['number']}]({report['url']}) · {safe(report['title'])}")
    rows += ["", "</details>", "",
             f"<sub>Snapshot: {date_label} (India). Counts include public PRs authored by "
             '<a href="https://github.com/Anandb71">@Anandb71</a> outside Anandb71 and getArbor-dev. '
             'Closed means closed without merge. '
             '<a href="config/contributions.json">Data</a> · '
             '<a href="https://github.com/search?q=author%3AAnandb71+is%3Apr+is%3Apublic+-user%3AAnandb71+-org%3AgetArbor-dev&amp;type=pullrequests">Live PR search ↗</a>'
             "</sub>", "", END]
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Fetch public PRs with the authenticated GitHub CLI first")
    args = parser.parse_args()
    path = ROOT / "config" / "contributions.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if args.refresh:
        data = refresh(data)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    readme = ROOT / "README.md"
    content = readme.read_text(encoding="utf-8")
    generated = section(data)
    if START in content:
        content = re.sub(re.escape(START) + ".*?" + re.escape(END), lambda _: generated, content, flags=re.S)
    else:
        content = content.split("### Open Source Contributions")[0].rstrip() + "\n"
        work_start = content.index('<picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Anandb71/Anandb71/output/work-dark.svg">')
        content = content[:work_start] + generated + "\n\n" + content[work_start:]
    if not content.startswith("<!-- SPDX-License-Identifier:"):
        content = "<!-- SPDX-License-Identifier: LicenseRef-All-Rights-Reserved -->\n\n" + content
    readme.write_text(content, encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "contributions.py"), "--data", str(path),
                    "--output", str(ROOT / "assets" / "oss")], check=True)
    print(f"Updated README and artwork from {len(data['pullRequests'])} public PRs.")


if __name__ == "__main__":
    main()
