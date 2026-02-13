"""
GitHub Good First Issue Completion Rate Timeline
================================================

Tracks the percentage of "Good First Issues" that are closed as
completed over time for a repository.

Outputs:
• Cumulative completion rate timeline

Run:
    uv run analytics/gfi_completion_rate.py
"""

from __future__ import annotations
import os
import time
import pathlib
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

import requests
import pandas as pd
import matplotlib.pyplot as plt
from dotenv import load_dotenv

# ---------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------

load_dotenv()

REPO = "hiero-ledger/hiero-sdk-python"
LABELS = ["Good First Issue"]
FILTER = "all"  # "all" or "12m"

TOKEN = os.getenv("GITHUB_TOKEN")
HEADERS = {"Authorization": f"token {TOKEN}"} if TOKEN else {}

REQUEST_DELAY = 0.25
HTTP_TIMEOUT = 10

PLOTS_DIR = pathlib.Path("analytics/plots/gfi_completion")
PLOTS_DIR.mkdir(parents=True, exist_ok=True)

print("🔐 Authenticated GitHub API" if TOKEN else "⚠️ No GITHUB_TOKEN — 60 req/hr limit")

# ---------------------------------------------------------------------
# API HELPERS
# ---------------------------------------------------------------------

def safe_get(url: str, retries: int = 5) -> Any:
    for attempt in range(retries):
        r = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)

        # Secondary rate limit
        if r.status_code == 403 and not r.text.strip():
            print("🚦 Secondary rate limit — sleeping 30s…")
            time.sleep(30)
            continue

        remaining = int(r.headers.get("X-RateLimit-Remaining", "1"))
        reset_ts = int(r.headers.get("X-RateLimit-Reset", "0"))

        if remaining <= 0:
            wait = max(0, reset_ts - int(time.time()))
            print(f"⏳ Rate limit hit — waiting {wait}s…")
            time.sleep(wait)
            continue

        try:
            data = r.json()
            time.sleep(REQUEST_DELAY)
            return data
        except Exception:
            time.sleep(2)

    print(f"❌ Failed to fetch after retries: {url}")
    return []

# ---------------------------------------------------------------------
# DATA FETCHING
# ---------------------------------------------------------------------

def fetch_all_gfi_issues(repo: str, labels: List[str]) -> List[Dict[str, Any]]:
    issues = []
    page = 1

    label_query = ",".join(labels)

    while True:
        url = (
            f"https://api.github.com/repos/{repo}/issues"
            f"?state=all&labels={label_query}&per_page=100&page={page}"
        )
        data = safe_get(url)

        if not isinstance(data, list) or not data:
            break

        for item in data:
            if "pull_request" not in item:
                issues.append(item)

        if len(data) < 100:
            break
        page += 1

    return issues

# ---------------------------------------------------------------------
# PROCESSING
# ---------------------------------------------------------------------

def filter_closed_dates(ts: pd.Timestamp) -> bool:
    if FILTER == "all":
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(days=365)
    return ts >= cutoff


def build_completion_timeline(issues: List[Dict[str, Any]]) -> pd.DataFrame:
    records = []

    for issue in issues:
        if issue.get("state") != "closed" or not issue.get("closed_at"):
            continue

        closed_ts = pd.to_datetime(issue["closed_at"], utc=True)

        if not filter_closed_dates(closed_ts):
            continue

        reason = issue.get("state_reason")
        completed = 1 if reason == "completed" else 0

        records.append({
            "closed_at": closed_ts,
            "completed": completed,
            "closed": 1,
        })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records).sort_values("closed_at")

    df["cum_closed"] = df["closed"].cumsum()
    df["cum_completed"] = df["completed"].cumsum()
    df["pct_completed"] = df["cum_completed"] / df["cum_closed"] * 100

    return df

# ---------------------------------------------------------------------
# PLOTTING
# ---------------------------------------------------------------------

def plot_completion_rate(df: pd.DataFrame, repo: str) -> None:
    if df.empty:
        print("⚠️ No closed Good First Issues found.")
        return

    plt.figure(figsize=(12, 6))

    plt.plot(df["closed_at"], df["pct_completed"], linewidth=2, label="Cumulative")

    plt.title(f"% of Good First Issues Closed as Completed\n{repo}")
    plt.xlabel("Date")
    plt.ylabel("Completion Rate (%)")
    plt.ylim(0, 100)
    plt.grid(True, alpha=0.4)
    plt.legend()
    plt.tight_layout()

    path = PLOTS_DIR / f"gfi_completion_rate_{FILTER}.png"
    plt.savefig(path, dpi=300)
    plt.close()

    print(f"📈 Saved plot → {path}")

# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------

def main():
    print(f"\n📊 Fetching Good First Issues for {REPO}…")
    issues = fetch_all_gfi_issues(REPO, LABELS)

    print(f"   Found {len(issues)} total labeled issues")

    df = build_completion_timeline(issues)

    if not df.empty:
        final_rate = df["pct_completed"].iloc[-1]
        print(f"   Current cumulative completion rate: {final_rate:.1f}%")

    plot_completion_rate(df, REPO)


if __name__ == "__main__":
    main()
