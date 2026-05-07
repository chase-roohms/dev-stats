#!/usr/bin/env python3
"""Export repository metric history from historical versions of data/github-stats.json."""

from __future__ import annotations

import argparse
import csv
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import requests


DEFAULT_REPO_OWNER = "chase-roohms"
DEFAULT_REPO_NAME = "dev-stats"
DEFAULT_STATS_PATH = "data/github-stats.json"
DEFAULT_TARGET_REPOSITORY = "transmute-app/transmute"
DEFAULT_OUTPUT_PATH = "data/transmute-app-transmute-history.csv"
DEFAULT_SKIPPED_OUTPUT_PATH = "data/transmute-app-transmute-history-skipped.json"
DEFAULT_API_BASE_URL = "https://api.github.com"
DEFAULT_RAW_BASE_URL = "https://raw.githubusercontent.com"
CSV_FIELDNAMES = ["sha", "datetime", "star_count", "watcher_count", "fork_count", "issue_count"]


@dataclass(frozen=True)
class FileCommit:
    sha: str
    committed_at: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download every historical revision of data/github-stats.json from GitHub "
            "and export one repository's metrics as CSV."
        )
    )
    parser.add_argument("--repo-owner", default=DEFAULT_REPO_OWNER, help="GitHub owner for this repository")
    parser.add_argument("--repo-name", default=DEFAULT_REPO_NAME, help="GitHub repository name for this repository")
    parser.add_argument("--stats-path", default=DEFAULT_STATS_PATH, help="Path to the tracked JSON file inside the repository")
    parser.add_argument(
        "--target-repository",
        default=DEFAULT_TARGET_REPOSITORY,
        help="Full owner/name of the repository to export from github-stats.json",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_PATH,
        help="Output CSV path, relative to the repository root unless absolute",
    )
    parser.add_argument(
        "--skipped-output",
        default=DEFAULT_SKIPPED_OUTPUT_PATH,
        help="Output JSON path for commits that do not contain the target repository",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit for the number of historical revisions to process",
    )
    parser.add_argument(
        "--keep-temp-dir",
        action="store_true",
        help="Keep the downloaded historical JSON files instead of deleting the temporary directory",
    )
    return parser


def get_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    )

    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        session.headers["Authorization"] = f"Bearer {github_token}"

    return session


def iter_file_commits(
    session: requests.Session,
    repo_owner: str,
    repo_name: str,
    stats_path: str,
    limit: int | None = None,
) -> Iterator[FileCommit]:
    page = 1
    emitted = 0

    while True:
        response = session.get(
            f"{DEFAULT_API_BASE_URL}/repos/{repo_owner}/{repo_name}/commits",
            params={"path": stats_path, "per_page": 100, "page": page},
            timeout=30,
        )
        response.raise_for_status()
        commits = response.json()

        if not commits:
            return

        for commit in commits:
            yield FileCommit(
                sha=commit["sha"],
                committed_at=commit["commit"]["committer"]["date"],
            )
            emitted += 1
            if limit is not None and emitted >= limit:
                return

        page += 1


def download_commit_file(
    session: requests.Session,
    repo_owner: str,
    repo_name: str,
    stats_path: str,
    commit: FileCommit,
    temp_dir: Path,
) -> Path:
    raw_url = f"{DEFAULT_RAW_BASE_URL}/{repo_owner}/{repo_name}/{commit.sha}/{stats_path}"
    response = session.get(raw_url, timeout=30)
    response.raise_for_status()

    temp_file = temp_dir / f"{commit.committed_at.replace(':', '-')}__{commit.sha}.json"
    temp_file.write_text(response.text, encoding="utf-8")
    return temp_file


def extract_history_row(json_path: Path, commit: FileCommit, target_repository: str) -> dict[str, int | str] | None:
    with json_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    repository_stats = payload.get("repositories", {}).get(target_repository)
    if not repository_stats:
        return None

    return {
        "sha": commit.sha,
        "datetime": payload.get("last_updated", commit.committed_at),
        "star_count": repository_stats.get("stars", 0),
        "watcher_count": repository_stats.get("watchers", 0),
        "fork_count": repository_stats.get("forks", 0),
        "issue_count": repository_stats.get("open_issues", 0),
    }


def load_existing_rows(output_path: Path) -> dict[str, dict[str, int | str]]:
    if not output_path.exists():
        return {}

    with output_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows_by_sha: dict[str, dict[str, int | str]] = {}
        for row in reader:
            sha = row.get("sha")
            if not sha:
                continue

            rows_by_sha[sha] = {
                "sha": sha,
                "datetime": row["datetime"],
                "star_count": int(row["star_count"]),
                "watcher_count": int(row["watcher_count"]),
                "fork_count": int(row["fork_count"]),
                "issue_count": int(row["issue_count"]),
            }

    return rows_by_sha


