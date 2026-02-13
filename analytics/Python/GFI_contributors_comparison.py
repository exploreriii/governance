"""
Python SDK Onboarding Signal
============================

Plots cumulative:
• Good First Issues
• Contributors

on the same timeline for hiero-sdk-python.

Purpose:
Understand the relationship between onboarding supply (issues)
and contributor demand (people).
"""

from __future__ import annotations
import os
import time
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any

import requests
import pandas as pd
import matplotlib.pyplot as plt
from dotenv import load_dotenv

# ---------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------

load_dotenv()

REPO = "hiero-ledger/hiero-sdk-python"
FILTER = "12m"  # "all" or "12m"

LABELS = [
    "good first issue",
    "good first issue candidate",
]

TOKEN = os.getenv("GITHUB_TOKEN")
HEADERS = {"Authorization": f"token {TOKEN}"} if TOKEN else {}
REQUEST_DELAY = 0.2
HTTP_TIMEOUT = 10

OUT_DIR = pathlib.Path("analytics/plots/python/onboarding_signal")
OUT_DIR.mkdir(parents=True, exist_ok=True)

print("🔐 Authenticated GitHub API" if TOKEN else "⚠️ No GITHUB_TOKEN — 60 req/hr limit")

# ---------------------------------------------------------------------
# API HELPERS
# ---------------------------------------------------------------------

def safe_get(url: str) -> Any:
    r = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)
    if r.status_code == 403 and not r.text.strip():
        print("⏳ Secondary rate limit — sleeping 30s…")
        time.sleep(30)
        return safe_get(url)

    remaining = r.headers.get("X-RateLimit-Remaining")
    reset = r.headers.get("X-RateLimit-Reset")
    if remaining and int(remaining) <= 0:
        wait = max(0, int(reset) - int(time.time()))
        print(f"⏳ Rate limit hit — waiting {wait}s…")
        time.sleep(wait)
        return safe_get(url)

    time.sleep(REQUEST_DELAY)
    return r.json()

# ---------------------------------------------------------------------
# DATA FETCHING
# ---------------------------------------------------------------------

def fetch_issues(label: str) -> List[pd.Timestamp]:
    page = 1
    timestamps = []

    while True:
        url = (
            f"https://api.github.com/repos/{REPO}/issues"
            f"?state=all&labels={label}&per_page=100&page={page}"
        )
        data = safe_get(url)

        if not isinstance(data, list) or not data:
            break

        for issue in data:
            ts = issue.get("created_at")
            if ts:
                timestamps.append(pd.to_datetime(ts, utc=True))

        if len(data) < 100:
            break
        page += 1

    return timestamps


def fetch_contributor_first_dates() -> List[pd.Timestamp]:
    url = f"https://api.github.com/repos/{REPO}/contributors?per_page=100&anon=true"
    contributors = safe_get(url)

    timestamps = []

    for c in contributors:
        login = c.get("login")
        if not login:
            continue

        commits_url = (
            f"https://api.github.com/repos/{REPO}/commits"
            f"?author={login}&per_page=100"
        )
        commits = safe_get(commits_url)

        dates = [
            pd.to_datetime(
                commit["commit"]["author"]["date"], utc=True
            )
            for commit in commits
            if commit.get("commit", {}).get("author", {}).get("date")
        ]

        if dates:
            timestamps.append(min(dates))

    return timestamps

# ---------------------------------------------------------------------
# PROCESSING
# ---------------------------------------------------------------------

def filter_dates(dates: List[pd.Timestamp]) -> List[pd.Timestamp]:
    if FILTER == "all":
        return dates
    cutoff = datetime.now(timezone.utc) - timedelta(days=365)
    return [d for d in dates if d >= cutoff]


def cumulative_df(dates: List[pd.Timestamp]) -> pd.DataFrame:
    df = pd.DataFrame({"date": sorted(dates)})
    df["count"] = range(1, len(df) + 1)
    return df

# ---------------------------------------------------------------------
# PLOTTING
# ---------------------------------------------------------------------

def plot_onboarding_signal(
    gfi_df: pd.DataFrame,
    contrib_df: pd.DataFrame,
) -> None:
    fig, ax1 = plt.subplots(figsize=(12, 6))

    ax1.plot(
        gfi_df["date"],
        gfi_df["count"],
        color="#1f77b4",
        linewidth=2,
        label="Good First Issues (cumulative)",
    )
    ax1.set_ylabel("Good First Issues", color="#1f77b4")
    ax1.tick_params(axis="y", labelcolor="#1f77b4")

    ax2 = ax1.twinx()
    ax2.plot(
        contrib_df["date"],
        contrib_df["count"],
        color="#2ca02c",
        linewidth=2,
        label="Contributors (cumulative)",
    )
    ax2.set_ylabel("Contributors", color="#2ca02c")
    ax2.tick_params(axis="y", labelcolor="#2ca02c")

    ax1.set_title("Python SDK Onboarding Signal")
    ax1.set_xlabel("Date")
    ax1.grid(True, alpha=0.4)

    lines, labels = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines + lines2, labels + labels2, loc="upper left")

    plt.tight_layout()

    out = OUT_DIR / f"python_sdk_onboarding_signal_{FILTER}.png"
    plt.savefig(out, dpi=300)
    plt.close()

    print(f"📈 Saved → {out}")

# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------

def main() -> None:
    print("📥 Fetching Good First Issues…")
    gfi_dates = []
    for label in LABELS:
        gfi_dates.extend(fetch_issues(label))

    print("👥 Fetching contributor first commits…")
    contrib_dates = fetch_contributor_first_dates()

    gfi_df = cumulative_df(filter_dates(gfi_dates))
    contrib_df = cumulative_df(filter_dates(contrib_dates))

    plot_onboarding_signal(gfi_df, contrib_df)


if __name__ == "__main__":
    main()
