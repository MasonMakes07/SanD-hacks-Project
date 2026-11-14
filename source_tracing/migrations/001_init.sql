-- TruthTrace Person 3 schema, version 1. ADDITIVE ONLY: every statement is
-- CREATE ... IF NOT EXISTS. Nothing here drops, truncates or alters data, and
-- all tables are prefixed tt_ so they cannot collide with other tables.
-- Apply with a DIRECT (non-pooled) Neon connection: see store.apply_migrations.

CREATE TABLE IF NOT EXISTS tt_investigations (
    investigation_id    TEXT PRIMARY KEY,
    cache_key           TEXT NOT NULL,
    claim               TEXT NOT NULL,
    normalized_claim    TEXT NOT NULL,
    schema_version      INTEGER NOT NULL,
    algorithm_version   INTEGER NOT NULL,
    earliest_source_id  TEXT,
    -- When the evidence was gathered from the web (set once, never rewritten
    -- on cache hits). Not a publication time.
    retrieved_at        TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    meta                JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS tt_investigations_cache_idx
    ON tt_investigations (cache_key, retrieved_at DESC);

CREATE TABLE IF NOT EXISTS tt_sources (
    investigation_id  TEXT NOT NULL REFERENCES tt_investigations (investigation_id) ON DELETE CASCADE,
    source_id         TEXT NOT NULL,
    position          INTEGER NOT NULL,
    title             TEXT NOT NULL,
    url               TEXT NOT NULL,
    published_date    DATE,            -- NULL = no reliable publication date
    excerpt           TEXT NOT NULL,
    PRIMARY KEY (investigation_id, source_id),
    -- The same page cannot be stored twice for one investigation.
    UNIQUE (investigation_id, url)
);

CREATE TABLE IF NOT EXISTS tt_edges (
    investigation_id   TEXT NOT NULL,
    source_id          TEXT NOT NULL,
    target_id          TEXT NOT NULL,
    relationship_type  TEXT NOT NULL
        CHECK (relationship_type IN ('explicit_citation', 'similar_wording', 'same_fact_check')),
    position           INTEGER NOT NULL,
    evidence           JSONB NOT NULL,
    PRIMARY KEY (investigation_id, source_id, target_id, relationship_type),
    FOREIGN KEY (investigation_id, source_id) REFERENCES tt_sources (investigation_id, source_id) ON DELETE CASCADE,
    FOREIGN KEY (investigation_id, target_id) REFERENCES tt_sources (investigation_id, source_id) ON DELETE CASCADE
);
