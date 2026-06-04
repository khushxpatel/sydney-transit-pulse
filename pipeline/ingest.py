"""Ingest layer: pull GTFS-Realtime trip updates and land them as snapshots.

This is deliberately the *only* module that talks to the network. It turns one
protobuf payload into a flat table of stop-level predictions and writes it to
``data/raw`` as Parquet. The GitHub Actions scraper commits those snapshots, so
the repo itself becomes a slowly-growing history of how Sydney's network ran.

Design notes for reviewers:
- The real world fails. Feeds 502, time out, and occasionally return an empty
  protobuf. We retry with backoff and record the failure rather than crashing
  the whole scrape, because losing one feed for one cycle is fine; losing the
  scheduler is not.
- We never trust the upstream blindly. Every row is validated and clamped in
  ``transform``; here we only do the structural parse.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from . import config

# Lazy import: gtfs-realtime-bindings pulls in protobuf, which we don't want to
# require for someone who only runs the dashboard against pre-built data.
try:
    from google.transit import gtfs_realtime_pb2
except ImportError:  # pragma: no cover - exercised only in minimal installs
    gtfs_realtime_pb2 = None


REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime(config.SNAPSHOT_TIME_FORMAT)


def fetch_feed_bytes(feed: str) -> bytes:
    """Fetch one feed's protobuf payload, retrying transient failures.

    Raises after the final attempt so the caller can decide whether one feed
    failing should abort the run (it shouldn't).
    """
    url = config.FEEDS[feed]
    headers = {"Authorization": f"apikey {config.require_api_key()}"}

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            if not resp.content:
                raise ValueError("empty response body")
            return resp.content
        except (requests.RequestException, ValueError) as err:
            last_error = err
            if attempt < MAX_RETRIES:
                sleep_for = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                print(f"[ingest] {feed} attempt {attempt} failed ({err}); "
                      f"retrying in {sleep_for}s")
                time.sleep(sleep_for)
    raise RuntimeError(f"failed to fetch {feed} after {MAX_RETRIES} attempts: "
                       f"{last_error}")


def parse_trip_updates(payload: bytes, feed: str, fetched_at: str) -> pd.DataFrame:
    """Flatten a GTFS-Realtime FeedMessage into stop-level prediction rows.

    One row per StopTimeUpdate. We keep the raw delay; cleaning happens later so
    that the snapshot on disk is a faithful record of what the agency actually
    published (you can always re-clean, you can never un-clean).
    """
    if gtfs_realtime_pb2 is None:
        raise RuntimeError(
            "gtfs-realtime-bindings is not installed. "
            "Run: pip install gtfs-realtime-bindings"
        )

    message = gtfs_realtime_pb2.FeedMessage()
    message.ParseFromString(payload)

    rows: list[dict] = []
    for entity in message.entity:
        if not entity.HasField("trip_update"):
            continue
        trip = entity.trip_update.trip
        for stu in entity.trip_update.stop_time_update:
            # A StopTimeUpdate may carry arrival, departure, both, or neither.
            # Prefer arrival delay (what a waiting passenger feels); fall back
            # to departure. Skip rows with no timing signal at all.
            arrival_delay = stu.arrival.delay if stu.HasField("arrival") else None
            departure_delay = (
                stu.departure.delay if stu.HasField("departure") else None
            )
            delay = arrival_delay if arrival_delay is not None else departure_delay
            if delay is None:
                continue
            rows.append(
                {
                    "feed": feed,
                    "fetched_at": fetched_at,
                    "trip_id": trip.trip_id,
                    "route_id": trip.route_id,
                    "stop_id": stu.stop_id,
                    "stop_sequence": stu.stop_sequence or None,
                    "arrival_delay_s": arrival_delay,
                    "departure_delay_s": departure_delay,
                    "delay_s": delay,
                    "schedule_relationship": trip.schedule_relationship,
                }
            )

    return pd.DataFrame(rows)


def snapshot_feed(feed: str) -> config.FeedResult:
    """Fetch, parse, and persist one feed. The unit the scraper calls per cycle."""
    fetched_at = _utc_now_iso()
    payload = fetch_feed_bytes(feed)
    df = parse_trip_updates(payload, feed, fetched_at)

    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.RAW_DIR / f"{feed}_{_utc_stamp()}.parquet"
    df.to_parquet(out_path, index=False)

    print(f"[ingest] {feed}: {len(df)} stop predictions -> {out_path.name}")
    return config.FeedResult(
        feed=feed,
        fetched_at=fetched_at,
        record_count=len(df),
        snapshot_path=out_path,
    )


def snapshot_all() -> list[config.FeedResult]:
    """Poll every configured feed. One feed failing does not stop the others."""
    results: list[config.FeedResult] = []
    for feed in config.FEEDS:
        try:
            results.append(snapshot_feed(feed))
        except Exception as err:  # noqa: BLE001 - we genuinely want to continue
            print(f"[ingest] WARNING: {feed} failed this cycle: {err}")
    return results


if __name__ == "__main__":
    if os.environ.get("USE_FIXTURE") == "1":
        from .fixtures import write_fixture_snapshot

        print("[ingest] USE_FIXTURE=1: generating synthetic snapshot")
        write_fixture_snapshot()
    else:
        snapshot_all()
