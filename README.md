# 🚆 Sydney Transit Pulse

**An end-to-end data pipeline that turns Transport for NSW's noisy real-time
feeds into a live on-time-performance dashboard for Sydney's transport network.**

Ingests messy real-world data on a schedule, cleans it with documented and
tested rules, and presents the result as a decision-ready view. Built to mirror
the day-to-day shape of forward-deployed data work: ugly source data in, a
trustworthy product out.

> Live dashboard: _deploy link goes here_ · Built by [@khushxpatel](https://github.com/khushxpatel)

---

## Why this exists

Real-time transit feeds look simple and aren't. Cancelled trains keep emitting
predictions, clock skew produces 27-hour "delays", and some records have no
timing at all. The interesting engineering is not fetching the data, it is
making the resulting numbers *defensible*. That judgement is documented in
[`docs/DATA_DECISIONS.md`](docs/DATA_DECISIONS.md).

## Architecture

```
                Transport for NSW
              GTFS-Realtime feeds (protobuf)
            trains · buses · ferries · light rail
                        │
                        ▼
   ┌───────────────────────────────────────────────┐
   │  GitHub Actions scraper  (every 15 min, cron)  │   pipeline/ingest.py
   │  fetch → parse protobuf → flatten → Parquet     │
   │  → git commit snapshot   (the "git scraping"    │
   │     pattern: repo = reproducible history)       │
   └───────────────────────────────────────────────┘
                        │  data/raw/*.parquet (immutable)
                        ▼
   ┌───────────────────────────────────────────────┐
   │  Transform  (clean + aggregate)                │   pipeline/transform.py
   │  drop cancelled · clamp outliers · null-safe    │
   │  → DuckDB: stop_predictions, route_performance  │
   └───────────────────────────────────────────────┘
                        │  data/analytics.duckdb (derived)
                        ▼
   ┌───────────────────────────────────────────────┐
   │  Streamlit dashboard  (no live network calls)  │   dashboard/app.py
   │  on-time % per route · worst-first triage table │
   └───────────────────────────────────────────────┘
```

**Why git scraping?** The GitHub Action commits every snapshot back to the repo,
so the data history is reproducible, free to host, and needs no database server.
The dashboard reads a pre-built DuckDB file, so it loads instantly and deploys
anywhere.

## Run it in 60 seconds (no API key needed)

```bash
pip install -r requirements.txt

# 1. Generate a synthetic snapshot (includes realistic dirty records)
USE_FIXTURE=1 python -m pipeline.ingest

# 2. Clean + aggregate into the analytics DB
python -m pipeline.transform

# 3. Launch the dashboard
streamlit run dashboard/app.py
```

## Run it on live Sydney data

1. Create a free app at <https://opendata.transport.nsw.gov.au> and copy the API
   key.
2. Export it and run the real ingester:

   ```bash
   export TFNSW_API_KEY=your_key_here
   python -m pipeline.ingest      # pulls live trains/buses/ferries/light rail
   python -m pipeline.transform
   streamlit run dashboard/app.py
   ```

3. To run the scraper continuously, add `TFNSW_API_KEY` as a GitHub Actions
   secret. The workflow in `.github/workflows/scrape.yml` then commits a live
   snapshot every 15 minutes automatically.

## Tests

```bash
python -m pytest -q
```

The suite covers each cleaning rule (cancelled-service exclusion, outlier
clamping, null-delay handling, the on-time threshold) plus a full
fixture → ingest → transform → query smoke test. CI runs it on every push.

## Project layout

| Path | Responsibility |
|------|----------------|
| `pipeline/config.py` | All tunables and paths; env-overridable |
| `pipeline/ingest.py` | The only module that touches the network |
| `pipeline/fixtures.py` | Synthetic data so the repo runs credential-free |
| `pipeline/transform.py` | Cleaning rules + aggregation → DuckDB |
| `dashboard/app.py` | Streamlit decision view |
| `docs/DATA_DECISIONS.md` | Why each cleaning rule exists, with tradeoffs |
| `.github/workflows/` | Scheduled scraper + CI |

## Design choices worth calling out

- **Resilient ingestion.** Each feed retries with exponential backoff; one feed
  failing never aborts the others or the scheduler.
- **Immutable raw, derived analytics.** Snapshots are committed and never
  rewritten; the DuckDB file is gitignored and rebuilt on demand.
- **No silent truncation.** Every cleaning step logs how many rows it dropped or
  clamped, because a dashboard that quietly discards data is a dashboard that
  lies.

## Roadmap

- Cancellation-rate metric (currently cancelled trips are excluded, not scored)
- Join static GTFS for human-readable stop and route names
- Per-mode on-time thresholds
- Historical trend view across committed snapshots

---

_Data © Transport for NSW, used under the Open Data licence. Synthetic sample
data is clearly labelled and never presented as real._
