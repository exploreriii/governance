"""
GitHub Contributors Timeline Plotter 
"""

from __future__ import annotations
import os
import time
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional

import requests
import pandas as pd
import matplotlib.pyplot as plt
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

# ---------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------

load_dotenv()

MODE = "org"                     # "org" or "repo"
ORG = "hiero-ledger"               # only if MODE = "org"
REPO = "hiero-ledger/hiero-sdk-python" # only if MODE = "repo"

FILTER = "12m"                   # "all" or "12m"

TOKEN = os.getenv("GITHUB_TOKEN")
HEADERS = {"Authorization": f"token {TOKEN}"} if TOKEN else {}
REQUEST_DELAY = 0.15             # lower delay now that parallel is used

PLOTS_DIR = pathlib.Path("analytics/plots/contributors_time")
PLOTS_DIR.mkdir(parents=True, exist_ok=True)

print("🔐 Authenticated GitHub API" if TOKEN else "⚠️ No GITHUB_TOKEN — 60 req/hr limit")


# -------------------------------------------------------------------
# LAYERS
# -------------------------------------------------------------------
LAYERS = {
    "core": [
        "hiero-consensus-node",
        "hiero-block-node",
        "hiero-mirror-node",
        "hiero-consensus-specifications",
    ],
    "sdks": [
        "hiero-sdk-java",
        "hiero-sdk-js",
        "hiero-sdk-python",
        "hiero-sdk-go",
        "hiero-sdk-swift",
        "hiero-sdk-cpp",
        "hiero-sdk-rust",
        "hiero-sdk-tck",
        "hiero-did-sdk-python",
        "hiero-did-sdk-js",
    ],
    "tooling": [
        "hiero-cli",
        "solo",
        "hiero-local-node",
        "hiero-solo-action",
        "homebrew-tools",
        "hiero-gradle-conventions",
        "hiero-hederium",
    ],
    "governance": [
        ".github",
        "hiero-improvement-proposals",
        "governance",
        "tsc",
        "tsc-eligibility-check",
        "sdk-collaboration-hub",
        "identity-collaboration-hub",
    ],
    "docs": [
        "hiero-docs",
        "hiero-website",
        "awesome-contributions",
        "hiero",
    ],
}

LAYER_COLORS = {
    "core": "#1f77b4",
    "sdks": "#ff7f0e",
    "tooling": "#2ca02c",
    "governance": "#9467bd",
    "docs": "#d62728",
    "other": "#7f7f7f",
}

def repo_layer(repo: str) -> Optional[str]:
    name = repo.split("/")[-1]
    for layer, prefixes in LAYERS.items():
        if name in prefixes:
            return layer
    return None


REPO_TIME_SERIES: Dict[str, pd.DataFrame] = {}

# ---------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------
def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


# ---------------------------------------------------------------------
# SAFE API REQUEST
# ---------------------------------------------------------------------
def safe_get(url: str, max_retries: int = 8) -> Any:
    attempts = 0

    while True:
        r = requests.get(url, headers=HEADERS)
        status = r.status_code

        # Secondary rate limit / abuse detection
        if status == 403 and not r.text.strip():
            log("🚦 Secondary rate limit hit — backing off 30s…")
            time.sleep(30)
            continue

        # Contributors API can return 204 legitimately
        if status == 204:
            return []

        # GitHub stats generation
        if status == 202:
            attempts += 1
            if attempts >= max_retries:
                log(f"⚠️ Stats not ready after {attempts} attempts — skipping.")
                return []
            log("⏳ GitHub generating stats — waiting 5s…")
            time.sleep(5)
            continue

        # Normal rate limit handling
        remaining = r.headers.get("X-RateLimit-Remaining")
        reset_ts = r.headers.get("X-RateLimit-Reset")
        if remaining is not None and int(remaining) <= 0:
            wait = max(0, int(reset_ts) - int(time.time()))
            log(f"⏳ Rate limit hit — waiting {wait}s…")
            time.sleep(wait)
            continue

        if not r.text.strip():
            log(f"⚠️ Empty body from {url} — treating as no data.")
            return []

        try:
            data = r.json()
        except Exception:
            attempts += 1
            if attempts >= max_retries:
                log(f"❌ JSON decode failed — giving up on {url}")
                return []
            time.sleep(1)
            continue

        time.sleep(REQUEST_DELAY)
        return data

# ---------------------------------------------------------------------
# FETCH HELPERS
# ---------------------------------------------------------------------
def fetch_repos(org: str) -> List[str]:
    """Fetch all repos for an org (paginated)."""
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


def fetch_user_first_commit(repo: str, login: str) -> tuple[str, Optional[pd.Timestamp]]:
    """
    Fetch earliest commit timestamp for a single user.
    Used in parallel pooled execution.
    """
    url = (
        f"https://api.github.com/repos/{repo}/commits"
        f"?author={login}&per_page=100"
    )
    commit_data = safe_get(url)

    if not isinstance(commit_data, list):
        return login, None

    timestamps = [
        pd.to_datetime(c["commit"]["author"]["date"], utc=True)
        for c in commit_data
        if c.get("commit", {}).get("author", {}).get("date")
    ]

    return login, min(timestamps) if timestamps else None


