# Data cleaning decisions

Real-time transit feeds are messy. Every rule below is a deliberate choice with
a tradeoff, written down so the numbers on the dashboard are defensible. This is
the document I would walk a customer through when they ask "can I trust this?".

## 1. Cancelled services are excluded, not counted as delayed

GTFS-Realtime keeps emitting `StopTimeUpdate`s for a trip after it is marked
`CANCELED` (`schedule_relationship == 3`). Those carry a stale predicted delay.

- **Decision:** drop them before computing on-time performance.
- **Why:** a cancelled train has no meaningful "delay". Counting its stale
  prediction as a 40-minute delay would punish a route for services that never
  ran, overstating lateness.
- **Tradeoff:** cancellations are themselves a reliability problem. A v2 would
  surface a separate "cancellation rate" metric rather than silently dropping
  them. Today we log the drop count so it is never invisible.

## 2. Implausible delays are clamped, not dropped

A small fraction of records report delays like 99,999 seconds (27+ hours),
almost always clock skew or a stuck record.

- **Decision:** clamp the magnitude to 90 minutes
  (`MAX_PLAUSIBLE_DELAY_SECONDS`) rather than dropping the row.
- **Why:** dropping would discard genuine major-disruption signal. A real
  90-minute delay during an incident is information an operator wants. Clamping
  keeps the row's "this is very late" signal while stopping one garbage value
  from dominating an average or a p90.
- **Tradeoff:** clamping compresses the tail. For incident forensics you would
  go back to the raw snapshot, which is preserved unmodified in `data/raw`.

## 3. Missing delays are dropped, never imputed to zero

Some `StopTimeUpdate`s carry neither an arrival nor a departure prediction.

- **Decision:** drop rows with a null delay.
- **Why:** imputing zero would invent on-time arrivals that were never
  predicted, inflating the on-time rate. Absence of data is not evidence of
  punctuality.

## 4. On-time is defined as within 5 minutes

- **Decision:** a stop counts as on time when `|delay| <= 300s`.
- **Why:** this matches the convention most transport agencies publish against,
  so the number is comparable to official figures rather than a bespoke metric
  nobody can benchmark.
- **Tradeoff:** 5 minutes is generous for a metro and strict for a regional
  service. The threshold is a single constant (`ON_TIME_THRESHOLD_SECONDS`) so
  it can be tuned per mode later.

## 5. Raw snapshots are immutable

`data/raw/*.parquet` is committed and never rewritten. The cleaned DuckDB file
is a derived artefact, gitignored, and rebuilt from raw on demand. You can
always re-clean with new rules; you can never un-clean a destroyed source.
