# Press kit

*For research and risk analytics. Not investment advice.*

## One line

Tycheon is an open-source calibrated forecasting and risk layer for financial time series. Kronos forecasts the path; Tycheon tells you how much to trust it.

## 100 words

Tycheon is an Apache-2.0 Python library that sits on top of time-series forecasting models such as Kronos, TimesFM and Chronos. It enforces point-in-time data access, calibrates forecast intervals with conformal prediction and earns a "calibrated" status only on a holdout, turns sample paths into Value at Risk, Expected Shortfall and drawdown probabilities, and scores every model against a mandatory random-walk baseline with Diebold-Mariano tests. Its first published benchmark, on synthetic data, found that no model beat the random walk, and the project published that result. Tycheon is built by Ambharii Labs in New York.

## Three facts

1. On its first published benchmark (3 synthetic series, 108 forecast origins), none of the 6 non-baseline models beat the random walk with statistical confidence.
2. Zero-shot Kronos-mini's nominal 90% interval contained 57.4% of outcomes on that benchmark. It ran with 20 sample paths against 100 for the other models, and the data is synthetic.
3. After a check where two-sided calibration cut a 5-day 95% VaR from 5.38% to 3.93% on one stock, Tycheon made risk calibration widen-only.

## What it is not

Not a trading signal service. Not investment advice. No live execution. The published evidence so far is synthetic and says nothing about real markets.

## Links

- Code: <https://github.com/anilatambharii/tycheon>
- Paper (Zenodo): <https://doi.org/10.5281/zenodo.24045160>
- Paper (SSRN): <https://doi.org/10.2139/ssrn.7689081>
- Software DOI: <https://doi.org/10.5281/zenodo.24041298>
- Docs: <https://anilatambharii.github.io/tycheon/>
- PyPI: <https://pypi.org/project/tycheon/>
- Demo on synthetic data: <https://huggingface.co/spaces/AmbhariiLabs/tycheon-demo>
- Ambharii Labs: <https://ambharii.com>

Tycheon builds on Kronos ([shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos), MIT licence).

## Founder

Anil Prasad, founder and Chief AI Architect, Ambharii Labs. Software, data and AI engineering leader in healthcare, energy and finance. IEEE member.
<https://anilsprasad.com> | ORCID: <https://orcid.org/0009-0008-0693-4003>

## Images

Free to use when writing about Tycheon. Please keep the synthetic-data caveat next to any chart.

| Image | Download |
|---|---|
| Logo mark (1024 x 1024) | [tycheon_logo_mark_1024.png](assets/press/tycheon_logo_mark_1024.png){: download="tycheon_logo_mark_1024.png" } |
| Logo, horizontal, for dark backgrounds | [tycheon_logo_horizontal_dark.png](assets/press/tycheon_logo_horizontal_dark.png){: download="tycheon_logo_horizontal_dark.png" } |
| Logo, horizontal, for light backgrounds | [tycheon_logo_horizontal_light.png](assets/press/tycheon_logo_horizontal_light.png){: download="tycheon_logo_horizontal_light.png" } |
| Founder headshot (800 x 800) | [headshot_circle_800.png](assets/press/headshot_circle_800.png){: download="headshot_circle_800.png" } |
| Architecture diagram | [architecture_dark_1920x1080.png](assets/press/architecture_dark_1920x1080.png){: download="architecture_dark_1920x1080.png" } |
| Interval coverage chart (synthetic data) | [chart_coverage_dark_1920x1080.png](assets/press/chart_coverage_dark_1920x1080.png){: download="chart_coverage_dark_1920x1080.png" } |
| VaR before and after widen-only (one stock, one window) | [chart_var_fix_dark_1920x1080.png](assets/press/chart_var_fix_dark_1920x1080.png){: download="chart_var_fix_dark_1920x1080.png" } |

## Contact

anil@ambharii.com

*For research and risk analytics. Not investment advice.*
