# Runbook: GPUs idle (`TycheonGpuIdle`)

**Meaning:** GPU nodes are allocated to Tycheon pods, but average utilisation has been under the
threshold for an hour. GPUs are the most expensive thing in the platform: idle ones are waste.

1. **Is a job running?** `kubectl -n tycheon get pods -l app.kubernetes.io/component=worker-gpu`
   and the fine-tune jobs through the control plane. A job in its *evaluation* phase (walk-forward
   scoring) uses the GPU lightly; a job loading data uses it not at all.
2. **Is the worker waiting?** A GPU worker with an empty queue holds its GPU while it polls. If the
   queue is empty most of the time, scale the GPU worker Deployment to zero and let the node group
   scale to zero (`workers.gpu.replicas: 0`), or move to job-per-run workers.
3. **Is the node group not scaling down?** Check the cluster autoscaler logs and PodDisruptionBudgets /
   `safe-to-evict` annotations pinning the node.
4. **Is the metric wrong?** The alert needs the DCGM exporter. If DCGM is down the expression is empty
   and the alert will not fire, so also look at the exporter's own health.

**Cost:** one GPU node-hour is billed whether or not it is used. Record what you changed and the
estimated saving.
