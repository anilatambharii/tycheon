---
title: Tycheon Demo
emoji: 📉
colorFrom: blue
colorTo: gray
sdk: static
pinned: false
license: apache-2.0
short_description: Forecast uncertainty and risk, with honest baselines
---

# Tycheon demo

Tycheon is open-source calibrated forecasting and risk analytics: conformal prediction intervals,
VaR and Expected Shortfall, and a random-walk baseline with Diebold-Mariano tests on every
evaluation. Kronos forecasts the path; Tycheon tells you how much to trust it.

This Space is a static page of **precomputed results**. They were produced by running the
open-source package on four **synthetic** price series with the cheap baseline models (random
walk, drift, GARCH), calibrated and raw side by side. The series are generated, so nothing here
says anything about real markets.

To run it on your own data: `pip install tycheon`.

**For research and risk analytics. Not investment advice.**

- Source and docs: https://github.com/anilatambharii/tycheon
- License: Apache-2.0
