"""Synthetic snapshot generator so the project runs with zero credentials.

A reviewer cloning this repo should see a working dashboard in under a minute
without signing up for an API key. This module fabricates realistic stop-level
predictions, including the kind of dirty records the cleaning layer exists to
handle (absurd delays, cancelled trips, missing stop sequences).

The data is deterministic given a seed so CI is reproducible. It is clearly
labelled as synthetic in the ``feed`` column suffix so it can never be mistaken
for real Transport for NSW data.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone

import pandas as pd

from . import config

# Stand-ins for real Sydney routes. Names chosen so the dashboard reads as
# recognisably "Sydney" without claiming to be live data.
_ROUTES = {
    "sydneytrains": ["T1", "T2", "T3", "T4", "T8"],
    "buses": ["333", "380", "L90", "M10"],
    "ferries": ["F1", "F4"],
    "lightrail": ["L1"],
}

_STOPS = [f"stop_{i}" for i in range(1, 21)]


def _synth_feed(feed: str, rng: random.Random, fetched_at: str) -> pd.DataFrame:
    rows: list[dict] = []
    for route in _ROUTES[feed]:
        # Each route has its own "personality": some run late at peak, some are
        # reliable. This makes the dashboard's per-route comparison meaningful.
        route_bias = rng.uniform(-30, 240)
        n_trips = rng.randint(8, 20)
        for trip_n in range(n_trips):
            trip_id = f"{route}_trip_{trip_n}"
            for seq, stop in enumerate(rng.sample(_STOPS, rng.randint(5, 12)), 1):
                delay = int(rng.gauss(route_bias, 120))

                # Inject the messiness on purpose, at realistic rates:
                roll = rng.random()
                schedule_rel = 0  # SCHEDULED
                if roll < 0.01:
                    # ~1% absurd outlier (clock skew / stale cancelled service).
                    delay = rng.choice([7200, -3600, 99999])
                elif roll < 0.04:
                    # ~3% cancelled trips still emitting a stale prediction.
                    schedule_rel = 3  # CANCELED
                missing_seq = rng.random() < 0.05

                rows.append(
                    {
                        "feed": f"{feed}__synthetic",
                        "fetched_at": fetched_at,
                        "trip_id": trip_id,
                        "route_id": route,
                        "stop_id": stop,
                        "stop_sequence": None if missing_seq else seq,
                        "arrival_delay_s": delay,
                        "departure_delay_s": delay,
                        "delay_s": delay,
                        "schedule_relationship": schedule_rel,
                    }
                )
    return pd.DataFrame(rows)


def write_fixture_snapshot(seed: int = 42) -> list[config.FeedResult]:
    """Generate one synthetic snapshot per feed and land it in data/raw."""
    rng = random.Random(seed)
    fetched_at = datetime.now(timezone.utc).isoformat()
    stamp = datetime.now(timezone.utc).strftime(config.SNAPSHOT_TIME_FORMAT)
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)

    results: list[config.FeedResult] = []
    for feed in _ROUTES:
        df = _synth_feed(feed, rng, fetched_at)
        out_path = config.RAW_DIR / f"{feed}__synthetic_{stamp}.parquet"
        df.to_parquet(out_path, index=False)
        results.append(
            config.FeedResult(feed, fetched_at, len(df), out_path)
        )
        print(f"[fixtures] {feed}: {len(df)} synthetic rows -> {out_path.name}")
    return results


if __name__ == "__main__":
    write_fixture_snapshot()
