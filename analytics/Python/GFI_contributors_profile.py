"""
Python SDK Contributor Account Age
==================================

Visualizes the GitHub account age distribution of contributors to
hiero-sdk-python.

Purpose:
Understand contributor seniority / experience level.
"""

from __future__ import annotations
import os
import time
import pathlib
from datetime import datetime, timezone
from typing import List, Any

import requests
import pandas as pd
import matplotlib.pyplot as plt
from dotenv import load_dotenv

# ---------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------

load_dotenv()

REPO = "hiero-ledger/hiero-sdk-python"

TOKEN = os.getenv("GITHUB_TOKEN")
HEADERS = {"Authorization": f"token {TOKEN}"} if TOKEN else {}
REQUEST_DELAY = 0.2
HTTP_TIMEOUT = 10

OUT_DIR = pathlib.Path("analytics/plots/python/python_sdk_contributor_account_age")
OUT_DIR.mkdir(parents=True, exist_ok=True)

print("🔐 Authenticated GitHub API" if TOKEN else "⚠️ No GITHUB_TOKEN — 60 req/hr limit")

# ---------------------------------------------------------------------
# API HELPERS
# ---------------------------------------------------------------------

def safe_get(url: str) -> Any:
    r = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)

    # Secondary rate limit
    if r.status_code == 403 and not r.text.strip():
        time.sleep(30)
        return safe_get(url)

    remaining = r.headers.get("X-RateLimit-Remaining")
    reset = r.headers.get("X-RateLimit-Reset")
    if remaining and int(remaining) <= 0:
        wait = max(0, int(reset) - int(time.time()))
        time.sleep(wait)
        return safe_get(url)

    time.sleep(REQUEST_DELAY)
    return r.json()

# ---------------------------------------------------------------------
# DATA FETCHING
# ---------------------------------------------------------------------

def fetch_contributors() -> List[str]:
    data = safe_get(
        f"https://api.github.com/repos/{REPO}/contributors?per_page=100"
    )
    return [c["login"] for c in data if c.get("login")]


def fetch_account_age_years(login: str) -> float:
    user = safe_get(f"https://api.github.com/users/{login}")
    created_at = pd.to_datetime(user["created_at"], utc=True)
    now = datetime.now(timezone.utc)
    return (now - created_at).days / 365

# ---------------------------------------------------------------------
# ANALYSIS
# ---------------------------------------------------------------------

def collect_account_ages() -> pd.Series:
    ages = []

    for login in fetch_contributors():
        print(f"🔍 {login}")
        try:
            ages.append(fetch_account_age_years(login))
        except Exception:
            continue

    return pd.Series(ages, name="account_age_years")

# ---------------------------------------------------------------------
# PLOTTING
# ---------------------------------------------------------------------

def plot_account_age_distribution(ages: pd.Series) -> None:
    plt.figure(figsize=(8, 5))
    plt.hist(ages, bins=30)
    plt.title("Python SDK Contributor GitHub Account Age")
    plt.xlabel("Account Age (years)")
    plt.ylabel("Contributors")
    plt.grid(True, alpha=0.4)
    plt.tight_layout()

    out = OUT_DIR / "account_age_distribution.png"
    plt.savefig(out, dpi=300)
    plt.close()

    print(f"📊 Saved → {out}")

# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------

def main() -> None:
    ages = collect_account_ages()
    plot_account_age_distribution(ages)

if __name__ == "__main__":
    main()
