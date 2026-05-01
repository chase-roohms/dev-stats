#!/usr/bin/env python3
"""Generate charts from a GitHub stats history CSV."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator


DEFAULT_INPUT_PATH = "data/transmute-app-transmute-history.csv"
DEFAULT_EVENTS_PATH = "data/transmute-dates.json"
DEFAULT_OUTPUT_DIR = "images/transmute-app-transmute-history"

METRICS = [
    ("star_count", "Stars", "#0f766e"),
    ("fork_count", "Forks", "#d97706"),
    ("issue_count", "Open Issues", "#7c3aed"),
]


@dataclass(frozen=True)
class HistoryPoint:
    timestamp: datetime
    star_count: int
    watcher_count: int
    fork_count: int
    issue_count: int


@dataclass(frozen=True)
class EventMarker:
    timestamp: datetime
    event: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plot GitHub stats history CSV data into PNG charts.")
    parser.add_argument("--input", default=DEFAULT_INPUT_PATH, help="Path to the source CSV file")
    parser.add_argument(
        "--events",
        default=DEFAULT_EVENTS_PATH,
        help="Path to a JSON file containing dated events to annotate on the stars chart",
    )
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Directory where PNG charts will be written")
    parser.add_argument(
        "--title-prefix",
        default="transmute-app/transmute",
        help="Title prefix for the generated charts",
    )
    return parser


def get_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_path(path_str: str, repo_root: Path) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return repo_root / path


def load_history(csv_path: Path) -> list[HistoryPoint]:
    points: list[HistoryPoint] = []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            points.append(
                HistoryPoint(
                    timestamp=datetime.fromisoformat(row["datetime"]),
                    star_count=int(row["star_count"]),
                    watcher_count=int(row["watcher_count"]),
                    fork_count=int(row["fork_count"]),
                    issue_count=int(row["issue_count"]),
                )
            )
    points.sort(key=lambda point: point.timestamp)
    if not points:
        raise ValueError(f"No rows found in {csv_path}")
    return points


def load_event_markers(events_path: Path) -> list[EventMarker]:
    if not events_path.exists():
        return []

    with events_path.open("r", encoding="utf-8") as handle:
        raw_events = json.load(handle)

    markers: list[EventMarker] = []
    for entry in raw_events:
        markers.append(
            EventMarker(
                timestamp=datetime.fromisoformat(entry["date"]).replace(tzinfo=timezone.utc),
                event=entry["event"],
            )
        )

    markers.sort(key=lambda marker: marker.timestamp)
    return markers


def configure_time_axis(axis: plt.Axes) -> None:
    locator = mdates.AutoDateLocator(minticks=6, maxticks=10)
    formatter = mdates.ConciseDateFormatter(locator)
    axis.xaxis.set_major_locator(locator)
    axis.xaxis.set_major_formatter(formatter)


def annotate_event_markers(axis: plt.Axes, markers: list[EventMarker], values: list[int]) -> None:
    if not markers:
        return

    ymin = min(values)
    ymax = max(values)
    span = max(ymax - ymin, 1)
    label_levels = [0.06, 0.18, 0.30]

    for index, marker in enumerate(markers):
        axis.axvline(marker.timestamp, color="#475569", linestyle="--", linewidth=1.0, alpha=0.45)
        y_position = ymax - span * label_levels[index % len(label_levels)]
        axis.annotate(
            marker.event,
            xy=(marker.timestamp, y_position),
            xytext=(4, 0),
            textcoords="offset points",
            rotation=90,
            va="top",
            ha="left",
            fontsize=8,
            color="#334155",
            bbox={"boxstyle": "round,pad=0.2", "facecolor": "white", "edgecolor": "none", "alpha": 0.8},
        )


def save_single_metric_chart(
    points: list[HistoryPoint],
    metric_key: str,
    metric_label: str,
    color: str,
    output_path: Path,
    title_prefix: str,
    event_markers: list[EventMarker],
) -> None:
    figure, axis = plt.subplots(figsize=(13, 7), constrained_layout=True)
    timestamps = [point.timestamp for point in points]
    values = [getattr(point, metric_key) for point in points]

    axis.plot(timestamps, values, color=color, linewidth=2.4)
    axis.fill_between(timestamps, values, color=color, alpha=0.12)
    axis.set_title(f"{title_prefix} {metric_label} History")
    axis.set_xlabel("Date")
    axis.set_ylabel(metric_label)
    axis.yaxis.set_major_locator(MaxNLocator(integer=True))
    axis.grid(True, alpha=0.3)
    configure_time_axis(axis)

    if metric_key == "star_count":
        annotate_event_markers(axis, event_markers, values)

    figure.savefig(output_path, dpi=180)
    plt.close(figure)


def save_combined_chart(points: list[HistoryPoint], output_path: Path, title_prefix: str) -> None:
    figure, primary_axis = plt.subplots(figsize=(14, 8), constrained_layout=True)
    secondary_axis = primary_axis.twinx()
    timestamps = [point.timestamp for point in points]

    primary_series = [
        ("star_count", "Stars", "#0f766e"),
    ]
    secondary_series = [
        ("fork_count", "Forks", "#d97706"),
        ("issue_count", "Open Issues", "#7c3aed"),
    ]

    handles = []
    labels = []

    for metric_key, metric_label, color in primary_series:
        line, = primary_axis.plot(
            timestamps,
            [getattr(point, metric_key) for point in points],
            label=metric_label,
            color=color,
            linewidth=2.3,
        )
        handles.append(line)
        labels.append(metric_label)

    for metric_key, metric_label, color in secondary_series:
        linestyle = "--" if metric_key == "fork_count" else ":"
        line, = secondary_axis.plot(
            timestamps,
            [getattr(point, metric_key) for point in points],
            label=metric_label,
            color=color,
            linewidth=2.1,
            linestyle=linestyle,
        )
        handles.append(line)
        labels.append(metric_label)

    primary_axis.set_title(f"{title_prefix} Combined GitHub Metrics History")
    primary_axis.set_xlabel("Date")
    primary_axis.set_ylabel("Stars")
    secondary_axis.set_ylabel("Forks and Open Issues")
    primary_axis.yaxis.set_major_locator(MaxNLocator(integer=True))
    secondary_axis.yaxis.set_major_locator(MaxNLocator(integer=True))
    primary_axis.grid(True, alpha=0.3)
    configure_time_axis(primary_axis)
    primary_axis.legend(handles, labels, loc="upper left")

    figure.savefig(output_path, dpi=180)
    plt.close(figure)


def main() -> None:
    args = build_parser().parse_args()
    repo_root = get_repo_root()
    input_path = resolve_path(args.input, repo_root)
    events_path = resolve_path(args.events, repo_root)
    output_dir = resolve_path(args.output_dir, repo_root)
    output_dir.mkdir(parents=True, exist_ok=True)

    points = load_history(input_path)
    event_markers = load_event_markers(events_path)

    for metric_key, metric_label, color in METRICS:
        file_name = metric_key.replace("_count", "") + "-history.png"
        save_single_metric_chart(
            points=points,
            metric_key=metric_key,
            metric_label=metric_label,
            color=color,
            output_path=output_dir / file_name,
            title_prefix=args.title_prefix,
            event_markers=event_markers,
        )

    save_combined_chart(
        points=points,
        output_path=output_dir / "all-stats-history.png",
        title_prefix=args.title_prefix,
    )

    print(f"Saved 4 charts to {output_dir}")


if __name__ == "__main__":
    main()