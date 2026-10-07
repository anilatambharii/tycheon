# Runbook: cost anomaly (`TycheonGpuCostAnomaly`, `TycheonTrafficAnomaly`)

**Meaning:** more GPUs are allocated than the guardrail allows, or metered traffic is far above
its 7-day norm. Either costs money, and the second can also be abuse or a runaway client.

## GPU count
1. `kubectl get nodes -l tycheon.io/pool=gpu` and `kubectl -n tycheon get pods -o wide | grep worker-gpu`.
2. Why so many? A queue burst that the autoscaler answered (legitimate: then raise the guardrail
   deliberately), a scale-up loop (check the autoscaler events), or pods that did not terminate.
3. Cap it now: lower the GPU node group's maximum and `workers.gpu.replicas`, then redeploy.

## Traffic
1. Which meter? `sum by (kind) (rate(tycheon_usage_committed_total[1h]))`.
2. Which customer? The metrics deliberately carry no tenant. Use the control-plane's own usage
   tables (an operator with database access): top organisations by `usage_events` in the last hours.
3. **Legitimate growth** (a customer onboarding): nothing to do but watch capacity.
4. **A runaway client** (a retry loop): contact the customer; their rate limit already bounds the
   harm per replica. Their quota is a hard limit, so they cannot spend beyond their plan.
5. **Abuse** (a leaked key): revoke the key (`DELETE /v1/api-keys/<id>` as the owner, or in the
   database as an operator with an audit entry) and follow [credentials leak](credentials-leak.md).

Also set a cloud-provider budget alert / cost anomaly detection on the account (AWS Cost Anomaly
Detection, GCP Budgets): the metrics here see only what Tycheon meters, not the whole bill.
