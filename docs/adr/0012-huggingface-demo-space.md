# 12. Public demo on Hugging Face Spaces

Date: 2026-11-04. Status: accepted.

## Context

Tycheon needs a place where people can try calibrated forecasts and risk figures without installing
anything. Hugging Face Spaces is where the forecasting-model community already looks.

## Decision

* One Docker Space, `AmbhariiLabs/tycheon-demo`, built from `deploy/huggingface/`. It runs the
  open-source package only. Tycheon Cloud (`ee/`) is never staged: `deploy.py` refuses to.
* The real Tycheon API listens on loopback with a random per-boot key. The only public surface is a
  small front door (`app/proxy.py`) that serves one page and forwards a fixed allowlist: four
  synthetic series, the random-walk, drift and GARCH models, bounded horizons and sizes.
* Synthetic data only. Licensed or scraped market data is not redistributed and `yfinance` is off.
* Calls are rate limited per client and capped in concurrency. Uploads are not accepted.
* The wheel is built from the commit being deployed and shipped inside the Space, so the demo never
  runs a version that differs from the repository.
* Deployment is a manual script with a token from the environment. Making the Space public is a
  manual step by a person.

## Consequences

* The demo says nothing about real markets, and says so on the page.
* No foundation-model weights are downloaded or hosted; Kronos, TimesFM and Chronos stay out.
* The front door duplicates a few bounds that the API also enforces, on purpose: it is the part
  exposed to strangers.
