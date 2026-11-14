-- TruthTrace schema, version 2. ADDITIVE ONLY: CREATE ... IF NOT EXISTS.
-- One row per /api/analyze result: the verdict Person 2's backend returned for
-- an investigation. A cached investigation can have many analyses over time.
-- investigation_id has no foreign key on purpose: results with no saved trace
-- (no sources, or the trace failed) are still recorded.

CREATE TABLE IF NOT EXISTS tt_analyses (
    analysis_id       TEXT PRIMARY KEY,
    investigation_id  TEXT NOT NULL,
    claim             TEXT NOT NULL,
    status            TEXT NOT NULL
        CHECK (status IN ('supported', 'contradicted', 'misleading', 'insufficient_evidence')),
    explanation       TEXT NOT NULL,
    response          JSONB NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tt_analyses_investigation_idx
    ON tt_analyses (investigation_id, created_at DESC);
