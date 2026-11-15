-- Extraction time is distinct from database insertion time. Legacy stays unknown.
ALTER TABLE tt_video_claims ADD COLUMN IF NOT EXISTS extracted_at TIMESTAMPTZ;
