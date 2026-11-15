-- TruthTrace schema, version 3. ADDITIVE ONLY: CREATE ... IF NOT EXISTS.
-- Cache of claims Gemini extracted from YouTube clips, so checking the same moment
-- of a video again skips the video model. A clip is identified by the video id and
-- its bucketed window; prompt_version invalidates rows when the prompt changes.

CREATE TABLE IF NOT EXISTS tt_video_claims (
    video_id           TEXT NOT NULL,
    window_start       INTEGER NOT NULL CHECK (window_start >= 0),
    window_end         INTEGER NOT NULL CHECK (window_end > window_start),
    prompt_version     INTEGER NOT NULL,
    checkable          BOOLEAN NOT NULL,
    claim              TEXT NOT NULL,
    timestamp_seconds  INTEGER,
    quote              TEXT NOT NULL,
    speaker            TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (video_id, window_start, window_end, prompt_version)
);
