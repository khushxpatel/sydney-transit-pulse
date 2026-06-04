"""Central configuration for the Sydney Transit Pulse pipeline.

Everything that an operator might want to tune lives here so the rest of the
codebase reads cleanly. Values can be overridden with environment variables,
which is how the GitHub Actions scraper and the deployed dashboard configure
themselves without code changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Repo-relative paths. Resolved once so every module agrees on where data lives.
ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
FIXTURE_DIR = ROOT / "data" / "fixtures"
ANALYTICS_DB = ROOT / "data" / "analytics.duckdb"

# Transport for NSW Open Data real-time GTFS-R endpoints.
# Free API key: https://opendata.transport.nsw.gov.au (create app, copy the key).
API_BASE = "https://api.transport.nsw.gov.au/v1/gtfs/realtime"
API_KEY = os.environ.get("TFNSW_API_KEY", "")

# The feeds we poll. Each is a separate GTFS-Realtime protobuf endpoint.
# Keeping them in one place makes it trivial to add light rail, metro, etc.
FEEDS: dict[str, str] = {
    "sydneytrains": f"{API_BASE}/sydneytrains",
    "buses": f"{API_BASE}/buses",
    "ferries": f"{API_BASE}/ferries/sydneyferries",
    "lightrail": f"{API_BASE}/lightrail/innerwest",
}

# A stop counts as "on time" when its predicted delay is within this window.
# Transport agencies usually report on-time performance at 5 minutes, so we
# match the convention rather than inventing our own.
ON_TIME_THRESHOLD_SECONDS = 5 * 60

# Delays beyond this are almost always data artefacts (a cancelled service that
# never cleared, a clock-skew bug in a feed). We clamp them so one bad record
# cannot drag a route's average into nonsense. See docs/DATA_DECISIONS.md.
MAX_PLAUSIBLE_DELAY_SECONDS = 90 * 60

# How the scraper labels snapshots. UTC keeps ordering stable across DST.
SNAPSHOT_TIME_FORMAT = "%Y%m%dT%H%M%SZ"


@dataclass(frozen=True)
class FeedResult:
    """One poll of one feed. Carries enough context to debug a bad pull."""

    feed: str
    fetched_at: str  # ISO-8601 UTC
    record_count: int
    snapshot_path: Path


def require_api_key() -> str:
    """Fail loudly and early if the key is missing.

    A silent empty key produces a 401 buried in a stack trace later; surfacing
    it here with a fix-it message is the difference between a 30-second and a
    30-minute debugging session for whoever runs this next.
    """
    if not API_KEY:
        raise RuntimeError(
            "TFNSW_API_KEY is not set. Get a free key at "
            "https://opendata.transport.nsw.gov.au and export it, e.g.\n"
            "  export TFNSW_API_KEY=your_key_here\n"
            "Or run with USE_FIXTURE=1 to use bundled sample data."
        )
    return API_KEY
