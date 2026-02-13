"""
Python SDK Commit Activity Timeline
===================================

Shows weekly commit activity for Hiero Python repositories.

Run:
    uv run analytics/Python/commit_activity.py
"""

from __future__ import annotations
import os
import time
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import requests
import pandas as pd
import matplotlib.pyplot as plt
from dotenv import load_dotenv

# -------------------------------------------------------------------
# CONFIG
# -------------------------------------------------------------------
load_dotenv()

ORG = "hiero-ledger"
PYTHON_REPOS = [
    "hiero-sdk-python",
    "hiero-did-sdk-python",
]

TOKEN = os.getenv("GITHUB_TOKEN")
HEADERS = {"Authorization": f"token {TOKEN}"} if TOKEN else {}

HTTP_TIMEOUT = 15
REQUEST_DELAY = 0.25

print("🔐 Using authenticated GitHub API" if TOKEN else "⚠️ No GITHUB_TOKEN found — 60 req/hr limit")

# -------------------------------------------------------------------
# HTTP HELPERS
# -------------------------------------------------------------------
def safe_get_json(url: str) -> Any:
    while True:
        r = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)

        if r.status_code == 202:
            print("⏳ GitHub is computing stats, retrying...")
            time.sleep(3)
            continue

        remaining = int(r.headers.get("X-RateLimit-Remaining", "1"))
        reset_ts = int(r.headers.get("X-RateLimit-Reset", "0"))

        if remaining <= 0:
            wait = max(0, reset_ts - int(time.time()))
            print(f"⏳ Rate limit hit — waiting {wait}s")
            time.sleep(wait)
            continue

        if r.status_code >= 400:
            print(f"❌ Error {r.status_code} for {url}")
            return None

        try:
            data = r.json()
            time.sleep(REQUEST_DELAY)
            return data
        except Exception:
            return None

# -------------------------------------------------------------------
# FETCH COMMIT ACTIVITY
# -------------------------------------------------------------------
def fetch_commit_activity(repo: str) -> Optional[List[Dict[str, Any]]]:
    full_name = f"{ORG}/{repo}"
    print(f"📦 Fetching {full_name}")
    url = f"https://api.github.com/repos/{full_name}/stats/commit_activity"
    return safe_get_json(url)

# -------------------------------------------------------------------
# BUILD DATAFRAME
# -------------------------------------------------------------------
def build_python_commit_df() -> pd.DataFrame:
    rows = []

    for repo in PYTHON_REPOS:
        activity = fetch_commit_activity(repo)
        if not activity:
            continue

        for wk in activity:
            rows.append({
                "week_start": datetime.fromtimestamp(int(wk["week"]), tz=timezone.utc),
                "repo": repo,
                "commits": int(wk.get("total", 0) or 0),
            })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    weekly = (
        df.groupby("week_start", as_index=False)
        .agg(commits=("commits", "sum"))
        .sort_values("week_start")
    )

    weekly["commits_4w_avg"] = weekly["commits"].rolling(4, min_periods=1).mean()

    return weekly

# -------------------------------------------------------------------
# PLOT
# -------------------------------------------------------------------
def plot_python_commit_activity(df: pd.DataFrame):
    if df.empty:
        print("⚠️ No commit data found.")
        return

    plt.figure(figsize=(12, 6))
    plt.plot(df["week_start"], df["commits"], alpha=0.3, label="Weekly commits")
    plt.plot(df["week_start"], df["commits_4w_avg"], linewidth=3, label="4-week average")

    plt.title("Hiero Python SDK Commit Activity")
    plt.xlabel("Week")
    plt.ylabel("Commits")
    plt.grid(alpha=0.3)
    plt.legend()
    plt.tight_layout()

    out = "analytics/plots/python_commit_activity.png"
    os.makedirs("analytics/plots", exist_ok=True)
    plt.savefig(out, dpi=300)
    plt.close()

    print(f"🐍 Saved → {out}")

# -------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------
def main():
    df = build_python_commit_df()
    plot_python_commit_activity(df)

if __name__ == "__main__":
    main()
