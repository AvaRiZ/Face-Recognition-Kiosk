# Event Model Policy & Database Fixes

## Applied Fixes (Alembic Migration 20260501_0003)

### 1. Timestamp Standardization ✅
**Issue:** `user_embeddings.created_at` was `TIMESTAMP` (no timezone) while all other temporal columns were `TIMESTAMPTZ`.

**Fix:** Converted to `TIMESTAMPTZ` for timezone consistency.

**Impact:** 
- Eliminates silent bugs in time-sensitive event logging and analytics
- Ensures all timestamps use same timezone semantics (Asia/Kuala_Lumpur configured at database level)
- Backwards compatible: existing timestamps interpreted as UTC and stored with timezone info

---

### 2. Recognition Log Foreign Key Delete Policy ✅
**Issue:** `recognition_log.user_id` FK had `ON DELETE NO ACTION`, blocking user deletion even when legitimate archival/deletion workflows needed to proceed.

**Previous behavior:** Deleting a user with recognition log entries would fail.

**Fix:** Changed to `ON DELETE SET NULL` (consistent with `recognition_events` which uses the same pattern).

**Impact:**
- Users can now be archived/deleted without blocking on recognition logs
- Audit trail preserved (log entries remain with `user_id = NULL`)
- Reflects the intent: preserve recognition event history independent of user lifecycle

**Policy decision:**
- `recognition_events`: uses `ON DELETE SET NULL` → events remain even after user deleted
- `recognition_log`: now uses `ON DELETE SET NULL` (was `NO ACTION`) → same behavior
- `user_embeddings`: uses `ON DELETE CASCADE` → tightly coupled to user lifecycle; old embeddings should not persist

---

### 3. Event Model Policy Decision ✅
**Issue:** Two event tables existed (`recognition_events` and `recognition_log`) with unclear source-of-truth.

**Decision:** **recognition_events is canonical; recognition_log is legacy compatibility layer only.**

**Documented in `app_settings`:**
```sql
SELECT * FROM app_settings WHERE key LIKE 'event_model%';
-- event_model_canonical: recognition_events
-- event_model_legacy: recognition_log_compatibility_layer_only
```

### Why This Decision

| Aspect | recognition_events | recognition_log |
|--------|-------------------|-----------------|
| Created | Alembic 0001 (first migration) | Alembic 0002 (hardening) |
| Purpose | Canonical event ingestion (full payload) | Legacy compatibility / analytics |
| Columns | event_id (UNIQUE), decision, comprehensive scores, payload_json, station_id, sr_code | Basic: user_id, confidence, method, scores |
| FK delete policy | SET NULL (event preserved if user deleted) | SET NULL (fixed in 0003) |
| Planned use | Worker → API deduplication, new events | Gradual deprecation, existing dashboards |
| Row count | 2 (fresh data) | 40 (historical + test data) |

### Recommended Transition

1. **Immediate (now):** Both tables remain active
   - New recognition events written to `recognition_events` via API contract
   - Existing dashboards continue querying `recognition_log`
   - Migration 0003 ensures consistent FK behavior

2. **Short-term (next sprint):** Deprecation warnings
   - Add deprecation notice to `recognition_log` analytics endpoints
   - Document migration path for existing queries
   - Dashboard should move to reading from `recognition_events`

3. **Medium-term (after validation):** Optional cleanup
   - Archive or remove `recognition_log` if no compliance/audit requirements mandate its retention
   - Redirect all queries to `recognition_events` view/queries
   - Update any RBAC or reporting that still references the old table

### Migration Commands

**To apply this fix:**
```bash
alembic upgrade head
```

**To verify:**
```sql
-- Check timestamp type
SELECT data_type 
FROM information_schema.columns 
WHERE table_name='user_embeddings' AND column_name='created_at';
-- Expected: "timestamp with time zone"

-- Check FK policy
SELECT rc.delete_rule
FROM information_schema.table_constraints tc
JOIN information_schema.referential_constraints rc
  ON tc.constraint_name = rc.constraint_name
WHERE tc.table_name='recognition_log' 
  AND tc.constraint_name='recognition_log_user_id_fkey';
-- Expected: SET NULL

-- Check event model policy
SELECT * FROM app_settings WHERE key LIKE 'event_model%';
```

---

## Summary of Changes

| Change | Before | After | Alembic |
|--------|--------|-------|---------|
| user_embeddings.created_at type | TIMESTAMP | TIMESTAMPTZ | 20260501_0003 |
| recognition_log.user_id FK delete | NO ACTION | SET NULL | 20260501_0003 |
| Event model canonical source | Unclear | recognition_events | 20260501_0003 |
| Event model legacy status | Unclear | compatibility layer | 20260501_0003 |

---

## Code Implications

**Services/API Layer:**
- `services/recognition_service.py`: Continue writing to `recognition_events` via API
- `routes/ml_analytics.py`: Uses `recognition_events` for analytics
- `routes/routes.py`: Dashboard, stats, exports, and event reporting now read from `recognition_events`

**User Management:**
- `database/repository.py`: User deletion/archival now succeeds (no FK block on recognition_log)
- No code changes required; FK constraint allows cascading by design

**Testing:**
- Existing tests should pass; behavior is additive (enables user deletion that was previously blocked)
- No type-casting issues from TIMESTAMP→TIMESTAMPTZ (automatic)

---

## Verification Checklist

- [x] Migration created and applied (20260501_0003)
- [x] user_embeddings.created_at is TIMESTAMPTZ
- [x] recognition_log.user_id FK is SET NULL
- [x] event_model_canonical and event_model_legacy in app_settings
- [x] Alembic version advanced to 20260501_0003
