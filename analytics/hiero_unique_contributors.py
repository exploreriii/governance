"""
Hiero – Unique Contributors Over Time
Produces exactly two plots:
1. Total cumulative contributors (all time)
2. Cumulative contributors in a filtered window (e.g. last 12 months)
"""

from __future__ import annotations
import os
import time
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests
import pandas as pd
import matplotlib.pyplot as plt
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

# ---------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------

load_dotenv()

ORG = "hiero-ledger"
FILTER = "12m"              # "all" or "12m"
REQUEST_DELAY = 0.15

TOKEN = os.getenv("GITHUB_TOKEN")
HEADERS = {"Authorization": f"token {TOKEN}"} if TOKEN else {}

PLOTS_DIR = pathlib.Path("analytics/plots/contributors_unique")
PLOTS_DIR.mkdir(parents=True, exist_ok=True)

print("🔐 Authenticated GitHub API" if TOKEN else "⚠️ No GITHUB_TOKEN — 60 req/hr limit")

# ---------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------

def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")

# ---------------------------------------------------------------------
# SAFE API REQUEST
# ---------------------------------------------------------------------

def safe_get(url: str, max_retries: int = 8):
    attempts = 0
    while True:
        r = requests.get(url, headers=HEADERS)

        if r.status_code == 202:
            attempts += 1
            if attempts >= max_retries:
                return []
            time.sleep(5)
            continue

        if r.status_code == 204:
            return []

        remaining = r.headers.get("X-RateLimit-Remaining")
        reset_ts = r.headers.get("X-RateLimit-Reset")
        if remaining is not None and int(remaining) <= 0:
            wait = max(0, int(reset_ts) - int(time.time()))
            log(f"⏳ Rate limit hit — waiting {wait}s")
            time.sleep(wait)
            continue

        try:
            data = r.json()
        except Exception:
            attempts += 1
            if attempts >= max_retries:
                return []
            time.sleep(1)
            continue

        time.sleep(REQUEST_DELAY)
        return data

# ---------------------------------------------------------------------
# FETCHING
# ---------------------------------------------------------------------

def fetch_repos(org: str) -> List[str]:
    repos = []
    page = 1
    while True:
        url = f"https://api.github.com/orgs/{org}/repos?per_page=100&page={page}"
        data = safe_get(url)
        if not isinstance(data, list) or not data:
            break
        repos.extend(r["full_name"] for r in data)
        if len(data) < 100:
            break
        page += 1
    return repos


def fetch_user_first_commit(repo: str, login: str) -> Optional[pd.Timestamp]:
    url = f"https://api.github.com/repos/{repo}/commits?author={login}&per_page=100"
    commits = safe_get(url)
    if not isinstance(commits, list):
        return None

    dates = [
        pd.to_datetime(c["commit"]["author"]["date"], utc=True)
        for c in commits
        if c.get("commit", {}).get("author", {}).get("date")
    ]
    return min(dates) if dates else None


def fetch_repo_contributors(repo: str) -> Dict[str, pd.Timestamp]:
    url = f"https://api.github.com/repos/{repo}/contributors?per_page=100&anon=true"
    contributors = safe_get(url)
    if not isinstance(contributors, list):
        return {}

    logins = [
        c.get("login") or c.get("name")
        for c in contributors
        if c.get("login") or c.get("name")
    ]

    first_dates: Dict[str, pd.Timestamp] = {}
    with ThreadPoolExecutor(max_workers=min(10, len(logins)) or 1) as pool:
        futures = {
            pool.submit(fetch_user_first_commit, repo, login): login
            for login in logins
        }
        for f in as_completed(futures):
            ts = f.result()
            if ts:
                first_dates[futures[f]] = ts

    return first_dates

# ---------------------------------------------------------------------
# PROCESSING
# ---------------------------------------------------------------------

def build_cumulative_df(dates: List[pd.Timestamp]) -> pd.DataFrame:
    df = pd.DataFrame({"created_at": sorted(dates)})
    df["count"] = range(1, len(df) + 1)
    return df


def filter_dates(dates: List[pd.Timestamp]) -> List[pd.Timestamp]:
    if FILTER == "all":
        return dates
    cutoff = datetime.now(timezone.utc) - timedelta(days=365)
    return [d for d in dates if d >= cutoff]

# ---------------------------------------------------------------------
# PLOTTING
# ---------------------------------------------------------------------

def plot_timeline(df: pd.DataFrame, title: str, filename: str) -> None:
    plt.figure(figsize=(12, 6))
    plt.plot(df["created_at"], df["count"], linewidth=3)
    plt.title(title)
    plt.xlabel("Date")
    plt.ylabel("Unique Contributors")
    plt.grid(alpha=0.4)
    plt.tight_layout()

    path = PLOTS_DIR / filename
    plt.savefig(path, dpi=300)
    plt.close()
    log(f"📈 Saved → {path}")

# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------

def main() -> None:
    repos = fetch_repos(ORG)
    log(f"Found {len(repos)} repositories")

    # Global map: contributor → earliest date across ALL repos
    global_first_seen: Dict[str, pd.Timestamp] = {}

    for repo in repos:
        log(f"Processing {repo}")
        repo_dates = fetch_repo_contributors(repo)
        for user, ts in repo_dates.items():
            if user not in global_first_seen or ts < global_first_seen[user]:
                global_first_seen[user] = ts

    all_dates = list(global_first_seen.values())

    # Plot 1: All time
    df_all = build_cumulative_df(all_dates)
    plot_timeline(
        df_all,
        "Hiero – Total Unique Contributors (All Time)",
        "contributors_total_all_time.png",
    )

    # Plot 2: Filtered
    filtered_dates = filter_dates(all_dates)
    if filtered_dates:
        df_filtered = build_cumulative_df(filtered_dates)
        plot_timeline(
            df_filtered,
            "Hiero – Unique Contributors (Filtered Window)",
            f"contributors_filtered_{FILTER}.png",
        )

if __name__ == "__main__":
    main()
