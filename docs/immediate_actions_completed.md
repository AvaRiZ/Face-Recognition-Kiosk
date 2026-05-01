# Event Model Migration: Immediate Actions Complete

**Date:** May 1, 2026  
**Status:** ✅ All immediate actions completed and verified

---

## What Was Done

### 1. Query Migrations to recognition_events (Canonical)

**ml_analytics.py** — Both functions now query the canonical event table:
- `run_ml_analytics()` — Full ML pipeline (ARIMA, K-Means, Chi-square, ANOVA)
- `run_basic_analytics()` — Basic descriptive statistics

**rbac_routes.py** — Dashboard and logging views now use canonical events:
- `dashboard()` — Updated to query recognition_events for stats/activity
- `view_logs()` — Updated to show events from recognition_events

### 2. Query Updates Applied

**Before:** Queries used `recognition_log` (legacy)
```sql
FROM recognition_log r
JOIN users u ON r.user_id = u.user_id
```

**After:** Queries use `recognition_events` (canonical)
```sql
FROM recognition_events re
LEFT JOIN users u ON re.user_id = u.user_id
WHERE re.captured_at IS NOT NULL
```

**Key differences:**
- Column reference: `r.timestamp` → `re.captured_at` (when recognition occurred)
- Join type: `JOIN` → `LEFT JOIN` (handles NULL user_id from deleted users)
- NULL filter: Added `WHERE re.captured_at IS NOT NULL` (handles edge cases)

### 3. Deprecation Warnings Added

Functions now emit `DeprecationWarning` when called:
- Indicates migration to canonical event model
- References [docs/database_schema_policy.md](docs/database_schema_policy.md) for details
- Does not block execution; warnings are logged to stderr

**Example:**
```python
warnings.warn(
    "run_ml_analytics reads from recognition_events (canonical). "
    "See docs/database_schema_policy.md",
    DeprecationWarning,
    stacklevel=2
)
```

### 4. Verification Results

All query patterns validated against live database:
- ✅ 2 recognition events retrieved
- ✅ LEFT JOIN correctly handles NULL user_id from deleted users
- ✅ Date aggregation (DATE() function works with TIMESTAMPTZ)
- ✅ Recent events query returns expected rows
- ✅ No orphaned user references

---

## Affected Files

| File | Changes |
|------|---------|
| `routes/ml_analytics.py` | Migrated `run_ml_analytics()` and `run_basic_analytics()` queries; added deprecation warnings |
| `rbac_routes.py` | Migrated `dashboard()` and `view_logs()` queries; added deprecation warnings |
| `docs/database_schema_policy.md` | Already documents the canonical model (created in prior task) |

---

## Next Steps (Short-term)

1. **Test end-to-end workflows** - completed
   - Dashboard endpoint returned 200 and canonical dashboard payload keys
   - Analytics endpoint returned 200 and full ML payload
   - Export endpoint returned CSV output with canonical event data

2. **Monitor deprecation warnings** - completed
   - Captured warnings from dashboard analytics, get_stats, and ML analytics
   - Confirmed warnings are advisory only and do not block responses

3. **User deletion test** (enabled by FK policy fix) - completed
   - Deleted a disposable user in a rollback-safe transaction
   - Verified `recognition_events.user_id` became `NULL`
   - Verified `recognition_log.user_id` became `NULL`

4. **Document completion** - completed
   - Updated policy docs to reflect canonical `recognition_events`
   - Recorded verification results and migration status
   - Kept `recognition_log` as legacy compatibility only during transition

---

## Backward Compatibility

- **Dual-table support:** Both `recognition_events` and `recognition_log` remain active
- **Graceful fallback:** If recognition_events queries fail, legacy system can still fall back (though not recommended)
- **No data loss:** `recognition_log` retained for compliance/audit purposes
- **Deprecation period:** Analytics now use canonical model; legacy table can be archived later

---

## Verification Commands

```bash
# Run with warnings enabled to see deprecation notices
python -W default -c "from routes.ml_analytics import run_ml_analytics; run_ml_analytics('/path/to/db')"

# Check for remaining recognition_log queries
grep -r "recognition_log" routes/ --include="*.py" | grep -v "# " | head -20
```

---

## Summary

✅ **Canonical event model now active in analytics and RBAC**  
✅ **All queries validated and tested**  
✅ **Deprecation warnings in place for monitoring**  
✅ **FK policy allows user deletion (previously blocked)**  
✅ **Backwards compatible during transition period**

## Verified Results

- `GET /dashboard` -> 200
- `GET /api/dashboard` -> 200
- `GET /api/stats` -> 200
- `GET /api/events` -> 200
- `GET /api/analytics-reports` -> 200
- `GET /entry-exit-logs/export?date=<today>` -> 200 CSV
- Deprecation warnings captured from dashboard, stats, and ML analytics flows
- Disposable user deletion test passed in transaction rollback
