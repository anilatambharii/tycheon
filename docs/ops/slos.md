# Service level objectives

These are the objectives Tycheon Cloud is operated against, the indicators that measure them, and the
alerts that fire when an objective is at risk. The alert rules are in the Helm chart
([`prometheusrule.yaml`](https://github.com/anilatambharii/tycheon/blob/main/deploy/helm/tycheon/templates/prometheusrule.yaml));
`promtool check rules` passes on the rendered rules. **Nothing here has run against production
traffic**: the numbers are starting points to be tuned on real load, and the thresholds marked
*default* are not customer commitments.

For research and risk analytics. Not investment advice.

## Objectives

| SLO | Indicator (metric) | Objective | Window |
|---|---|---|---|
| Availability | share of requests not answered with a 5xx: `tycheon_http_requests_total{status_class="5xx"}` over all, excluding unmatched routes | 99.9% | 30 days |
| Forecast latency | `tycheon_http_request_duration_seconds` for `POST /v1/forecast` | 99% under 2.5 s | 30 days |
| Calibration quality (proxy) | share of served forecasts that are `calibrated` (`tycheon_forecast_calibration_total`); gap between nominal and holdout coverage (`tycheon_calibration_coverage_gap`) | at most 20% not calibrated; 90th-percentile coverage gap under 5 points | rolling 1-6 h |
| Fine-tune queue | `tycheon_finetune_oldest_queued_seconds` | no job waits more than 30 minutes | per job |
| Cost guardrail | GPUs allocated (kube-state-metrics), GPU utilisation (DCGM), metered traffic vs its 7-day norm | at most 4 GPUs; at least 10% utilisation when allocated; traffic under 5x the norm | rolling |

A 99.9% availability objective leaves 43 minutes of error budget per 30 days. The latency bound is
for the analytic call itself; a first call with a cold model cache can be slower (see the
[high-latency runbook](runbooks/high-latency.md)).

## Alerts

| Alert | Severity | Fires when | Runbook |
|---|---|---|---|
| `TycheonErrorBudgetFastBurn` | page | 5xx ratio above 14.4x the budget over 5 min **and** 1 h | [error budget burn](runbooks/error-budget-burn.md) |
| `TycheonErrorBudgetSlowBurn` | ticket | above 6x the budget over 30 min **and** 6 h | [error budget burn](runbooks/error-budget-burn.md) |
| `TycheonForecastLatencyHigh` | page | forecast p99 above 2.5 s for 15 min | [high latency](runbooks/high-latency.md) |
| `TycheonUncalibratedShareHigh` | ticket | more than 20% of forecasts not calibrated for 2 h | [calibration decay](runbooks/calibration-decay.md) |
| `TycheonCoverageGapHigh` | ticket | p90 coverage gap above 5 points for 1 h | [calibration decay](runbooks/calibration-decay.md) |
| `TycheonGpuIdle` | ticket | GPUs allocated but under 10% utilised for 1 h | [GPU under-utilised](runbooks/gpu-underutilised.md) |
| `TycheonGpuSaturated` | ticket | GPUs over 95% utilised for 2 h | [queue stuck](runbooks/queue-stuck.md) |
| `TycheonGpuCostAnomaly` | page | more than 4 GPUs allocated for 1 h | [cost anomaly](runbooks/cost-anomaly.md) |
| `TycheonTrafficAnomaly` | ticket | metered traffic over 5x its 7-day norm for 2 h | [cost anomaly](runbooks/cost-anomaly.md) |
| `TycheonFineTuneQueueStuck` | ticket | oldest queued job older than 30 min for 15 min | [queue stuck](runbooks/queue-stuck.md) |
| `TycheonDeploymentUnavailable` | page | under half the replicas of a Tycheon deployment available for 10 min | [high latency](runbooks/high-latency.md) |
| `TycheonPodCrashLooping` | ticket | more than 3 restarts in 30 min | [high latency](runbooks/high-latency.md) |
| `TycheonQuotaRefusalsSpike` | info | sustained quota refusals | [quota lockout](runbooks/quota-lockout.md) |

Paging alerts are the ones that mean customers are being hurt *now*, or money is being lost fast.
Everything else is a ticket, to be handled in working hours.

## What these metrics do and do not tell you

* **Calibration decay and forecast drift are only proxied.** The control plane sees what it served:
  how many forecasts were calibrated, and each interval's holdout coverage as measured when it was
  fitted. It does not see what the market then did, so it cannot measure true out-of-sample decay.
  That needs a scheduled evaluation against realised outcomes (the nightly benchmark is the natural
  home) publishing its coverage and CRPS as metrics. That job does not exist yet; until it does, treat
  the two quality alerts as early warnings, not measurements. The share of forecasts requested with
  `calibrate=false` also counts as "not calibrated", so a customer who opts out raises the ratio.
* **No tenant identifiers are exported.** Labels are route templates, status classes, meter kinds,
  model families and pool names; dedicated GPU pools are collapsed to one label. This is tested.
* **The open-source API server is not instrumented** with these metrics; in a deployment that runs
  it, measure it at the ingress (ALB/NGINX metrics) and with a blackbox probe on `/health`.
* **Database, cache and load-balancer health** come from the cloud provider's metrics (CloudWatch /
  Cloud Monitoring); the chart does not install exporters for them.
* The GPU alerts need the NVIDIA DCGM exporter and kube-state-metrics, which are not part of the chart.

## Error budget policy

* While the 30-day budget is more than half spent, feature releases to production require the
  on-call engineer's sign-off; reliability work comes first.
* When it is exhausted, only fixes for reliability, security and data integrity ship until it recovers.
* Every page produces a ticket; a page that needed no action is a defect in the alert and is fixed or
  removed within a week.