def load_existing_skipped_commits(skipped_output_path: Path) -> dict[str, str]:
    if not skipped_output_path.exists():
        return {}

    with skipped_output_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if not isinstance(payload, list):
        raise ValueError(f"Expected a list of skipped commits in {skipped_output_path}")

    skipped_commits: dict[str, str] = {}
    for entry in payload:
        if not isinstance(entry, dict):
            continue

        sha = entry.get("sha")
        committed_at = entry.get("committed_at")
        if not isinstance(sha, str) or not isinstance(committed_at, str):
            continue

        skipped_commits[sha] = committed_at

    return skipped_commits


def write_csv(output_path: Path, rows: list[dict[str, int | str]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def write_skipped_commits(skipped_output_path: Path, skipped_commits_by_sha: dict[str, str]) -> None:
    skipped_output_path.parent.mkdir(parents=True, exist_ok=True)
    serialized_commits = [
        {"sha": sha, "committed_at": committed_at}
        for sha, committed_at in sorted(skipped_commits_by_sha.items(), key=lambda item: item[1])
    ]
    skipped_output_path.write_text(json.dumps(serialized_commits, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    args = build_parser().parse_args()
    repo_root = get_repo_root()
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = repo_root / output_path
    skipped_output_path = Path(args.skipped_output)
    if not skipped_output_path.is_absolute():
        skipped_output_path = repo_root / skipped_output_path

    session = build_session()
    existing_rows_by_sha = load_existing_rows(output_path)
    skipped_commits_by_sha = load_existing_skipped_commits(skipped_output_path)

    historical_rows: list[dict[str, int | str]] = []
    commit_count = 0
    reused_row_count = 0
    downloaded_row_count = 0
    skipped_commit_count = 0
    reused_skipped_commit_count = 0

    if args.keep_temp_dir:
        temp_dir_path = Path(tempfile.mkdtemp(prefix="github-stats-history-"))
        cleanup = False
    else:
        temp_dir_context = tempfile.TemporaryDirectory(prefix="github-stats-history-")
        temp_dir_path = Path(temp_dir_context.__enter__())
        cleanup = True

    try:
        print(f"Downloading historical revisions to {temp_dir_path}")
        for commit in iter_file_commits(
            session=session,
            repo_owner=args.repo_owner,
            repo_name=args.repo_name,
            stats_path=args.stats_path,
            limit=args.limit,
        ):
            commit_count += 1
            cached_row = existing_rows_by_sha.get(commit.sha)
            if cached_row is not None:
                reused_row_count += 1
                historical_rows.append(cached_row)
                print(f"[{commit_count}] Reusing {commit.sha} ({commit.committed_at}) from {output_path}")
                continue

            if commit.sha in skipped_commits_by_sha:
                reused_skipped_commit_count += 1
                print(f"[{commit_count}] Reusing skipped {commit.sha} ({commit.committed_at}) from {skipped_output_path}")
                continue

            print(f"[{commit_count}] Downloading {commit.sha} ({commit.committed_at})")
            json_path = download_commit_file(
                session=session,
                repo_owner=args.repo_owner,
                repo_name=args.repo_name,
                stats_path=args.stats_path,
                commit=commit,
                temp_dir=temp_dir_path,
            )
            row = extract_history_row(
                json_path=json_path,
                commit=commit,
                target_repository=args.target_repository,
            )
            if row is not None:
                downloaded_row_count += 1
                historical_rows.append(row)
            else:
                skipped_commit_count += 1
                skipped_commits_by_sha[commit.sha] = commit.committed_at

        historical_rows.reverse()
        write_csv(output_path=output_path, rows=historical_rows)
        write_skipped_commits(skipped_output_path=skipped_output_path, skipped_commits_by_sha=skipped_commits_by_sha)
        print(
            f"Wrote {len(historical_rows)} rows to {output_path} "
            f"({reused_row_count} reused, {downloaded_row_count} downloaded"
            f", {skipped_commit_count} newly skipped, {reused_skipped_commit_count} skipped reused)"
        )
        print(f"Wrote {len(skipped_commits_by_sha)} skipped commits to {skipped_output_path}")
        if args.keep_temp_dir:
            print(f"Kept downloaded revisions in {temp_dir_path}")
    finally:
        session.close()
        if cleanup:
            temp_dir_context.__exit__(None, None, None)


if __name__ == "__main__":
    main()
