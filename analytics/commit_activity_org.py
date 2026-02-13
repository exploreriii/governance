"""
GitHub Org Commit Activity Ledger
=================================

Aggregates GitHub's repo-level "Commit activity" stats into an org-wide
weekly time series (last 52 weeks), similar to GitHub’s Insights chart.

Generates:
• Org-wide weekly commits (smoothed)
• Commit intensity (commits per active repo)
• Optional layered ledger (smoothed)
• CSV export

Run:
    uv run analytics/commit_activity_org.py --warm
    uv run analytics/commit_activity_org.py
"""

from __future__ import annotations
import sys
import os
import time
import math
import pathlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
import pandas as pd
import matplotlib.pyplot as plt
from dotenv import load_dotenv

# -------------------------------------------------------------------
# ENV / GLOBALS
# -------------------------------------------------------------------
load_dotenv()

ORG = "hiero-ledger"
HTTP_TIMEOUT = 15
REQUEST_DELAY = 0.25

SHOW_ACTIVE_REPOS = True
SHOW_LAYERED_LEDGER = True
USE_WEIGHTING = False

TOKEN = os.getenv("GITHUB_TOKEN")
HEADERS = {"Authorization": f"token {TOKEN}"} if TOKEN else {}
print("🔐 Using authenticated GitHub API" if TOKEN else "⚠️ No GITHUB_TOKEN found — 60 req/hr limit")

BASE_DIR = pathlib.Path("analytics")
PLOTS_DIR = BASE_DIR / "plots/commit_activity"
CACHE_DIR = BASE_DIR / ".cache"

PLOTS_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

WARMED_REPOS_FILE = CACHE_DIR / "warmed_commit_activity.txt"

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

# -------------------------------------------------------------------
# HTTP HELPERS
# -------------------------------------------------------------------
def safe_get(url: str) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)
    remaining = int(r.headers.get("X-RateLimit-Remaining", "1"))
    reset_ts = int(r.headers.get("X-RateLimit-Reset", "0"))

    if remaining <= 0:
        wait = max(0, reset_ts - int(time.time()))
        print(f"⏳ Rate limit reached — waiting {wait}s")
        time.sleep(wait)
        r = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)

    time.sleep(REQUEST_DELAY)
    return r


def safe_get_json(url: str) -> Any:
    r = safe_get(url)

    if r.status_code in (202, 204):
        return {"__status__": r.status_code}

    if r.status_code >= 400:
        return {"__status__": r.status_code, "__error__": r.text}

    if not r.text.strip():
        return {"__status__": r.status_code}

    try:
        return r.json()
    except Exception:
        return {"__status__": r.status_code}

# -------------------------------------------------------------------
# WARM CACHE
# -------------------------------------------------------------------
def warm_stats_cache(repos: List[str], sleep_s: int = 2):
    warmed = set()
    if WARMED_REPOS_FILE.exists():
        warmed = set(WARMED_REPOS_FILE.read_text().splitlines())

    to_warm = [r for r in repos if r not in warmed]

    print(f"\n🔥 Warming commit activity stats")
    print(f"   → {len(to_warm)} repos to warm")
    print(f"   → {len(warmed)} already warm\n")

    for i, full_name in enumerate(to_warm, 1):
        print(f"[{i}/{len(to_warm)}] 🔥 {full_name}")
        data = safe_get_json(f"https://api.github.com/repos/{full_name}/stats/commit_activity")

        if isinstance(data, list):
            print("   ✅ stats ready")
        else:
            print("   ⏳ computing")

        warmed.add(full_name)
        time.sleep(sleep_s)

    WARMED_REPOS_FILE.write_text("\n".join(sorted(warmed)))
    print("\n🔥 Warm-up complete\n")

# -------------------------------------------------------------------
# GITHUB API
# -------------------------------------------------------------------
def fetch_repos(org: str) -> List[Dict[str, Any]]:
    repos = []
    page = 1
    while True:
        url = f"https://api.github.com/orgs/{org}/repos?per_page=100&page={page}&type=all"
        data = safe_get_json(url)
        if not isinstance(data, list):
            break
        repos.extend(data)
        if len(data) < 100:
            break
        page += 1
    return repos


