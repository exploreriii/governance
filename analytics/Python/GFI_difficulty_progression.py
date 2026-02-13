"""
Python SDK Contribution Mix (Last 14 Days)
=========================================

Outputs:
1. Bar chart — merged PRs by difficulty (absolute)
2. Bar chart — contributors by difficulty
3. Pie chart — % of merged PRs by difficulty

Repository:
    hiero-ledger/hiero-sdk-python
"""

from __future__ import annotations
import os
import time
import re
from typing import Dict, Any
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import requests
import matplotlib.pyplot as plt
from dotenv import load_dotenv

# ---------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------

load_dotenv()

REPO = "hiero-ledger/hiero-sdk-python"

GOOD_FIRST = {"good first issue", "good first issue candidate"}
INTERMEDIATE = {"help wanted", "intermediate", "medium"}
ADVANCED = {"advanced", "hard", "complex"}

ISSUE_RE = re.compile(r"#(\d+)")

LOOKBACK_DAYS = 14
CUTOFF_DATE = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

TOKEN = os.getenv("GITHUB_TOKEN")
HEADERS = {"Authorization": f"token {TOKEN}"} if TOKEN else {}
REQUEST_DELAY = 0.2
HTTP_TIMEOUT = 10

OUT_DIR = "analytics/plots/python/contribution_mix"
os.makedirs(OUT_DIR, exist_ok=True)

TIERS = ["Good First", "Intermediate", "Advanced"]

print("🔐 Authenticated GitHub API" if TOKEN else "⚠️ No GITHUB_TOKEN — 60 req/hr limit")

COLORS = {
    "Good First": "#1f77b4",     # blue
    "Intermediate": "#ff7f0e",   # orange
    "Advanced": "#2ca02c",       # green
}

# ---------------------------------------------------------------------
# API HELPERS
# ---------------------------------------------------------------------

def safe_get(url: str) -> Any:
    r = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)

    if r.status_code == 403 and not r.text.strip():
        time.sleep(30)
        return safe_get(url)

    remaining = r.headers.get("X-RateLimit-Remaining")
    reset = r.headers.get("X-RateLimit-Reset")
    if remaining and int(remaining) <= 0:
        time.sleep(max(0, int(reset) - int(time.time())))
        return safe_get(url)

    time.sleep(REQUEST_DELAY)
    return r.json()

# ---------------------------------------------------------------------
# DATA FETCHING
# ---------------------------------------------------------------------

def fetch_closed_issues() -> Dict[int, dict]:
    issues = {}
    page = 1

    while True:
        url = (
            f"https://api.github.com/repos/{REPO}/issues"
            f"?state=closed&per_page=100&page={page}"
        )
        data = safe_get(url)
        if not data:
            break

        for issue in data:
            if "pull_request" not in issue:
                issues[issue["number"]] = issue

        if len(data) < 100:
            break
        page += 1

    return issues


def fetch_recent_merged_prs() -> list[dict]:
    prs = []
    page = 1

    while True:
        url = (
            f"https://api.github.com/repos/{REPO}/pulls"
            f"?state=closed&per_page=100&page={page}"
        )
        data = safe_get(url)
        if not data:
            break

        for pr in data:
            merged_at = pr.get("merged_at")
            if not merged_at:
                continue

            merged_time = datetime.fromisoformat(
                merged_at.replace("Z", "+00:00")
            )

            if merged_time >= CUTOFF_DATE:
                prs.append(pr)

        if len(data) < 100:
            break
        page += 1

    return prs

# ---------------------------------------------------------------------
# CLASSIFICATION
# ---------------------------------------------------------------------

def classify_issue(labels: set[str]) -> str | None:
    if labels & GOOD_FIRST:
        return "Good First"
    if labels & INTERMEDIATE:
        return "Intermediate"
    if labels & ADVANCED:
        return "Advanced"
    return None


def extract_issue_numbers(pr: dict) -> set[int]:
    text = f"{pr.get('title', '')} {pr.get('body', '')}"
    return {int(m) for m in ISSUE_RE.findall(text)}

