# Runbook: high latency, unavailable replicas, crash loops
(`TycheonForecastLatencyHigh`, `TycheonDeploymentUnavailable`, `TycheonPodCrashLooping`)

## Latency
1. **Which model?** Slow forecasts are almost always `kronos-mini` or a fine-tuned model:
   they run a neural model on CPU unless a GPU serves them. Compare the latency of requests for
   `random-walk` (milliseconds) with Kronos (seconds). The `tycheon_forecast_calibration_total`
   `model_family` label shows the mix.
2. **Cold model cache.** The first Kronos call on a new pod loads the weights from the model-cache
   volume (or downloads them if the cache is empty). Check the `model-prefetch` Job ran
   (`kubectl -n tycheon get jobs`) and that the PVC is mounted. Expect the first request per pod to be slow.
3. **CPU saturation.** `kubectl -n tycheon top pods`. Analytics are CPU-heavy; the gateway bounds
   concurrency per pod (4), so a saturated pod queues requests. Scale out (HPA) or raise CPU requests.
4. **Database latency.** Every call does a handful of small queries. Check RDS CPU, connections and
   slow queries; the metering reservation is a single-row upsert and should be sub-millisecond.
5. **One noisy tenant.** Per-organisation rate limits bound this, but only per replica. If one customer
   dominates, check `tycheon_usage_committed_total` by kind, and the access logs by API key prefix
   (never log keys). Lower that plan's rate limit or contact the customer.

## Unavailable replicas / crash loops
* `kubectl -n tycheon describe pod` events: `OOMKilled` (raise the limit), failed probes (the app is
  slow to start: the startup probe allows 5 minutes for the control plane), `FailedScheduling`
  (no node fits: GPU pods need the GPU pool and its toleration), `ImagePullBackOff`.
* Config errors show as an immediate exit with `configuration error: ...` in the logs
  (a missing or too-short secret).
* A node drain blocked by a PodDisruptionBudget: `kubectl get pdb -n tycheon`.

## If you cannot find it in 30 minutes
Roll back, then escalate per [incident response](../incident-response.md).
