-- Bounded recent-history query. Complete result/context remains in response JSONB.
CREATE INDEX IF NOT EXISTS tt_analyses_recent_idx
    ON tt_analyses (created_at DESC, analysis_id DESC);