# ---------------------------------------------------------------------
# ANALYSIS
# ---------------------------------------------------------------------

def analyze() -> tuple[dict, dict]:
    pr_counts = defaultdict(int)
    contributors = defaultdict(set)

    issues = fetch_closed_issues()
    prs = fetch_recent_merged_prs()

    for pr in prs:
        author = pr.get("user", {}).get("login")
        if not author:
            continue

        for issue_num in extract_issue_numbers(pr):
            issue = issues.get(issue_num)
            if not issue:
                continue

            labels = {l["name"].lower() for l in issue.get("labels", [])}
            tier = classify_issue(labels)
            if not tier:
                continue

            pr_counts[tier] += 1
            contributors[tier].add(author)

    return pr_counts, contributors

# ---------------------------------------------------------------------
# PLOTTING
# ---------------------------------------------------------------------
def plot_prs_by_difficulty(pr_counts: dict) -> None:
    values = [pr_counts.get(t, 0) for t in TIERS]
    colors = [COLORS[t] for t in TIERS]

    plt.figure(figsize=(7, 5))
    plt.bar(TIERS, values, color=colors)

    plt.title("Merged PRs by Difficulty (Last 14 Days)")
    plt.ylabel("Merged PRs")
    plt.xlabel("Difficulty")
    plt.grid(axis="y", alpha=0.4)
    plt.tight_layout()

    out = f"{OUT_DIR}/merged_prs_by_difficulty_last_14_days.png"
    plt.savefig(out, dpi=300)
    plt.close()

    print(f"📊 Saved bar chart → {out}")


def plot_contributors(contributors: dict) -> None:
    values = [len(contributors.get(t, [])) for t in TIERS]
    colors = [COLORS[t] for t in TIERS]

    plt.figure(figsize=(7, 5))
    plt.bar(TIERS, values, color=colors)

    plt.title("Contributors by Difficulty (Last 14 Days)")
    plt.ylabel("Unique Contributors")
    plt.xlabel("Difficulty")
    plt.grid(axis="y", alpha=0.4)
    plt.tight_layout()

    out = f"{OUT_DIR}/contributors_by_difficulty.png"
    plt.savefig(out, dpi=300)
    plt.close()

    print(f"👥 Saved bar chart → {out}")


def plot_percentage_mix(pr_counts: dict) -> None:
    values = [pr_counts.get(t, 0) for t in TIERS]
    total = sum(values)

    if total == 0:
        print("⚠️ No merged PRs to plot")
        return

    percentages = [v / total * 100 for v in values]
    labels = [
        f"{tier}\n{pct:.0f}% ({count})"
        for tier, pct, count in zip(TIERS, percentages, values)
    ]

    plt.figure(figsize=(7, 5))
    plt.pie(
        percentages,
        labels=labels,
        colors=[COLORS[t] for t in TIERS],
        startangle=90,
        counterclock=False,
    )


    plt.title("Merged PR Mix by Difficulty (Last 14 Days)")
    plt.tight_layout()

    out = f"{OUT_DIR}/merged_prs_percentage.png"
    plt.savefig(out, dpi=300)
    plt.close()

    print(f"🥧 Saved pie chart → {out}")

# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------

def main() -> None:
    pr_counts, contributors = analyze()

    print("\n📊 Contribution Summary (Last 14 Days)")
    print("-" * 55)
    print(f"{'Difficulty':<15} {'Merged PRs':<12} {'Contributors'}")
    print("-" * 55)

    for tier in TIERS:
        print(
            f"{tier:<15} "
            f"{pr_counts.get(tier, 0):<12} "
            f"{len(contributors.get(tier, []))}"
        )

    plot_prs_by_difficulty(pr_counts)
    plot_contributors(contributors)
    plot_percentage_mix(pr_counts)

    print(f"\n📁 Plots saved to → {OUT_DIR}")
    print("✅ Done")

if __name__ == "__main__":
    main()
