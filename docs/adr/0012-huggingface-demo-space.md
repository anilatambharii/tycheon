# 12. Public demo on Hugging Face Spaces

Date: 2026-11-04. Status: accepted.

## Context

Tycheon needs a place where people can try calibrated forecasts and risk figures without installing
anything. Hugging Face Spaces is where the forecasting-model community already looks. The
organisation's free tier has no CPU quota for a running (Docker) Space, and we do not add paid
infrastructure for a demo.

## Decision

* One **static** Space, `AmbhariiLabs/tycheon-demo`, built from `deploy/huggingface/`. A static
  Space needs no hardware.
* Every number on the page is precomputed by `build_static.py` with the real services (the same
  `/v1/forecast` and `/v1/risk` code paths) over a fixed grid: four bundled synthetic series, the
  random-walk, drift and GARCH models, a few horizons, calibrated and raw side by side. The page
  says so.
* Synthetic data only. Licensed or scraped market data is not redistributed and `yfinance` is off.
* There is no server, so there is nothing to rate limit and no upload surface. Tycheon Cloud (`ee/`)
  is never staged: `deploy.py` writes exactly three files (`README.md`, `index.html`, `data.json`)
  and refuses anything else.
* Deployment is a manual script with a token from the environment or a gitignored `.env`. Making
  the Space public is a manual step by a person.

## Consequences

* The demo is not interactive beyond the grid, and says nothing about real markets. Visitors who
  want their own data install the package.
* No foundation-model weights are downloaded or hosted; Kronos, TimesFM and Chronos stay out.
* If the organisation later gets hardware quota, a live Space can be added; the precomputed one
  stays useful as a fast landing page.