def fetch_commit_activity(full_name: str) -> Optional[List[Dict[str, Any]]]:
    url = f"https://api.github.com/repos/{full_name}/stats/commit_activity"
    start = time.time()

    while True:
        data = safe_get_json(url)

        if isinstance(data, list):
            return data

        if data.get("__status__") == 202 and time.time() - start < 60:
            time.sleep(3)
            continue

        return None

# -------------------------------------------------------------------
# DATA BUILDING
# -------------------------------------------------------------------
def layer_for_repo(full_name: str) -> str:
    name = full_name.split("/", 1)[1].lower()
    for layer, needles in LAYERS.items():
        if any(n in name for n in needles):
            return layer
    return "other"


def build_org_weekly_df(repos_meta: List[Dict[str, Any]]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []

    # -----------------------------
    # COLLECT REPO-WEEK ROWS
    # -----------------------------
    for repo in repos_meta:
        full_name = repo["full_name"]
        print(f"📦 {full_name}")

        activity = fetch_commit_activity(full_name)
        if not activity:
            continue

        layer = layer_for_repo(full_name)

        for wk in activity:
            total = int(wk.get("total", 0) or 0)
            rows.append(
                {
                    "week_start": datetime.fromtimestamp(int(wk["week"]), tz=timezone.utc),
                    "repo": full_name.split("/", 1)[1],   # 👈 ADD THIS
                    "layer": layer,
                    "commits": total,
                    "active": 1 if total > 0 else 0,
                }
            )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    # Ensure numeric dtypes (critical for rolling ops)
    df["commits"] = pd.to_numeric(df["commits"], errors="coerce").fillna(0)
    df["active"] = pd.to_numeric(df["active"], errors="coerce").fillna(0)

    # -----------------------------
    # ORG-WIDE WEEKLY AGGREGATION
    # -----------------------------
    org = (
        df.groupby("week_start", as_index=False)
        .agg(
            commits=("commits", "sum"),
            active_repos=("active", "sum"),
        )
        .sort_values("week_start")
    )

    # Force numeric again (defensive, but cheap)
    org["commits"] = pd.to_numeric(org["commits"], errors="coerce").fillna(0)
    org["active_repos"] = pd.to_numeric(org["active_repos"], errors="coerce").fillna(0)

    # -----------------------------
    # SMOOTHING / SIGNALS
    # -----------------------------
    org["commits_4w_avg"] = org["commits"].rolling(4, min_periods=1).mean()
    org["commits_8w_avg"] = org["commits"].rolling(8, min_periods=1).mean()

    # Commit intensity (the most useful signal)
    org["commits_per_repo"] = org["commits"] / org["active_repos"].replace(0, pd.NA)
    org["commits_per_repo"] = pd.to_numeric(
        org["commits_per_repo"], errors="coerce"
    )

    org["commits_per_repo_4w_avg"] = (
        org["commits_per_repo"]
        .rolling(4, min_periods=1)
        .mean()
    )

    # -----------------------------
    # LAYERED LEDGER (OPTIONAL)
    # -----------------------------
    if SHOW_LAYERED_LEDGER:
        lw = (
            df.groupby(["week_start", "layer"], as_index=False)
            .agg(commits=("commits", "sum"))
            .pivot(index="week_start", columns="layer", values="commits")
            .fillna(0)
        )

        # Force numeric before rolling
        lw = lw.apply(pd.to_numeric, errors="coerce").fillna(0)

        for col in lw.columns:
            lw[f"{col}_4w_avg"] = lw[col].rolling(4, min_periods=1).mean()

        org = org.merge(lw.reset_index(), on="week_start", how="left")

    return org, df

# -------------------------------------------------------------------
# PLOTS
# -------------------------------------------------------------------
def plot_org_commits(df: pd.DataFrame):
    plt.figure(figsize=(13, 6))
    plt.plot(df["week_start"], df["commits"], alpha=0.2, label="Weekly")
    plt.plot(df["week_start"], df["commits_4w_avg"], linewidth=2.8, label="4w avg")
    plt.plot(df["week_start"], df["commits_8w_avg"], linestyle="--", label="8w avg")
    plt.title(f"Org Commit Activity • {ORG}")
    plt.grid(alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(PLOTS_DIR / f"{ORG}_org_commits.png", dpi=300)
    plt.close()


def plot_commit_intensity(df: pd.DataFrame):
    plt.figure(figsize=(13, 6))
    plt.plot(df["week_start"], df["commits_per_repo_4w_avg"], linewidth=3)
    plt.title("Commit Intensity (4w avg commits per active repo)")
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(PLOTS_DIR / f"{ORG}_commit_intensity.png", dpi=300)
    plt.close()


def plot_layered_ledger(df: pd.DataFrame):
    cols = [c for c in df.columns if c.endswith("_4w_avg") and c not in (
        "commits_4w_avg",
        "commits_per_repo_4w_avg",
    )]

    plt.figure(figsize=(13, 6))
    for col in cols:
        base = col.replace("_4w_avg", "")
        plt.plot(df["week_start"], df[col], label=base, color=LAYER_COLORS.get(base))

    plt.title("Layered Commit Ledger (4w avg)")
    plt.grid(alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(PLOTS_DIR / f"{ORG}_layered_ledger.png", dpi=300)
    plt.close()

def plot_layer_repo_charts(df: pd.DataFrame):
    """
    For each layer, generate a chart showing smoothed commit activity
    for each repo in that layer.
    """
    repo_dir = PLOTS_DIR / "by_layer"
    repo_dir.mkdir(exist_ok=True)

    for layer in sorted(df["layer"].unique()):
        layer_df = df[df["layer"] == layer]

        if layer_df.empty:
            continue

        # Aggregate per repo per week
        repo_week = (
            layer_df.groupby(["week_start", "repo"], as_index=False)
            .agg(commits=("commits", "sum"))
            .sort_values("week_start")
        )

        # Pivot to repo columns
        pivot = repo_week.pivot(
            index="week_start",
            columns="repo",
            values="commits"
        ).fillna(0)

        # Smooth each repo (4-week avg)
        smooth = pivot.rolling(4, min_periods=1).mean()

        # Drop repos that are effectively dead (noise control)
        raw_totals = pivot.sum()

        active_repos = [
            repo for repo in raw_totals.index
            if raw_totals[repo] >= 5   # tune this (e.g. 1, 3, 10)
        ]

        if not active_repos:
            continue

        plt.figure(figsize=(14, 7))

        for repo in active_repos:
            plt.plot(
                smooth.index,
                smooth[repo],
                linewidth=2,
                alpha=0.85,
                label=repo
            )

        plt.title(f"{ORG} • {layer} repos (4w avg commits)")
        plt.xlabel("Week")
        plt.ylabel("Commits (4-week avg)")
        plt.grid(alpha=0.3)
        plt.legend(fontsize=8, ncol=2)
        plt.tight_layout()

        out = repo_dir / f"{ORG}_{layer}_repos.png"
        plt.savefig(out, dpi=300)
        plt.close()

        print(f"📊 Saved → {out}")

# -------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------
def main():
    repos = fetch_repos(ORG)
    repo_names = [r["full_name"] for r in repos]

    if "--warm" in sys.argv:
        warm_stats_cache(repo_names)
        return

    org_df, raw_df = build_org_weekly_df(repos)


    plot_org_commits(org_df)
    plot_commit_intensity(org_df)

    if SHOW_LAYERED_LEDGER:
        plot_layered_ledger(org_df)

    plot_layer_repo_charts(raw_df)


    org_df.to_csv(PLOTS_DIR / f"{ORG}_commit_activity.csv", index=False)
    raw_df.to_csv(PLOTS_DIR / f"{ORG}_commit_activity_raw.csv", index=False)
    print("✅ Charts & CSV generated")

if __name__ == "__main__":
    main()
