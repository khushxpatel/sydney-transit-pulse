"""Streamlit dashboard: the customer-facing surface of the pipeline.

An FDE's pipeline is only as valuable as the decision it enables. This view
answers one operational question: "which Sydney routes are running late right
now, and how badly?" It reads the analytics DuckDB built by the transform layer
and never touches the network itself, so it loads fast and deploys anywhere.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

# Make the pipeline package importable when Streamlit runs this file directly.
sys.path.append(str(Path(__file__).resolve().parent.parent))
from pipeline import config  # noqa: E402

st.set_page_config(page_title="Sydney Transit Pulse", page_icon="🚆", layout="wide")


@st.cache_data(ttl=60)
def load_tables() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load the two analytics tables. Cached so reruns are instant."""
    if not config.ANALYTICS_DB.exists():
        return pd.DataFrame(), pd.DataFrame()
    con = duckdb.connect(str(config.ANALYTICS_DB), read_only=True)
    try:
        preds = con.execute("SELECT * FROM stop_predictions").df()
        perf = con.execute("SELECT * FROM route_performance").df()
    finally:
        con.close()
    return preds, perf


def main() -> None:
    st.title("🚆 Sydney Transit Pulse")
    st.caption(
        "On-time performance across Sydney's public transport network, built "
        "from Transport for NSW real-time GTFS feeds. Data is scraped on a "
        "schedule and version-controlled, so the history is reproducible."
    )

    preds, perf = load_tables()
    if perf.empty:
        st.warning(
            "No analytics database found yet. Build one with:\n\n"
            "```\nUSE_FIXTURE=1 python -m pipeline.ingest\n"
            "python -m pipeline.transform\n```"
        )
        st.stop()

    is_synthetic = perf["feed"].str.contains("synthetic").any()
    if is_synthetic:
        st.info(
            "Showing **synthetic sample data** (no API key configured). Set "
            "`TFNSW_API_KEY` and re-run the pipeline for live Sydney data."
        )

    feeds = sorted(perf["feed"].unique())
    chosen = st.multiselect("Modes", feeds, default=feeds)
    view = perf[perf["feed"].isin(chosen)]

    # Headline metrics: the numbers an operations lead would glance at first.
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Routes tracked", view["route_id"].nunique())
    c2.metric("Predictions", f"{int(view['predictions'].sum()):,}")
    c3.metric("Avg on-time", f"{view['on_time_rate'].mean():.1f}%")
    c4.metric("Worst delay", f"{view['worst_delay_min'].max():.0f} min")

    st.subheader("On-time performance by route")
    st.caption("Lower bars = more delays. On-time means within 5 minutes.")
    chart = view.set_index("route_id")["on_time_rate"].sort_values()
    st.bar_chart(chart)

    st.subheader("Route detail")
    st.caption("Sorted worst-first, the triage order an operator would use.")
    st.dataframe(
        view[
            [
                "feed",
                "route_id",
                "predictions",
                "on_time_rate",
                "median_delay_min",
                "p90_delay_min",
                "worst_delay_min",
            ]
        ].rename(
            columns={
                "on_time_rate": "on_time_%",
                "median_delay_min": "median_delay_min",
                "p90_delay_min": "p90_delay_min",
                "worst_delay_min": "worst_delay_min",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("How this is built"):
        st.markdown(
            "1. A GitHub Action polls Transport for NSW GTFS-Realtime feeds and "
            "commits each snapshot to `data/raw` (the *git scraping* pattern).\n"
            "2. `pipeline.transform` cleans the snapshots (drops cancelled "
            "services, clamps clock-skew outliers) and writes a DuckDB file.\n"
            "3. This dashboard reads that DuckDB file. No live network calls, so "
            "it deploys to any static-ish host and loads instantly."
        )


if __name__ == "__main__":
    main()
