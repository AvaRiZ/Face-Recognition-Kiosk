# Event Model Policy

## Current Canonical Model (as of 2026-05-04)

- `recognition_events` is the only event table used for ingestion, analytics, and reporting.
- `users` keeps profile identity data.
- `user_embeddings` stores model vectors and uses `ON DELETE CASCADE`.
- `recognition_events.user_id` uses `ON DELETE SET NULL` so event history remains after profile deletion.

## Removed Legacy Layer

- The legacy compatibility event table was removed in migration `20260504_0011`.
- Any remaining legacy rows are backfilled into `recognition_events` before removal.
- API and repository code paths now read/write only canonical events.

## Operational Notes

1. Apply migrations with `alembic upgrade head` before starting services.
2. SQLite initialization backfills old legacy rows (if present) into canonical events, then drops the legacy table.
3. PostgreSQL migration `20260504_0011` performs the same backfill/drop flow.

## Verification Queries

```sql
-- Canonical table exists
SELECT to_regclass('public.recognition_events');

-- Legacy table removed
SELECT to_regclass('public.recognition_log');

-- Canonical policy marker
SELECT key, value
FROM app_settings
WHERE key = 'event_model_canonical';
```