def fetch_contributor_first_dates(repo: str) -> Dict[str, pd.Timestamp]:
    """
    Fast parallel version:
    - Gets contributors list
    - Uses a thread pool to fetch first commits per contributor
    """
    contribs_url = f"https://api.github.com/repos/{repo}/contributors?per_page=100&anon=true"
    contributors = safe_get(contribs_url)

    if not isinstance(contributors, list):
        log(f"⚠️ Could not fetch contributors for {repo}")
        return {}

    logins = [
        c.get("login") or c.get("name")
        for c in contributors
        if (c.get("login") or c.get("name"))
    ]

    log(f"   👥 {repo} → {len(logins)} contributors (checking commits in parallel…)")

    first_dates: Dict[str, pd.Timestamp] = {}

    # Limit threads to avoid abuse detection
    max_workers = min(10, len(logins)) or 1

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(fetch_user_first_commit, repo, login): login
            for login in logins
        }

        for future in as_completed(futures):
            login, ts = future.result()
            if ts:
                first_dates[login] = ts

    return first_dates


# ---------------------------------------------------------------------
# PROCESSING
# ---------------------------------------------------------------------
def filter_dates(dates: List[pd.Timestamp], mode: str) -> List[pd.Timestamp]:
    if mode == "all":
        return dates
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=365)
    return [d for d in dates if d >= cutoff]


def build_cumulative_df(dates: List[pd.Timestamp]) -> pd.DataFrame:
    df = pd.DataFrame({"created_at": dates})
    df.sort_values("created_at", inplace=True)
    df["count"] = range(1, len(df) + 1)
    return df


def summarize(repo: str, dates: List[pd.Timestamp]) -> dict:
    now = datetime.now(timezone.utc)
    dates_sorted = sorted(dates)
    return {
        "repo": repo,
        "total": len(dates_sorted),
        "last_12m": sum(d >= now - timedelta(days=365) for d in dates_sorted),
        "first": dates_sorted[0].date().isoformat() if dates_sorted else None,
        "last": dates_sorted[-1].date().isoformat() if dates_sorted else None,
    }


# ---------------------------------------------------------------------
# PLOTTING
# ---------------------------------------------------------------------

def plot_summary(df: pd.DataFrame, metric: str, title: str) -> None:
    df_sorted = df.sort_values(metric, ascending=False)

    plt.figure(figsize=(14, 12))
    plt.barh(df_sorted["repo"], df_sorted[metric])
    plt.xlabel("Contributors")
    plt.title(title)
    plt.gca().invert_yaxis()
    plt.grid(axis="x", linestyle="--", alpha=0.6)
    plt.tight_layout()

    path = PLOTS_DIR / f"contributors_summary_{metric}.png"
    plt.savefig(path, dpi=300)
    plt.close()

    log(f"📊 Saved summary → {path}")

# ---------------------------------------------------------------------
# NEW: LAYER COMPARISON PLOT
# ---------------------------------------------------------------------
def plot_layer_contributors(
    layer: str,
    repo_dfs: Dict[str, pd.DataFrame],
) -> None:
    plt.figure(figsize=(12, 6))

    for repo, df in repo_dfs.items():
        plt.plot(
            df["created_at"],
            df["count"],
            label=repo.split("/")[-1],
            linewidth=2,
        )

    plt.title(f"Contributor Growth by Repo — Layer: {layer}")
    plt.xlabel("Date")
    plt.ylabel("Cumulative Contributors")
    plt.grid(True, alpha=0.4)
    plt.legend(fontsize=9)
    plt.tight_layout()

    path = PLOTS_DIR / f"layer_{layer}_contributors_{FILTER}.png"
    plt.savefig(path, dpi=300)
    plt.close()

    log(f"📈 Saved layer comparison → {path}")

# ---------------------------------------------------------------------
# WORKFLOW
# ---------------------------------------------------------------------
def analyze_repo(repo: str) -> dict:
    log(f"\n📊 Processing {repo}")

    first_dates = fetch_contributor_first_dates(repo)
    timestamps = sorted(first_dates.values())

    summary = summarize(repo, timestamps)

    filtered = filter_dates(timestamps, FILTER)
    if filtered:
        df = build_cumulative_df(filtered)

        # Store ONLY for summaries + layer comparisons
        REPO_TIME_SERIES[repo] = df

    else:
        log("   → No contributors in selected window")

    return summary


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------
def main() -> None:
    if MODE == "repo":
        repos = [REPO]
    else:
        repos = fetch_repos(ORG)

    log(f"Analyzing {len(repos)} repositories…")

    summaries = [analyze_repo(r) for r in repos]
    df = pd.DataFrame(summaries)

    plot_summary(df, "last_12m", "Contributors Active in Last 12 Months")
    plot_summary(df, "total", "Total Contributors")

    layers: Dict[str, Dict[str, pd.DataFrame]] = {}

    for repo, ts_df in REPO_TIME_SERIES.items():
        layer = repo_layer(repo)
        if not layer:
            continue
        layers.setdefault(layer, {})[repo] = ts_df

    for layer, repo_dfs in layers.items():
        if len(repo_dfs) > 1:
            plot_layer_contributors(layer, repo_dfs)

if __name__ == "__main__":
    main()
