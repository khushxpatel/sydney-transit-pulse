"""Tests for the cleaning and aggregation logic.

These guard the rules that make the numbers defensible: cancelled services are
excluded, clock-skew outliers are clamped not dropped, null delays never become
fake zeros, and on-time performance matches the documented 5-minute threshold.
"""

from __future__ import annotations

import pandas as pd
import pytest

from pipeline import config, transform


def _row(**overrides) -> dict:
    base = {
        "feed": "sydneytrains__synthetic",
        "fetched_at": "2026-06-04T00:00:00+00:00",
        "trip_id": "T1_trip_0",
        "route_id": "T1",
        "stop_id": "stop_1",
        "stop_sequence": 1,
        "arrival_delay_s": 60,
        "departure_delay_s": 60,
        "delay_s": 60,
        "schedule_relationship": 0,
    }
    base.update(overrides)
    return base


def test_drops_cancelled_services():
    df = pd.DataFrame([_row(), _row(schedule_relationship=3, delay_s=600)])
    cleaned = transform.clean(df)
    assert len(cleaned) == 1
    assert (cleaned["schedule_relationship"] == 0).all()


def test_drops_null_delays_rather_than_imputing():
    df = pd.DataFrame([_row(), _row(delay_s=None, arrival_delay_s=None,
                                    departure_delay_s=None)])
    cleaned = transform.clean(df)
    assert len(cleaned) == 1
    assert cleaned["delay_s"].notna().all()


def test_clamps_outliers_without_dropping_them():
    huge = config.MAX_PLAUSIBLE_DELAY_SECONDS * 100
    df = pd.DataFrame([_row(delay_s=huge)])
    cleaned = transform.clean(df)
    # Row is kept (a disruption is signal) but clamped to the plausible ceiling.
    assert len(cleaned) == 1
    assert cleaned["delay_s"].iloc[0] == config.MAX_PLAUSIBLE_DELAY_SECONDS


def test_on_time_flag_uses_five_minute_threshold():
    df = pd.DataFrame([
        _row(delay_s=config.ON_TIME_THRESHOLD_SECONDS),       # exactly 5 min -> on time
        _row(delay_s=config.ON_TIME_THRESHOLD_SECONDS + 1),   # 5 min 1s -> late
    ])
    cleaned = transform.clean(df)
    assert cleaned.sort_values("delay_s")["on_time"].tolist() == [True, False]


def test_aggregate_computes_on_time_rate_as_percentage():
    df = pd.DataFrame([_row(delay_s=0), _row(delay_s=0),
                       _row(delay_s=3600), _row(delay_s=3600)])
    cleaned = transform.clean(df)
    perf = transform.aggregate_route_performance(cleaned)
    assert perf["on_time_rate"].iloc[0] == pytest.approx(50.0)


def test_end_to_end_build_from_fixtures(tmp_path):
    """A full fixture -> ingest -> transform -> query smoke test."""
    from pipeline import fixtures

    fixtures.write_fixture_snapshot(seed=7)
    db = transform.build(db_path=str(tmp_path / "analytics.duckdb"))

    import duckdb

    con = duckdb.connect(db, read_only=True)
    try:
        n_preds = con.execute("SELECT count(*) FROM stop_predictions").fetchone()[0]
        n_routes = con.execute("SELECT count(*) FROM route_performance").fetchone()[0]
    finally:
        con.close()
    assert n_preds > 0
    assert n_routes > 0
