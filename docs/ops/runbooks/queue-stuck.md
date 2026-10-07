# Runbook: fine-tune queue stuck or GPUs saturated
(`TycheonFineTuneQueueStuck`, `TycheonGpuSaturated`)

**Meaning:** a customer's fine-tune job has been waiting too long, or the GPUs are fully busy.

1. **Is any worker alive in that pool?** The alert's `pool` label is `shared`, or `dedicated` for
   Enterprise customers' own pools. `kubectl -n tycheon get pods -l app.kubernetes.io/component=worker-gpu`
   (and `worker-cpu`). No pod for the pool means nobody will ever claim the job: scale it up, or for
   a dedicated pool check that a worker was started with the right `--pool dedicated-<org>`.
2. **Is the worker running but not claiming?** Logs: it polls the queue every few seconds. A
   database error here (permissions, connection) stops it.
3. **Is a job hung?** A `running` job whose worker died stays `running` (there is no lease or heartbeat
   yet: a known gap). Find it: running jobs older than their expected time, with no matching pod. Cancel it
   from the customer's side (`POST /v1/finetune/jobs/<id>/cancel`) or, as an operator, set its status
   to `failed` in the database with an error message, so the customer is not billed for a dead run.
4. **Capacity.** If every GPU is genuinely busy, queueing is correct. Add GPU nodes (raise the node
   group's maximum in Terraform) or tell the customer the expected wait.
5. **Quotas.** Cloud GPU quotas are a common hidden limit: check the account's GPU vCPU quota.

**Never** delete a job row to "clear" the queue: the customer's usage and audit trail depend on it.
