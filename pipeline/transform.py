"""Transform layer: turn raw snapshots into a clean analytics table.

This is where judgement lives. Raw feeds are noisy: cancelled trips keep
emitting stale predictions, a handful of records claim two-hour delays from
clock skew, some stops arrive with no sequence number. The cleaning rules here
are explicit and documented (see docs/DATA_DECISIONS.md) because in an FDE
setting the *defensibility* of a number matters as much as the number.

Output is a DuckDB database with two tables the dashboard reads:
- ``stop_predictions``: cleaned, one row per stop-time-update.
- ``route_performance``: aggregated on-time performance per route per snapshot.
"""

from __future__ import annotations

import glob

import duckdb
import pandas as pd

from . import config


def load_raw() -> pd.DataFrame:
    """Concatenate every snapshot Parquet in data/raw into one frame."""
    paths = sorted(glob.glob(str(config.RAW_DIR / "*.parquet")))
    if not paths:
        raise FileNotFoundError(
            f"No snapshots in {config.RAW_DIR}. Run the ingester first, e.g.\n"
            "  USE_FIXTURE=1 python -m pipeline.ingest"
        )
    frames = [pd.read_parquet(p) for p in paths]
    return pd.concat(frames, ignore_index=True)


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the documented cleaning rules and return a tidy frame.

    Each rule is a named, reversible filter rather than a silent mutation so the
    drop counts can be logged. Silent truncation is how dashboards end up lying.
    """
    start_n = len(df)

    # Rule 1: drop cancelled services. schedule_relationship == 3 (CANCELED)
    # still emits predictions, but a cancelled train's "delay" is meaningless.
    canceled = df["schedule_relationship"] == 3
    df = df[~canceled]

    # Rule 2: a missing delay is not a zero delay. Drop null delays rather than
    # imputing, which would invent on-time performance that never happened.
    null_delay = df["delay_s"].isna()
    df = df[~null_delay]

    # Rule 3: clamp implausible delays instead of dropping them. A real but
    # extreme delay (major disruption) is signal; a 99999s value is noise. We
    # clamp to the plausible ceiling so disruptions still register without one
    # garbage row dominating an average.
    ceiling = config.MAX_PLAUSIBLE_DELAY_SECONDS
    clamped = df["delay_s"].abs() > ceiling
    df = df.copy()
    df.loc[clamped, "delay_s"] = (
        df.loc[clamped, "delay_s"].clip(lower=-ceiling, upper=ceiling)
    )

    # Derived columns the dashboard needs.
    df["delay_min"] = df["delay_s"] / 60.0
    df["on_time"] = df["delay_s"].abs() <= config.ON_TIME_THRESHOLD_SECONDS

    print(
        f"[transform] cleaned {start_n} -> {len(df)} rows "
        f"(dropped {canceled.sum()} cancelled, {null_delay.sum()} null-delay; "
        f"clamped {int(clamped.sum())} outliers)"
    )
    return df


def aggregate_route_performance(df: pd.DataFrame) -> pd.DataFrame:
    """On-time performance and delay distribution per feed+route+snapshot."""
    grouped = df.groupby(["feed", "route_id", "fetched_at"], dropna=False)
    perf = grouped.agg(
        predictions=("delay_s", "size"),
        on_time_rate=("on_time", "mean"),
        median_delay_min=("delay_min", "median"),
        p90_delay_min=("delay_min", lambda s: s.quantile(0.9)),
        worst_delay_min=("delay_min", "max"),
    ).reset_index()
    perf["on_time_rate"] = (perf["on_time_rate"] * 100).round(1)
    perf = perf.round(2)
    return perf.sort_values("on_time_rate")


def build(db_path: str | None = None) -> str:
    """Run the full transform and write the analytics DuckDB file."""
    target = db_path or str(config.ANALYTICS_DB)
    raw = load_raw()
    clean_df = clean(raw)
    perf = aggregate_route_performance(clean_df)

    con = duckdb.connect(target)
    try:
        con.execute("CREATE OR REPLACE TABLE stop_predictions AS SELECT * FROM clean_df")
        con.execute("CREATE OR REPLACE TABLE route_performance AS SELECT * FROM perf")
    finally:
        con.close()

    print(f"[transform] wrote {len(clean_df)} predictions, "
          f"{len(perf)} route rows -> {target}")
    return target


if __name__ == "__main__":
    build()
