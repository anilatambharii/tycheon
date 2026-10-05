# Model cards

Every model Tycheon can route to gets a card, and every forecast references the
cards of the models that contributed to it. No card, no routing.

## What a card must state

| Section | Contents |
|---|---|
| Identity | Name, version, upstream source and licence, weights provenance. |
| Training data | What it was trained on, its period, and what it has never seen. |
| Intended use | The horizons, frequencies and asset classes it is meant for. |
| Out-of-scope use | Where it is known to mislead. |
| Calibration | Target coverage, realised coverage, the window measured, and the conformal method used. |
| Baseline comparison | Performance against the random walk, with the Diebold-Mariano statistic. |
| Known failure modes | Regimes, liquidity conditions and data gaps that degrade it. |
| Cost and latency | Inference cost, latency and hardware assumptions. |

## Status

No cards yet. Cards land with the model adapters in Phase T1, one per model:
Kronos, TimesFM, Chronos, and each baseline.

A card is a claim we can be held to. It states measured numbers with the window
they were measured on, never a marketing figure.
