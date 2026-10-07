-- Operational visibility for the fine-tune queue: how many jobs wait, run, and for how long the
-- oldest has waited, per worker pool. Counts only: no tenant identifiers leave the database.
CREATE FUNCTION cp_finetune_queue_stats()
RETURNS TABLE (gpu_pool text, queued bigint, running bigint, oldest_queued_seconds double precision)
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT gpu_pool,
         count(*) FILTER (WHERE status = 'queued'),
         count(*) FILTER (WHERE status = 'running'),
         coalesce(extract(epoch FROM now() - min(created_at) FILTER (WHERE status = 'queued')), 0)::double precision
    FROM finetune_jobs
   GROUP BY gpu_pool
$$;
REVOKE ALL ON FUNCTION cp_finetune_queue_stats FROM PUBLIC;
GRANT EXECUTE ON FUNCTION cp_finetune_queue_stats TO {app_role};
