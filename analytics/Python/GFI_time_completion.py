"""
GitHub Good First Issue Time-to-Completion Timeline
===================================================

Tracks how long Good First Issues take to complete over time.

Metric:
    Average days from issue creation → completion

Output:
• Rolling average completion time timeline

Run:
    uv run analytics/gfi_time_to_completion.py
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

PLOTS_DIR = pathlib.Path("analytics/plots/gfi_time_to_completion")
PLOTS_DIR.mkdir(parents=True, exist_ok=True)

print("🔐 Authenticated GitHub API" if TOKEN else "⚠️ No GITHUB_TOKEN — 60 req/hr limit")

# ---------------------------------------------------------------------
# API HELPERS
# ---------------------------------------------------------------------

def safe_get(url: str, retries: int = 5) -> Any:
    for _ in range(retries):
        r = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)

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

    print(f"❌ Failed to fetch: {url}")
    return []

# ---------------------------------------------------------------------
# FETCH ISSUES
# ---------------------------------------------------------------------

def fetch_completed_gfis(repo: str, labels: List[str]) -> List[Dict[str, Any]]:
    issues = []
    page = 1
    label_query = ",".join(labels)

    while True:
        url = (
            f"https://api.github.com/repos/{repo}/issues"
            f"?state=closed&labels={label_query}&per_page=100&page={page}"
        )
        data = safe_get(url)

        if not isinstance(data, list) or not data:
            break

        for item in data:
            if "pull_request" in item:
                continue
            if item.get("state_reason") == "completed":
                issues.append(item)

        if len(data) < 100:
            break
        page += 1

    return issues

# ---------------------------------------------------------------------
# PROCESSING
# ---------------------------------------------------------------------

def build_time_to_completion_df(issues: List[Dict[str, Any]]) -> pd.DataFrame:
    records = []

    for issue in issues:
        created = issue.get("created_at")
        closed = issue.get("closed_at")

        if not created or not closed:
            continue

        created_ts = pd.to_datetime(created, utc=True)
        closed_ts = pd.to_datetime(closed, utc=True)

        if FILTER == "12m":
            cutoff = datetime.now(timezone.utc) - timedelta(days=365)
            if closed_ts < cutoff:
                continue

        days_to_complete = (closed_ts - created_ts).days

        records.append({
            "closed_at": closed_ts,
            "days_to_complete": days_to_complete,
        })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records).sort_values("closed_at")

    df.set_index("closed_at", inplace=True)

    # 90-day rolling average
    df["rolling_avg_days"] = (
        df["days_to_complete"].rolling("90D").mean()
    )

    df.reset_index(inplace=True)

    return df

# ---------------------------------------------------------------------
# PLOTTING
# ---------------------------------------------------------------------

def plot_time_to_completion(df: pd.DataFrame) -> None:
    if df.empty:
        print("⚠️ No completed Good First Issues found.")
        return

    plt.figure(figsize=(12, 6))

    plt.plot(
        df["closed_at"],
        df["rolling_avg_days"],
        linewidth=2,
        label="90-day Rolling Avg",
    )

    plt.title("Average Time to Complete Good First Issues")
    plt.xlabel("Completion Date")
    plt.ylabel("Days to Completion")
    plt.grid(True, alpha=0.4)
    plt.legend()
    plt.tight_layout()

    path = PLOTS_DIR / f"gfi_time_to_completion_{FILTER}.png"
    plt.savefig(path, dpi=300)
    plt.close()

    print(f"📈 Saved plot → {path}")

# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------

def main():
    print(f"\n📊 Fetching completed Good First Issues for {REPO}…")
    issues = fetch_completed_gfis(REPO, LABELS)

    print(f"   Found {len(issues)} completed GFIs")

    df = build_time_to_completion_df(issues)

    if not df.empty:
        latest = df["rolling_avg_days"].dropna().iloc[-1]
        print(f"   Current rolling average completion time: {latest:.1f} days")

    plot_time_to_completion(df)


if __name__ == "__main__":
    main()
