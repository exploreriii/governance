"""
GitHub Good First Issue Timeline Plotter
========================================

Retrieves all GitHub issues with specific labels (e.g., "good first issue",
"good first issue candidate") for a repository or an organization and generates:

• Per-repo multilabel cumulative timeline PNGs  
• Organization-wide multilabel summary bar charts (PNG)

Run:
    uv run analytics/good_first_issues.py
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

load_dotenv()
HTTP_TIMEOUT = 10   # Request timeout in seconds

# -----------------------------
# SETTINGS
# -----------------------------
MODE = "org"                                    # "org" or "repo"
ORG = "hiero-ledger"                            # org name if MODE = "org"
REPO = "hiero-ledger/hiero-sdk-python"          # repo name if MODE = "repo"

LABELS = ["good first issue", "good first issue candidate"]
FILTER = "12m"                                   # Timeframe: "all" or "12m"

LABEL_COLORS = {
    "good first issue": "#1f77b4",               # blue
    "good first issue candidate": "#ff7f0e",     # orange
}

TOKEN = os.getenv("GITHUB_TOKEN")
HEADERS = {"Authorization": f"token {TOKEN}"} if TOKEN else {}
REQUEST_DELAY = 0.25

PLOTS_DIR = pathlib.Path("analytics/plots/good_first_issues") # Output location
PLOTS_DIR.mkdir(parents=True, exist_ok=True)

print("🔐 Using authenticated GitHub API" if TOKEN else "⚠️ No GITHUB_TOKEN found — 60 req/hr limit")

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
    ],
    "tooling": [
        "hiero-cli",
        "solo",
        "hiero-local-node",
        "hiero-local-node",
        "hiero-solo-action",
    ],
    "governance": [
        ".github",
        "hiero-improvement-proposals",
        "governance",
    ],
    "docs": [
        "hiero-docs",
        "hiero-website",
        "awesome-contributions",
    ],
}

def repo_layer(repo: str) -> str | None:
    name = repo.split("/")[-1]
    for layer, names in LAYERS.items():
        if name in names:
            return layer
    return None

REPO_LABEL_SERIES: dict[str, dict[str, pd.DataFrame]] = {}

# -----------------------------
# GITHUB API HELPERS
# -----------------------------
def safe_get(url: str) -> Any:
    """Perform GitHub GET request with basic rate limit handling."""
    response = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)
    remaining = int(response.headers.get("X-RateLimit-Remaining", "1"))
    reset_ts = int(response.headers.get("X-RateLimit-Reset", "0"))

    if remaining <= 0:
        wait = max(0, reset_ts - int(time.time()))
        print(f"⏳ Rate limit reached — waiting {wait} seconds…")
        time.sleep(wait)
        response = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)

    time.sleep(REQUEST_DELAY)
    return response.json()


def fetch_repos(org: str) -> List[str]:
    """Fetch all repository full names for a given organization."""
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


def fetch_issues_for_label(repo: str, label: str) -> List[Dict[str, Any]]:
    """Fetch all issues for a given repository with a specific label."""
    issues = []
    page = 1
    while True:
        url = (
            f"https://api.github.com/repos/{repo}/issues"
            f"?state=all&labels={label}&per_page=100&page={page}"
        )
        data = safe_get(url)
        if not isinstance(data, list):
            break
        issues.extend(i for i in data if isinstance(i, dict))
        if len(data) < 100:
            break
        page += 1
    return issues

# -----------------------------
# DATA HELPERS
# -----------------------------
def extract_timestamps(issues: List[Dict[str, Any]]) -> List[pd.Timestamp]:
    """Extract and convert issue creation timestamps to UTC-aware pd.Timestamp objects."""
    ts_raw = [
        issue["created_at"]
        for issue in issues
        if isinstance(issue.get("created_at"), str)
    ]

    # Convert to UTC-aware timestamps
    timestamps = []
    for ts in ts_raw:
        dt = pd.to_datetime(ts)
        timestamps.append(
            dt.tz_convert("UTC") if dt.tzinfo else dt.tz_localize("UTC")
        )
    return timestamps


def filter_dates(dates: List[pd.Timestamp], mode: str) -> List[pd.Timestamp]:
    """Filter dates based on the specified mode."""
    now = datetime.now(timezone.utc)
    if mode == "all":
        return dates
    cutoff = now - timedelta(days=365)
    return [d for d in dates if d >= cutoff]


def build_cumulative_df(dates: List[pd.Timestamp]) -> pd.DataFrame:
    """Build a cumulative count DataFrame from a list of timestamps."""
    df = pd.DataFrame({"created_at": dates})
    df.sort_values("created_at", inplace=True)
    df["count"] = range(1, len(df) + 1)
    return df


def summarize_repo_multilabel(repo: str, issues_by_label: Dict[str, List[Dict[str, Any]]]) -> dict:
    """Generate a summary dictionary for a repository across multiple labels."""
    summary = {"repo": repo}
    now = datetime.now(timezone.utc)

    for label, issues in issues_by_label.items():
        ts = extract_timestamps(issues)

        summary[f"{label}_total"] = len(ts)
        summary[f"{label}_12m"] = sum(d >= now - timedelta(days=365) for d in ts)

        summary[f"{label}_first"] = min(ts).date().isoformat() if ts else None
        summary[f"{label}_last"] = max(ts).date().isoformat() if ts else None

    return summary

# -----------------------------
# SINGLE MULTILABEL PLOT FUNCTION (canonical)
# -----------------------------
def plot_layer_label_timeseries(
    layer: str,
    label: str,
    repo_dfs: dict[str, pd.DataFrame],
) -> None:
    plt.figure(figsize=(12, 6))

    for repo, df in repo_dfs.items():
        plt.plot(
            df["created_at"],
            df["count"],
            label=repo.split("/")[-1],
            linewidth=2,
        )

    plt.title(f"{label.title()} Over Time — Layer: {layer}")
    plt.xlabel("Date")
    plt.ylabel("Cumulative Count")
    plt.grid(True, alpha=0.4)
    plt.legend(fontsize=9)
    plt.tight_layout()

    out = PLOTS_DIR / f"layer_{layer}_{label.replace(' ', '_')}_{FILTER}.png"
    plt.savefig(out, dpi=300)
    plt.close()

    print(f"📈 Saved layer plot → {out}")

def plot_multilabel_timeseries(series: dict, repo: str):
    """Plot cumulative time series for multiple labels on the same chart."""
    repo_safe = repo.replace("/", "_")
    filename = f"{repo_safe}_gfi_multilabel_{FILTER}.png"
    path = PLOTS_DIR / filename

    now = datetime.now(timezone.utc)

    # Determine x-axis window
    if FILTER == "all":
        x_min = min(df["created_at"].min() for df in series.values())
        x_max = max(df["created_at"].max() for df in series.values())
    else:  # "12m"
        x_min = now - timedelta(days=365)
        x_max = now

    plt.figure(figsize=(10, 5))

    for label, df in series.items():
        plt.plot(
            df["created_at"],
            df["count"],
            label=label,
            color=LABEL_COLORS[label],
            marker="o",
            markersize=3,
        )

    plt.xlim(x_min, x_max)
    plt.title(f"Good First Issue Labels Over Time ({FILTER}) • {repo}")
    plt.xlabel("Date")
    plt.ylabel("Cumulative Count")
    plt.grid(True, alpha=0.4)
    plt.legend()
    plt.tight_layout()
    plt.savefig(path, dpi=300)
    plt.close()

    print(f"   ✔ Saved plot → {path}")

# -----------------------------
# SUMMARY BAR CHARTS
# -----------------------------
def plot_summary_png_multilabel(summary_df: pd.DataFrame, metric_suffix: str, title: str):
    """Plot summary bar chart for multiple labels."""
    label_cols = [f"{label}_{metric_suffix}" for label in LABELS]
    df_sorted = summary_df.sort_values(label_cols[0], ascending=False)
    repos = df_sorted["repo"]

    plt.figure(figsize=(16, 12))

    bar_height = 0.35
    y = range(len(repos))

    for i, label in enumerate(LABELS):
        plt.barh(
            [yy + i * bar_height for yy in y],
            df_sorted[f"{label}_{metric_suffix}"],
            height=bar_height,
            label=label,
            color=LABEL_COLORS[label],
        )

    plt.xlabel("Count")
    plt.title(title)
    plt.yticks([yy + bar_height / 2 for yy in y], repos)
    plt.grid(axis="x", linestyle="--", alpha=0.6)
    plt.legend()
    plt.tight_layout()

    out = PLOTS_DIR / f"summary_multilabel_{metric_suffix}.png"
    plt.savefig(out, dpi=300)
    plt.close()

    print(f"📊 Saved → {out}")

# -----------------------------
# REPO WORKFLOW
# -----------------------------
def analyze_repo(repo: str) -> dict:
    print(f"\n📊 Processing {repo}...")

    issues_by_label = {label: fetch_issues_for_label(repo, label) for label in LABELS}
    summary = summarize_repo_multilabel(repo, issues_by_label)

    repo_series: dict[str, pd.DataFrame] = {}

    for label, issues in issues_by_label.items():
        ts = extract_timestamps(issues)
        ts_f = filter_dates(ts, FILTER)

        if ts_f:
            repo_series[label] = build_cumulative_df(ts_f)

    if repo_series:
        plot_multilabel_timeseries(repo_series, repo)
        REPO_LABEL_SERIES[repo] = repo_series
    else:
        print(f"   → No issues found in {FILTER} window")

    return summary


# -----------------------------
# MAIN
# -----------------------------
def main():
    """Main execution function."""
    if MODE == "repo":
        summaries = [analyze_repo(REPO)]
    else:
        repos = fetch_repos(ORG)
        summaries = [analyze_repo(r) for r in repos]

    df = pd.DataFrame(summaries)

    plot_summary_png_multilabel(df, "12m", "Good First Issue Labels — Last 12 Months")
    plot_summary_png_multilabel(df, "total", "Total Good First Issue Labels")

    # ---------------------------------------------------------------------
    # LAYER PLOTS
    # ---------------------------------------------------------------------
    layers: dict[str, dict[str, dict[str, pd.DataFrame]]] = {}

    for repo, label_map in REPO_LABEL_SERIES.items():
        layer = repo_layer(repo)
        if not layer:
            continue
        layers.setdefault(layer, {})
        for label, df in label_map.items():
            layers[layer].setdefault(label, {})
            layers[layer][label][repo] = df

    for layer, label_map in layers.items():
        for label, repo_dfs in label_map.items():
            if len(repo_dfs) > 1:
                plot_layer_label_timeseries(layer, label, repo_dfs)

if __name__ == "__main__":
    main()
