# Library Facial Recognition System Architecture â€“ Dual-Camera Occupancy Model

## 1) Project Objective

Implement a comprehensive facial recognition system for library access control that:

- Identifies and tracks users across entry and exit points in real-time.
- Maintains accurate occupancy counts and capacity monitoring with librarian alerts.
- Automatically registers enrolled students and flags unrecognized individuals for registration.
- Provides comprehensive audit trails and analytics for user flow patterns.
- Serves a librarian dashboard for real-time occupancy and capacity management.

## 2) Scope and Design Assumptions

- **Hardware Setup**: Single laptop device with two USB webcameras (Camera 1 for entry, Camera 2 for exit).
- **One Backend Host**: The single laptop serves as the unified application host for integrated API and recognition processing.
- **Runtime Stack**: Host runs `web-api`, `entry-worker`, and `exit-worker` for dual-camera capture and event handling.
- **Single Persistent Datastore**: PostgreSQL (local or LAN-accessible) as the sole authoritative storage.
- **Real-Time Occupancy**: Calculated as: (entry events today) âˆ’ (exit events today) by recognition timestamp.
- **Display & Control**: Librarian dashboard accessible via browser on same laptop or connected admin workstation.
- **Librarian Display**: Separate browser tab or connected tablet/monitor shows real-time occupancy, capacity status, and alerts.
- **Network Model**: LAN-first; external internet not required for normal operation.

---

## 3) Final System Architecture

### System Diagram

```text
+------- Library Local Network (LAN) -------+
|                                          |
|  Admin/Staff                              |
|  Web Browser                              |
|      |                                    |
|      |---- HTTP/HTTPS ----+               |
|                           |               |
|                           v               |
|  Librarian Kiosk-Entry    [Web API +      |
|  (WebSocket conn)         Dashboard]      |
|      |                    |               |
|      |                    | Internal API  |
|      +----WebSocket-------+               |
|                           |               |
|  Librarian Kiosk-Exit     |               |
|  (WebSocket conn)         |               |
|      |                    |               |
|      +----WebSocket-------+               |
|                                          |
+----------|--Camera-1--|--Camera-2--------|---+
           |            |                  |
           v            v                  v
      Entry Camera   Exit Camera     +------+------+
                                     | App Host    |
                                     |             |
                                     | [Entry      |
                                     |  Worker]    |
                                     |             |
                                     | [Exit       |
                                     |  Worker]    |
                                     |             |
                                     | [Recognition
                                     |  Service]   |
                                     |             |
                                     | [Occupancy  |
                                     |  Service]   |
                                     |             |
                                     | [Web API]   |
                                     |             |
                                     | [PostgreSQL]|
                                     |             |
                                     | [Cache +    |
                                     |  Queue]     |
                                     +------+------+
```

### Component Layout (Single Laptop with Dual Webcameras)

```text
+------------- Single Laptop Host â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€+
|                                              |
|  â”Œâ”€ Entry Worker â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  |
|  â”‚ - Webcam 1 (USB) capture               â”‚  |
|  â”‚ - Face detection/embedding             â”‚  |
|  â”‚ - Entry-specific matching & rules      â”‚  |
|  â”‚ - Occupancy check (reject if full)     â”‚  |
|  â”‚ - Event queue & transmission           â”‚  |
|  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜  |
|                                              |
|  â”Œâ”€ Exit Worker â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  |
|  â”‚ - Webcam 2 (USB) capture               â”‚  |
|  â”‚ - Face detection/embedding             â”‚  |
|  â”‚ - Exit-specific matching & rules       â”‚  |
|  â”‚ - Occupancy decrement                  â”‚  |
|  â”‚ - Event queue & transmission           â”‚  |
|  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜  |
|                                              |
|  â”Œâ”€ Recognition Service (Shared) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â” |
|  â”‚ - Embedding computation                 â”‚ |
|  â”‚ - Profile matching                      â”‚ |
|  â”‚ - Threshold application                 â”‚ |
|  â”‚ - Local profile cache mgmt              â”‚ |
|  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜ |
|                                              |
|  â”Œâ”€ Occupancy Service (Shared) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â” |
|  â”‚ - Real-time occupancy calculation      â”‚ |
|  â”‚ - Capacity threshold checks             â”‚ |
|  â”‚ - Librarian alert generation            â”‚ |
|  â”‚ - Occupancy snapshot logging            â”‚ |
|  â”‚ - Entry/exit drift detection            â”‚ |
|  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜ |
|                                              |
|  â”Œâ”€ Web API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  |
|  â”‚ - Authentication + RBAC                â”‚  |
|  â”‚ - Event ingestion (entry/exit/unknown) â”‚  |
|  â”‚ - Profile lifecycle management         â”‚  |
|  â”‚ - User registration workflows          â”‚  |
|  â”‚ - Occupancy & capacity endpoints       â”‚  |
|  â”‚ - Analytics & reporting APIs           â”‚  |
|  â”‚ - Librarian dashboard data             â”‚  |
|  â”‚ - WebSocket for real-time updates      â”‚  |
|  â”‚ - Serves frontend assets               â”‚  |
|  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜ |
|                                              |
|  â”Œâ”€ PostgreSQL Database â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  |
|  â”‚ - Users (name, sr_code, program, type) â”‚  |
|  â”‚ - User embeddings (for matching)       â”‚  |
|  â”‚ - Recognition events (entry/exit)      â”‚  |
|  â”‚ - Occupancy snapshots                  â”‚  |
|  â”‚ - User registrations (flow history)    â”‚  |
|  â”‚ - Staff accounts & audit logs          â”‚  |
|  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜ |
|                                              |
|  â”Œâ”€ Local Cache & Queue â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  |
|  â”‚ - Profile embeddings (RAM)             â”‚  |
|  â”‚ - Event queues (disk - durable)        â”‚  |
|  â”‚ - Occupancy state (RAM + disk)         â”‚  |
|  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜ |
|                                              |
|  â”Œâ”€ USB Webcam Inputs â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   |
|  â”‚ - Webcam 1: Entry point video stream  â”‚   |
|  â”‚ - Webcam 2: Exit point video stream   â”‚   |
|  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜ |
+â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

---

## 4) Core Components

### A. Entry Worker (Camera 1)

**Responsibilities:**

- Capture frames from entry point camera stream in real-time.
- Detect faces and compute embeddings using cached recognition models.
- Match embeddings against local cached profile set.
- Apply entry-specific decision rules using configured thresholds:
  - **Enrolled**: Recognized as known student â†’ automatically register entry (name + SR Code + program).
  - **Unrecognized**: No match or low confidence â†’ flag for librarian alert and optional registration.
  - **Visitor**: Unknown individual â†’ require explicit librarian confirmation before entry.
- **Occupancy Check**: Query current occupancy and reject entry if at/exceeding capacity.
- Queue entry events to durable local queue and transmit to API with idempotency key.
- Periodically reload profiles and runtime settings from API (every 30 seconds).
- Cache occupancy state locally for fast capacity checks.

**Constraints:**

- No direct database writes; all persistence via API.
- Uses local queue for transient failure resilience.
- Entry rejection logic must prioritize occupancy safety over UX delays.

### B. Exit Worker (Camera 2)

**Responsibilities:**

- Capture frames from exit point camera stream in real-time.
- Detect faces and compute embeddings using cached recognition models.
- Match embeddings against local cached profile set.
- Apply exit-specific rules:
  - Log exit event with timestamp.
  - Update local occupancy counter immediately.
  - Support librarian override for manual exit confirmation.
  - Do NOT trigger registration prompts (only entry does).
- Queue exit events to durable local queue and transmit to API.
- Maintain consistency with entry worker's occupancy tracking.

**Constraints:**

- No direct database writes; all persistence via API.
- Exit detection must NEVER block or delay (avoid locking people inside).
- Even if API sync is stale, exit worker permits exits.

### C. Recognition Service (Shared)

**Responsibilities:**

- Compute face embeddings from captured frames (using DeepFace models).
- Match embeddings against local profile cache using similarity threshold (cosine distance).
- Return match results with confidence scores.
- Manage local profile cache lifecycle (load on startup, reload on version change).
- Support both entry and exit workers with consistent matching logic.

**Constraints:**

- Stateless service (no side effects).
- Embedding computation must be < 200ms per frame.

### D. Occupancy Service (Shared) â€” Event-Driven Real-Time Tracking

**Responsibilities:**

- **Event-Driven Updates**: Maintain real-time occupancy state in `daily_occupancy_state` table, updated immediately on every recognition event ingest (not scheduled).
- **Occupancy Calculation**: `occupancy_count = daily_entries - daily_exits` where entries/exits are summed from `daily_occupancy_state` table.
- **Real-Time State Query**: `get_current_occupancy()` returns live occupancy from `daily_occupancy_state` in < 100ms.
- **Capacity Checking**: Compare occupancy against `max_library_capacity` config; return `capacity_warning` flag if occupancy â‰¥ warning threshold (default 0.90).
- **Event Recording**: `record_event(camera_id, captured_at)` called on every successful event ingest; atomically increments `daily_entries` (if camera_id=1/entry) or `daily_exits` (if camera_id=2/exit).
- **Historical Snapshots**: Background `OccupancySnapshotScheduler` generates point-in-time snapshots every N seconds (default 300s) to `occupancy_snapshots` table for trend analysis and auditing.
- **Anomaly Detection**: Log warnings if occupancy < 0 (more exits than entries) or if divergence detected during reconciliation.
- **Publish Updates**: Trigger `analytics_updated` WebSocket broadcast on every occupancy state change with payload: `{occupancy_count, daily_entries, daily_exits, capacity_warning}`.
- **Nightly Reconciliation**: At 00:00 UTC, verify entry/exit counts match accumulated totals; if drift > 5 people, alert admin and allow manual adjustment.

**Architecture Highlights:**

- **Dual-Layer Model**:
  - **Live State**: `daily_occupancy_state` table (one row per date) updated immediately on every event â†’ < 100ms response time for real-time kiosk queries.
  - **Historical**: `occupancy_snapshots` table (many rows per date, one per snapshot interval) for analytics, trend detection, and capacity breach history.
- **Event-Driven (Not Schedule-Dependent)**: Occupancy changes immediately when entry/exit events are ingested, eliminating stale occupancy at endpoint query time.
- **Worker Integration**: Entry worker queries current occupancy before approving entry; exit worker triggers occupancy update on successful exit event.
- **No Race Conditions**: PostgreSQL atomic increments ensure consistency under concurrent entries/exits.

**Constraints:**

- Authoritative source: PostgreSQL `daily_occupancy_state` and `occupancy_snapshots` tables.
- Occupancy queries must be < 100ms for real-time kiosk display.
- Event ingestion must call `occupancy_service.record_event()` synchronously (blocking) to ensure occupancy state is current before any subsequent operations.

### E. Web API + Librarian Dashboard

**Responsibilities:**

- Handle staff and admin authentication with role-based authorization.
- Manage user profiles and enrollment (name, SR Code, program, user type).
- Ingest entry/exit/unrecognized events with idempotent deduplication (by `event_id`).
- Expose occupancy endpoints for real-time kiosk display and dashboard.
- Provide librarian dashboard with:
  - Live occupancy count and capacity percentage.
  - Capacity alerts and manual override capabilities.
  - User flow statistics (entries/exits by type, by program, by hour).
  - Daily report generation (entries, exits, peak hours, program breakdown).
- Expose internal endpoints for worker synchronization (profiles, settings, runtime config).
- Coordinate registration workflows during recognition sessions.
- Serve frontend assets directly (no reverse proxy).
- Broadcast real-time updates via WebSocket (occupancy, alerts, unrecognized detections).

**Constraints:**

- Sole database writer for application operations.
- Enforces validation and access control.
- Must handle high-frequency occupancy queries without bottleneck.

### F. PostgreSQL Data Layer

**Responsibilities:**

- Persist users, profiles, embeddings, and metadata (SR Code, program, flow type).
- Persist entry and exit recognition events with unique idempotency keys.
- Persist occupancy snapshots for analytics and auditing.
- Persist user registrations and flow-type histories.
- Enforce uniqueness for event idempotency via unique constraint on `event_id`.
- Support efficient queries for dashboard, analytics, and capacity reporting.
- Maintain full audit trail of authentication, profile changes, and recognition outcomes.

---

## 5) Data Model

### Required Tables

#### `users`
```sql
id (PK, int)
name (string)
sr_code (string, nullable) â€” SR Code for enrolled students
program (string, nullable) â€” Academic program
user_type (enum: 'enrolled', 'unrecognized', 'visitor', 'staff')
flow_type (enum: 'auto_entry', 'manual_entry', 'manual_registration')
created_at (timestamp)
updated_at (timestamp)
active (boolean, default true)
```

#### `user_embeddings`
```sql
id (PK, int)
user_id (FK â†’ users)
embedding (vector[768] or similar, pgvector for similarity search)
capture_timestamp (timestamp)
camera_id (int: 1=entry, 2=exit)
version (int) â€” for profile version tracking
created_at (timestamp)
```

#### `recognition_events`
```sql
id (PK, int)
event_id (string, unique) â€” idempotency key (worker-generated UUID)
user_id (FK â†’ users, nullable for unrecognized)
event_type (enum: 'entry', 'exit', 'unrecognized_attempt')
entries_at (timestamp, nullable) â€” timestamp of entry event (populated if event_type='entry')
exit_at (timestamp, nullable) â€” timestamp of exit event (populated if event_type='exit')
confidence_score (float: 0.0â€“1.0)
snapshot_path (string, optional) â€” path to face image
status (enum: 'allowed', 'denied', 'manual_override')
details (JSON, optional) â€” additional metadata (e.g., which camera, decision rationale)
created_at (timestamp) â€” server ingestion time

Unique Index: (event_id)
Covering Index: (COALESCE(entered_at, exited_at) DESC, event_type, user_id) for analytics
Index: (DATE(COALESCE(entered_at, exited_at)), event_type) for daily occupancy calculation
```

**Design Rationale:**
- Entry events: `entered_at` populated with recognition timestamp; `exited_at` NULL
- Exit events: `exited_at` populated with recognition timestamp; `entered_at` NULL
- This design makes staff export and audit trail queries simpler: one row per event with event-specific timestamp
- Occupancy calculation uses COALESCE(entered_at, exited_at) as the authoritative event timestamp for daily summation

#### `daily_occupancy_state` (NEW â€” Event-Driven Real-Time State)
```sql
id (PK, int)
state_date (date, unique) â€” calendar date in UTC
daily_entries (int, default 0) â€” cumulative entry events for this date
daily_exits (int, default 0) â€” cumulative exit events for this date
occupancy_count (int, computed: daily_entries - daily_exits) â€” current occupancy
last_updated (timestamp) â€” when occupancy was last recalculated

Unique Index: (state_date) â€” one row per date
Index: (last_updated DESC) for finding stale entries
```

**Purpose:**
- Maintains live, real-time occupancy state updated immediately on every event ingest
- Single point of truth for current occupancy queries
- Reset at midnight UTC to 0/0 for new day
- Entry worker queries this table (< 100ms) to check if library is at capacity before approving entry

#### `occupancy_snapshots` (NEW â€” Historical 5-Min Intervals)
```sql
id (PK, int)
snapshot_timestamp (timestamp) â€” when snapshot was taken
snapshot_date (date) â€” calendar date of snapshot (for easy filtering)
occupancy_count (int) â€” people inside at snapshot moment
capacity_limit (int) â€” library capacity at this time
capacity_warning (boolean) â€” was library at/over capacity at this moment?
daily_entries (int) â€” cumulative entry events up to snapshot
daily_exits (int) â€” cumulative exit events up to snapshot
created_at (timestamp) â€” server timestamp when snapshot recorded

Index: (snapshot_timestamp DESC) for time-series queries
Index: (snapshot_date DESC) for date-based queries
Index: (snapshot_date, occupancy_count DESC) for peak occupancy per day
```

**Purpose:**
- Generated by `OccupancySnapshotScheduler` every 5 minutes (configurable via `occupancy_snapshot_interval_seconds`)
- Provides historical time-series data for capacity trend analysis, peak occupancy detection, reporting
- Enables staff to see occupancy trends over a day without querying individual events

#### `user_registrations`
```sql
id (PK, int)
user_id (FK â†’ users)
registration_type (enum: 'auto_enrolled', 'manual_visitor', 'unrecognized_flagged')
registered_at (timestamp)
registered_by (FK â†’ staff_accounts, nullable for auto)
sr_code (string, nullable)
program (string, nullable)
notes (text, optional)
created_at (timestamp)

Index: (user_id, created_at DESC)
```

#### `staff_accounts`
```sql
id (PK, int)
username (string, unique)
email (string)
role (enum: 'admin', 'librarian', 'monitor')
password_hash (string)
created_at (timestamp)
updated_at (timestamp)
active (boolean, default true)
```

#### `app_settings`
```sql
id (PK, int)
key (string, unique)
value (string or JSON)
updated_at (timestamp)

Examples:
  - recognition_threshold: 0.6
  - capacity_limit: 150
  - capacity_warning_threshold: 0.80
  - profiles_version: 42
  - settings_version: 10
  - occupancy_snapshot_interval: 300 (seconds)
```

#### `audit_log`
```sql
id (PK, int)
action (string) â€” e.g., "login", "profile_update", "manual_override", "capacity_alert"
actor_id (FK â†’ staff_accounts, nullable for system actions)
target_id (int, generic target ID)
target_type (string) â€” e.g., "user", "event", "settings"
details (JSON) â€” additional context
timestamp (timestamp)

Index: (timestamp DESC, action)
```

### Key Indexes for Performance

```sql
-- Idempotency check (must be unique for idempotent inserts) â€” CRITICAL
CREATE UNIQUE INDEX idx_recognition_events_event_id 
  ON recognition_events (event_id);

-- Event analytics queries (using COALESCE for unified timestamp column)
CREATE INDEX idx_recognition_events_timestamp_type 
  ON recognition_events (COALESCE(entered_at, exited_at) DESC, event_type, user_id);

-- Real-time occupancy state lookup â€” CRITICAL (must be < 100ms)
CREATE UNIQUE INDEX idx_daily_occupancy_state_date 
  ON daily_occupancy_state (state_date);

-- Daily occupancy calculation (for occupancy snapshots and reconciliation)
CREATE INDEX idx_recognition_events_date_type 
  ON recognition_events (DATE(COALESCE(entered_at, exited_at)), event_type);

-- Occupancy trend queries (5-min snapshots)
CREATE INDEX idx_occupancy_snapshots_timestamp 
  ON occupancy_snapshots (snapshot_timestamp DESC);

CREATE INDEX idx_occupancy_snapshots_date 
  ON occupancy_snapshots (snapshot_date DESC);

-- Profile sync queries
CREATE INDEX idx_user_embeddings_user_version 
  ON user_embeddings (user_id, version DESC);

-- Audit trail queries
CREATE INDEX idx_audit_log_timestamp_action 
  ON audit_log (timestamp DESC, action);
```

---

## 6) Runtime Synchronization Contract

### A. Profiles and Embeddings

- API maintains `profiles_version` in `app_settings`.
- Both entry and exit workers fetch full profile + embedding snapshot on startup.
- Workers poll `GET /api/internal/profiles/version` every 30 seconds (configurable).
- On version change, worker atomically replaces local cache.
- Version mismatch between entry and exit workers is tolerated but logged for debugging.

### B. Occupancy State

- Entry and exit workers exchange occupancy updates via API `occupancy` endpoint.
- Entry worker checks occupancy before approving entry; rejects if at capacity.
- Exit worker increments occupancy counter immediately (for real-time display).
- API reconciles any drift between entry/exit event counts daily at midnight (00:00 UTC).
- If drift > 5 people, admin is alerted and may manually adjust.

### C. Runtime Settings

- API maintains `settings_version` in `app_settings`.
- Both workers fetch recognition thresholds, capacity limits, policies on startup and on version change.
- Invalid settings are rejected before activation.
- Separate thresholds for entry vs. exit decisions are supported (e.g., lower threshold for exits).

### D. Failure Behavior

- If sync fails, workers continue with last known valid configuration.
- Sync failures are logged and trigger librarian alerts if persistent (> 5 min).
- Entry worker enters `conservative_mode` if occupancy state unverified > 2 minutes (blocks all entries for safety).
- Exit worker permits exits even if sync is stale (never locks people inside).
- API provides manual override UI for librarian to enable/disable entry acceptance during outages.

---

## 7) End-to-End Data Flows

### Flow 1: Entry Recognition (Enrolled Student)

```
1. Student approaches Entry Camera 1.
2. Entry Worker detects face â†’ computes embedding.
3. Worker matches embedding against local profile cache.
4. Match succeeds with high confidence â†’ user_type = 'enrolled'.
5. Worker checks current occupancy:
   - If occupancy < capacity:    approve entry.
   - If occupancy >= capacity:    create 'denied' event, trigger alert.
6. Worker creates entry event:
   {
     event_id: UUID,
     user_id: <student_id>,
     event_type: 'entry',
     camera_id: 1,
     timestamp: <recognition_time>,
     confidence_score: 0.95,
     status: 'allowed'
   }
7. Event written to local durable queue.
8. Worker POSTs to API: POST /api/internal/recognition-events
9. API validates, deduplicates by event_id, writes to PostgreSQL.
10. API increments occupancy counter (entries - exits).
11. API publishes occupancy update via WebSocket to librarian kiosks.
12. Entry kiosk display shows confirmation: "Entry Approved â€” Current Occupancy: 42/150"
13. Student proceeds; barrier/gate opens (if applicable).
```

### Flow 2: Entry Recognition (Unrecognized Individual)

```
1. Unrecognized person approaches Entry Camera 1.
2. Entry Worker detects face â†’ computes embedding.
3. Worker matches embedding against local profile cache.
4. Match fails or confidence < threshold.
5. Worker creates unrecognized_attempt event:
   {
     event_id: UUID,
     user_id: null,
     event_type: 'unrecognized_attempt',
     camera_id: 1,
     timestamp: <recognition_time>,
     confidence_score: 0.45,
     snapshot_path: '/snapshots/2026-05-01/unknown_person_123.jpg',
     status: 'pending_review'
   }
6. Event written to local queue â†’ transmitted to API.
7. API logs event, triggers librarian alert.
8. Librarian kiosk (entry) displays:
   - Face snapshot of unknown person
   - "Unrecognized Detection â€” Register?"
   - Buttons: [Register as Student], [Register as Visitor], [Deny Entry]

9a. If Librarian clicks [Register as Student]:
    - Librarian enters name, SR Code, program.
    - API creates new user: user_type='enrolled', flow_type='auto_entry'
    - API increments profiles_version.
    - Entry Worker reloads profiles on next sync.
    - API creates manual entry override event.
    - Librarian clicks [Approve Entry].
    - Occupancy incremented.

9b. If Librarian clicks [Register as Visitor]:
    - Librarian enters name, visitor category.
    - API creates new user: user_type='visitor', flow_type='manual_registration'
    - Librarian clicks [Approve Entry].
    - Occupancy incremented.

9c. If Librarian clicks [Deny Entry]:
    - Event logged as security incident.
    - Entry blocked; person directed to reception.
```

### Flow 3: Entry Recognition (Registered Visitor)

```
1. Registered visitor approaches Entry Camera 1.
2. Entry Worker detects face â†’ computes embedding.
3. Worker matches embedding against local profile cache.
4. Match succeeds, user_type='visitor'.
5. Worker checks occupancy (same as Flow 1).
6. Worker creates entry event â†’ transmitted to API.
7. API increments occupancy.
8. Entry kiosk confirms: "Entry Approved for Visitor â€” Current Occupancy: 45/150"
9. Visitor proceeds.
```

### Flow 4: Exit Recognition

```
1. Person approaches Exit Camera 2.
2. Exit Worker detects face â†’ computes embedding.
3. Worker matches embedding against local profile cache.
4. Match succeeds â†’ user identified (any user_type).
5. Worker creates exit event:
   {
     event_id: UUID,
     user_id: <person_id>,
     event_type: 'exit',
     camera_id: 2,
     timestamp: <recognition_time>,
     confidence_score: 0.92,
     status: 'allowed'
   }
6. Event written to local queue â†’ transmitted to API.
7. API validates, decrements occupancy counter.
8. API publishes occupancy update via WebSocket.
9. Exit kiosk displays: "Exit Confirmed â€” Current Occupancy: 44/150"
10. Person exits; barrier/gate opens.
```

### Flow 5: Capacity Alert and Entry Rejection

```
1. Library currently at 148 occupancy; capacity = 150.
2. Person A enters (entry event created).
3. API increments occupancy to 149.
4. Person B approaches Entry Camera 1.
5. Entry Worker checks occupancy: 149 >= 150? NO, still OK.
6. Person B entry approved.
7. API increments occupancy to 150.

8. Person C approaches Entry Camera 1.
9. Entry Worker queries occupancy: 150 >= 150? YES.
10. Entry Worker creates 'denied' event:
    {
      event_id: UUID,
      user_id: <person_c_id>,
      event_type: 'entry',
      camera_id: 1,
      timestamp: <recognition_time>,
      status: 'denied'
    }
11. Event transmitted to API.
12. API broadcasts CAPACITY_REACHED alert via WebSocket.
13. Entry kiosk displays: "LIBRARY AT CAPACITY âš ï¸ â€” Please try again later"
14. Librarian dashboard shows red alert: "Capacity Reached: 150/150"
15. If librarian manually overrides:
    - Librarian clicks [Force Entry Override] button.
    - API creates manual_override event.
    - Person C is allowed entry (occupancy now 151).
```

### Flow 6: Daily Occupancy Reconciliation

```
At midnight (00:00 UTC):
1. Scheduled job runs in API.
2. Job queries all entry events for day: SELECT COUNT(*) FROM recognition_events 
                                         WHERE event_type='entry' AND DATE(timestamp)=TODAY
3. Job queries all exit events for day: SELECT COUNT(*) FROM recognition_events 
                                        WHERE event_type='exit' AND DATE(timestamp)=TODAY
4. Job compares:
   - If entries - exits != last occupancy snapshot: DRIFT DETECTED
   - If drift > 5: log alert, notify admin
   - If drift = 0: everything OK, log clean reconciliation
5. Job creates final occupancy snapshot for the day.
6. Admin dashboard shows reconciliation report.
```

### Flow 7: Profile Update (by Admin)

```
1. Admin updates user profile in dashboard: change SR Code or program.
2. API writes profile changes and embeddings to PostgreSQL.
3. API increments profiles_version.
4. Both entry and exit workers detect version change on next poll.
5. Workers fetch updated profile snapshot and replace local cache atomically.
```

---

## 8) API Contract

### Internal Worker Endpoints

#### Event Ingestion (Worker â†’ API)

**POST /api/internal/recognition-events**

Request:
```json
{
  "event_id": "a1b2c3d4-e5f6-47g8-h9i0-j1k2l3m4n5o6",
  "user_id": 42,
  "event_type": "entry|exit|unrecognized_attempt",
  "camera_id": 1,
  "confidence_score": 0.95,
  "timestamp": "2026-05-01T14:23:45Z",
  "snapshot_path": "/snapshots/2026-05-01/frame_123.jpg",
  "status": "allowed|denied|manual_override",
  "details": { "extra": "metadata" }
}
```

Response (Success):
```json
{
  "success": true,
  "event_id": "a1b2c3d4-...",
  "occupancy_count": 42,
  "capacity_limit": 150,
  "capacity_warning": false
}
```

Response (Idempotent Duplicate):
```json
{
  "success": true,
  "event_id": "a1b2c3d4-...",
  "note": "Event already processed (idempotent)"
}
```

#### Profile & Embedding Sync

**GET /api/internal/profiles/version**

Response:
```json
{
  "version": 42,
  "updated_at": "2026-05-01T10:00:00Z"
}
```

**GET /api/internal/profiles/snapshot**

Query Params: `?version=40` (optional, for conditional fetch)

Response:
```json
{
  "version": 42,
  "profiles": [
    {
      "id": 1,
      "name": "Alice Student",
      "sr_code": "SR-22-01111",
      "program": "Computer Science",
      "user_type": "enrolled",
      "embeddings": [
        {
          "vector": [0.1, 0.2, ..., 0.9],
          "camera_id": 1,
          "version": 42
        }
      ]
    },
    ...
  ]
}
```

#### Runtime Configuration

**GET /api/internal/runtime-config**

Response:
```json
{
  "settings_version": 10,
  "recognition_threshold": 0.6,
  "capacity_limit": 150,
  "capacity_warning_threshold": 0.80,
  "entry_policy": {
    "auto_enroll_enrolled": true,
    "require_visitor_approval": true
  },
  "exit_policy": {
    "allow_without_match": false,
    "override_capable": true
  },
  "occupancy_snapshot_interval": 300
}
```

#### Occupancy Query (Worker â†’ API)

**GET /api/internal/occupancy**

Response:
```json
{
  "occupancy_count": 42,
  "capacity_limit": 150,
  "occupancy_percent": 28.0,
  "capacity_warning": false,
  "daily_entries": 87,
  "daily_exits": 45,
  "last_update": "2026-05-01T14:23:50Z"
}
```

---

### Librarian Dashboard Endpoints

#### Authentication

**POST /api/auth/login**

Request:
```json
{
  "username": "librarian_001",
  "password": "secure_password"
}
```

Response:
```json
{
  "success": true,
  "token": "eyJhbGciOiJIUzI1NiIs...",
  "user": {
    "id": 5,
    "username": "librarian_001",
    "role": "librarian",
    "email": "lib@library.edu"
  }
}
```

**POST /api/auth/logout**

**GET /api/auth/me**

Response: Current user info

#### Occupancy & Analytics

**GET /api/occupancy/current**

Response:
```json
{
  "occupancy_count": 42,
  "capacity_limit": 150,
  "occupancy_percent": 28.0,
  "capacity_warning": false,
  "status": "normal|warning|critical"
}
```

**GET /api/occupancy/history?date=2026-05-01&limit=100**

Returns 5-min occupancy snapshots for a given date (historical time-series data).

Response:
```json
{
  "date": "2026-05-01",
  "snapshot_interval_seconds": 300,
  "snapshots": [
    {
      "snapshot_timestamp": "2026-05-01T08:00:00Z",
      "occupancy_count": 10,
      "capacity_limit": 150,
      "capacity_warning": false,
      "daily_entries": 10,
      "daily_exits": 0
    },
    {
      "snapshot_timestamp": "2026-05-01T08:05:00Z",
      "occupancy_count": 18,
      "capacity_limit": 150,
      "capacity_warning": false,
      "daily_entries": 18,
      "daily_exits": 0
    },
    {
      "snapshot_timestamp": "2026-05-01T09:00:00Z",
      "occupancy_count": 35,
      "capacity_limit": 150,
      "capacity_warning": false,
      "daily_entries": 50,
      "daily_exits": 15
    },
    ...
  ]
}
```

**GET /api/events?start_date=2026-05-01&end_date=2026-05-01&type=entry|exit|unrecognized&user_type=enrolled**

Response:
```json
{
  "total": 87,
  "events": [
    {
      "id": 1,
      "event_id": "a1b2c3d4-...",
      "user_id": 42,
      "user_name": "Alice Student",
      "event_type": "entry",
      "camera_id": 1,
      "timestamp": "2026-05-01T08:15:30Z",
      "status": "allowed"
    },
    ...
  ]
}
```

**GET /api/analytics/daily-report?date=2026-05-01**

Response:
```json
{
  "date": "2026-05-01",
  "total_entries": 87,
  "total_exits": 42,
  "by_user_type": {
    "enrolled": { "entries": 60, "exits": 25 },
    "visitor": { "entries": 20, "exits": 12 },
    "unrecognized": { "entries": 7, "exits": 5 }
  },
  "by_program": {
    "Computer Science": { "entries": 25, "exits": 10 },
    "Engineering": { "entries": 30, "exits": 18 },
    ...
  },
  "peak_hour": "12:00â€“13:00",
  "peak_occupancy": 142
}
```

**GET /api/analytics/occupancy-trends?days=7**

Response:
```json
{
  "period": "7 days",
  "data": [
    {
      "date": "2026-04-25",
      "avg_occupancy": 45.2,
      "peak_occupancy": 125,
      "capacity_breaches": 0
    },
    ...
  ]
}
```

#### Profile Management

**POST /api/profiles**

Request:
```json
{
  "name": "John Visitor",
  "sr_code": null,
  "program": null,
  "user_type": "visitor",
  "flow_type": "manual_registration"
}
```

Response: Created profile object

**PUT /api/profiles/:id**

Request: Profile updates (name, sr_code, program, etc.)

Response: Updated profile

**GET /api/profiles?page=1&limit=50**

Response: Paginated list of profiles

#### User Registration & Alerts

**GET /api/alerts/unrecognized**

Response:
```json
{
  "pending": [
    {
      "event_id": "a1b2c3d4-...",
      "snapshot_path": "/snapshots/2026-05-01/unknown_person_123.jpg",
      "timestamp": "2026-05-01T14:23:45Z",
      "confidence_score": 0.45,
      "recommendation": "Register as student or visitor"
    },
    ...
  ]
}
```

**POST /api/register/unrecognized**

Request:
```json
{
  "event_id": "a1b2c3d4-...",
  "name": "Jane Doe",
  "sr_code": "SR-22-01234",
  "program": "Business",
  "user_type": "enrolled",
  "action": "approve|deny"
}
```

Response:
```json
{
  "success": true,
  "user_id": 123,
  "occupancy_incremented": true
}
```

**POST /api/register/visitor**

Request:
```json
{
  "name": "External Guest",
  "visitor_category": "speaker|researcher|guest",
  "email": "guest@example.com"
}
```

Response: Created visitor profile + entry override token

#### Manual Overrides

**POST /api/events/manual-exit/:user_id**

Request:
```json
{
  "reason": "User unable to exit normally â€” assisted exit",
  "librarian_id": 5
}
```

Response:
```json
{
  "success": true,
  "occupancy_decremented": true
}
```

**POST /api/occupancy/adjust**

Request:
```json
{
  "adjustment": +2,
  "reason": "Drift correction after reconciliation",
  "admin_id": 1
}
```

Response:
```json
{
  "success": true,
  "new_occupancy": 44,
  "audit_logged": true
}
```

#### Settings & System Status

**PUT /api/settings/recognition**

Request:
```json
{
  "recognition_threshold": 0.65,
  "entry_threshold": 0.6,
  "exit_threshold": 0.5
}
```

Response:
```json
{
  "success": true,
  "settings_version": 11
}
```

**PUT /api/settings/capacity**

Request:
```json
{
  "capacity_limit": 200,
  "warning_threshold_percent": 80
}
```

**GET /api/system-status**

Response:
```json
{
  "api_status": "online",
  "entry_worker_status": "online",
  "exit_worker_status": "online",
  "database_status": "online",
  "last_entry_event": "2026-05-01T14:23:45Z",
  "last_exit_event": "2026-05-01T14:20:10Z",
  "profiles_version": 42,
  "settings_version": 10
}
```

**GET /api/audit-log?limit=100**

Response: Paginated audit trail

---

### WebSocket for Real-Time Updates

**WS /ws/librarian-kiosk?station=entry|exit&token=<auth_token>**

**WS /ws/analytics** (Alternative endpoint for analytics dashboard):

Server publishes on event ingest:
```json
{
  "type": "analytics_updated",
  "data": {
    "occupancy_count": 42,
    "capacity_limit": 150,
    "occupancy_percent": 28.0,
    "capacity_warning": false,
    "daily_entries": 87,
    "daily_exits": 45,
    "timestamp": "2026-05-01T14:23:50Z"
  }
}
```

**Broadcast Timing:**
- Emitted immediately after every entry/exit event is ingested and occupancy state is updated (< 1 sec latency)
- All connected kiosk clients receive update simultaneously
- Enables librarian dashboards to show real-time occupancy and capacity warnings without polling

Example occupancy_update (legacy, for kiosk displays):
```json
{
  "type": "occupancy_update",
  "data": {
    "occupancy_count": 42,
    "capacity_limit": 150,
    "occupancy_percent": 28.0,
    "status": "normal",
    "daily_entries": 87,
    "daily_exits": 45
  }
}
```

Example capacity alert:
```json
{
  "type": "capacity_alert",
  "data": {
    "occupancy_count": 150,
    "capacity_limit": 150,
    "message": "Library at capacity",
    "timestamp": "2026-05-01T14:30:00Z"
  }
}
```

Example unrecognized detection:
```json
{
  "type": "unrecognized_detection",
  "data": {
    "event_id": "a1b2c3d4-...",
    "snapshot_path": "/snapshots/2026-05-01/unknown_person_456.jpg",
    "timestamp": "2026-05-01T14:31:00Z",
    "action_required": true
  }
}
```

---

## 9) Security and Reliability Controls

### Access Control & Authentication

- **Role-Based Access Control (RBAC)**:
  - `admin`: Full system access (settings, profile management, manual overrides, all reports).
  - `librarian`: Occupancy monitoring, registration approvals, manual entry/exit overrides, daily reports.
  - `monitor`: Read-only dashboard access (occupancy, reports, analytics).
- Password hashing (bcrypt or similar) and secure session management with JWT tokens.
- Worker-to-API authentication via bearer token (shared secret or mutual TLS).
- LAN firewall rules restrict API and worker port exposure.
- Token expiration: 8 hours for staff, 12 hours for admin.

### Data Integrity & Idempotency

- Idempotent event ingestion with unique `event_id` (worker-generated UUID v4).
- Database unique constraint on `recognition_events.event_id` prevents duplicates.
- Transactional writes: event insertion + occupancy update atomic.
- Event timestamps are recognition timestamps (not server receive time), ensuring correct daily counts.

### Reliability & Failure Recovery

- **Entry Worker Queue**: Maintains durable local queue (JSON files on disk) for entry events.
- **Exit Worker Queue**: Maintains durable local queue for exit events.
- Queued events retried up to 10 times with exponential backoff before alerting admin.
- If API unavailable > 2 min:
  - Entry Worker enters `capacity_check_only` mode (blocks all new entries conservatively).
  - Exit Worker permits exits (never locks users inside).
- Local profile cache persisted to disk; restored on restart.
- Occupancy reconciliation runs nightly (00:00 UTC) to correct entry/exit drift.
- If drift > 5 people, admin is notified and can manually adjust via dashboard.

### Audit & Compliance

- Full audit trail of authentication events, profile changes, manual overrides, settings updates.
- All events logged with `created_at` timestamp and optional `actor_id` (who triggered action).
- Biometric artifacts (snapshots) retention: 30 days default, then purge.
- Compliance reporting available: access logs, registration history, capacity breaches.

### Worker Health & Monitoring

- Entry and exit workers report heartbeat to API every 30 seconds.
- API dashboard shows worker status: online, degraded, offline.
- Prolonged worker offline (> 5 min) triggers librarian alert.
- Librarian can manually enable/disable entry acceptance during worker downtime.

---

## 10) Deployment Topology

### Single-Laptop Deployment (Baseline)

```
+---------- Single Laptop Host ----------+
|                                        |
|  Entry Worker (Camera 1 thread)         |
|  Exit Worker (Camera 2 thread)          |
|  Recognition Service (shared)           |
|  Occupancy Service (shared)             |
|  Web API (Flask/FastAPI)                |
|  PostgreSQL (local or LAN host)         |
|  Event Queues (local disk)              |
|  Profile Cache (local RAM + disk)       |
|                                        |
|  Listening Ports:                      |
|    5000: Web API (staff/admin)          |
|    5001: WebSocket (real-time updates)  |
|    8000: Internal IPC (workers)         |
+----------------------------------------+
    |            |
    |            |
USB 3.0      USB 3.0
    |            |
    v            v
[Entry       [Exit
 Webcam]     Webcam]
    |            |
    +---- Physical Entry/Exit Points

+-- Admin/Librarian Workstation --+
| Browser (HTTP/HTTPS)            |
| Port 5000: Web API              |
| WebSocket: Real-time updates    |
+------- Connected via LAN -------+
```

### Hardware Configuration

**Laptop Requirements:**
- CPU: Intel i5/i7 or equivalent (multi-core for dual worker threads)
- RAM: 8 GB minimum (16 GB recommended for smooth processing)
- Storage: 256 GB SSD (for PostgreSQL, embeddings cache, event queues)
- USB Ports: Minimum 2 USB 3.0 ports (for webcameras)
- Network: Gigabit Ethernet or 5GHz WiFi for LAN connectivity

**Webcameras:**
- 2Ã— USB Webcameras (1080p/30fps or better)
- USB 3.0 for low-latency feed (USB 2.0 acceptable if necessary)
- Wide field of view (100Â°+ recommended for entry/exit zones)

**Optional Display/Tablet:**
- Separate monitor or tablet connected to laptop for librarian real-time display
- Shows occupancy, capacity warnings, and unrecognized detection alerts
- Can be on separate browser tab or dedicated display client

**Admin/Staff Access:**
- Any workstation on the LAN can access dashboard via browser (port 5000)
- Separate from the laptop running the recognition system

### Deployment Steps

1. **Setup Laptop**:
   - Install Python 3.9+ and dependencies from `requirements.txt`
   - Configure PostgreSQL (local or LAN-accessible instance)
   - Create database and run migrations

2. **Connect Webcameras**:
   - Plug Webcam 1 (entry point) into USB 3.0 port 1
   - Plug Webcam 2 (exit point) into USB 3.0 port 2
   - Test camera feeds: `python -c "import cv2; cv2.VideoCapture(0).isOpened()"`

3. **Configure Entry/Exit Zones**:
   - Mount Webcam 1 at entry point (capture approaching faces)
   - Mount Webcam 2 at exit point (capture departing faces)
   - Adjust camera angles for optimal face detection

4. **Start Application Stack**:
   - Run: `python -m app.host_stack`
   - Verify entry-worker and exit-worker processes started
   - Verify Web API listening on port 5000

5. **Configure Librarian Display**:
   - Open browser on same laptop or tablet to `http://localhost:5000/dashboard`
   - Or on admin workstation: `http://<laptop-ip>:5000/dashboard`
   - Connect to WebSocket for real-time occupancy updates

6. **Configure Admin/Staff Access**:
   - On any LAN workstation, open browser to `http://<laptop-ip>:5000`
   - Login with admin credentials
   - Access analytics, reports, and system settings

7. **Validate System**:
   - Test 10 consecutive entries (verify occupancy increments)
   - Test 5 consecutive exits (verify occupancy decrements)
   - Test capacity alert at limit (verify entry rejection)
   - Test unrecognized detection (verify librarian alert + registration flow)
   - Verify WebSocket updates reach librarian display within 1 second

---

## 11) Codebase Structure

### Recommended Directory Organization

```
app/
  â”œâ”€â”€ __init__.py
  â”œâ”€â”€ host_stack.py           # Orchestrator: API + entry-worker + exit-worker
  â”œâ”€â”€ flask_app.py            # Flask app factory
  â”œâ”€â”€ entry_worker.py         # Entry camera recognition loop [NEW]
  â”œâ”€â”€ exit_worker.py          # Exit camera recognition loop [NEW]
  â””â”€â”€ realtime.py             # WebSocket broadcast for librarian kiosks [NEW]

services/
  â”œâ”€â”€ __init__.py
  â”œâ”€â”€ recognition_service.py  # Embedding + matching logic
  â”œâ”€â”€ occupancy_service.py    # Real-time occupancy tracking [NEW]
  â”œâ”€â”€ face_service.py         # Face detection wrapper
  â”œâ”€â”€ embedding_service.py    # Embedding generation
  â”œâ”€â”€ registration_service.py # User registration workflows [NEW]
  â”œâ”€â”€ profile_sync_service.py # Coordinated profile/settings sync [NEW]
  â””â”€â”€ ... (existing services)

routes/
  â”œâ”€â”€ __init__.py
  â”œâ”€â”€ routes.py               # Main API routes
  â”œâ”€â”€ auth_routes.py          # Authentication
  â”œâ”€â”€ occupancy_routes.py     # Occupancy + capacity endpoints [NEW]
  â”œâ”€â”€ registration_routes.py  # User registration (unrecognized, visitor) [NEW]
  â”œâ”€â”€ analytics_routes.py     # Daily reports + trends [NEW]
  â””â”€â”€ ... (existing routes)

database/
  â”œâ”€â”€ __init__.py
  â”œâ”€â”€ schema.py               # Table definitions (updated for dual-camera)
  â”œâ”€â”€ repository.py           # Data access layer
  â”œâ”€â”€ migrations/
  â”‚   â”œâ”€â”€ versions/
  â”‚   â”‚   â”œâ”€â”€ 001_dual_camera_schema.py [NEW]
  â”‚   â”‚   â”œâ”€â”€ 002_user_types_and_flows.py [NEW]
  â”‚   â”‚   â”œâ”€â”€ 003_occupancy_snapshots.py [NEW]
  â”‚   â”‚   â””â”€â”€ ... (existing migrations)
  â””â”€â”€ postgres.py             # Connection management

workers/
  â”œâ”€â”€ __init__.py
  â”œâ”€â”€ event_queue.py          # Local durable queue (entry/exit events)
  â”œâ”€â”€ sync_manager.py         # Profile/settings sync logic [NEW]
  â””â”€â”€ ... (existing workers)

utils/
  â”œâ”€â”€ __init__.py
  â”œâ”€â”€ occupancy_calculator.py # Occupancy computation logic [NEW]
  â”œâ”€â”€ idempotency.py          # Event deduplication helpers [NEW]
  â””â”€â”€ ... (existing utilities)

frontend/
  â”œâ”€â”€ index.html
  â”œâ”€â”€ package.json
  â”œâ”€â”€ vite.config.js
  â””â”€â”€ src/
      â”œâ”€â”€ App.jsx
      â”œâ”€â”€ components/
      â”‚   â”œâ”€â”€ OccupancyCard.jsx      # Live occupancy display [NEW]
      â”‚   â”œâ”€â”€ CapacityAlert.jsx      # Alert UI [NEW]
      â”‚   â”œâ”€â”€ RegistrationForm.jsx   # Unrecognized registration [NEW]
      â”‚   â”œâ”€â”€ UserTypeSelector.jsx   # Student/visitor selector [NEW]
      â”‚   â””â”€â”€ ... (existing components)
      â”œâ”€â”€ pages/
      â”‚   â”œâ”€â”€ LibrarianDashboard.jsx # Real-time occupancy + alerts [NEW]
      â”‚   â”œâ”€â”€ AdminDashboard.jsx     # Admin reports + settings [NEW]
      â”‚   â”œâ”€â”€ RegistrationFlow.jsx   # Unrecognized registration flow [NEW]
      â”‚   â””â”€â”€ ... (existing pages)
      â””â”€â”€ hooks/
          â”œâ”€â”€ useWebSocket.js        # WebSocket connection hook [NEW]
          â””â”€â”€ ... (existing hooks)
```

### Key New Modules to Implement

1. **`services/occupancy_service.py`** â€” Real-time occupancy calculation, capacity checks, snapshots.
2. **`services/registration_service.py`** â€” User registration workflows (enrolled, visitor, unrecognized).
3. **`app/entry_worker.py`** â€” Entry-specific recognition loop with capacity checks.
4. **`app/exit_worker.py`** â€” Exit-specific recognition loop with occupancy decrement.
5. **`routes/occupancy_routes.py`** â€” Occupancy query and dashboard endpoints.
6. **`routes/registration_routes.py`** â€” Manual registration, approval/denial flows.
7. **`routes/analytics_routes.py`** â€” Daily reports, trend analysis, program breakdowns.
8. **`services/profile_sync_service.py`** â€” Coordinated profile/settings sync for both workers.
9. **`app/realtime.py`** â€” WebSocket server for librarian kiosk real-time updates.
10. **Database migrations** â€” Add `occupancy_snapshots`, `user_registrations`, enhanced `users` table.

---

## 12) Non-Functional Requirements

| Requirement | Target | Rationale |
|---|---|---|
| **Recognition Latency** | Entry/exit decision < 500 ms | User experience, real-time processing |
| **Occupancy Query Latency** | Real-time kiosk display < 100 ms | Live occupancy accuracy |
| **WebSocket Broadcast** | Occupancy updates to kiosks < 1 sec | Real-time alert responsiveness |
| **Event Durability** | Zero silent loss of entry/exit events | Audit trail + occupancy correctness |
| **Dashboard Availability** | Independent from worker stability | Admin always has read-only access |
| **Capacity Accuracy** | Â±1 person tolerance after nightly reconciliation | Acceptable margin for audit |
| **Audit Trail** | Full history of registrations, overrides, settings changes | Compliance + debugging |
| **Biometric Retention** | Snapshots purged after 30 days; embeddings retained indefinitely | Privacy + GDPR compliance |

---

## 13) Implementation Checklist

### Phase 1: Worker Runtime and Event Ingestion (COMPLETE)

- [x] Use dual worker launchers with explicit role/camera routing (`workers/entry_worker.py`, `workers/exit_worker.py`, `workers/recognition_worker.py`).
- [x] Route worker events through `/api/internal/recognition-events` with worker-generated `event_id` idempotency keys.
- [x] Persist event direction using `entered_at`/`exited_at` timestamps in `recognition_events`.
- [x] Implement durable outbound queue with retry/backoff for worker event delivery (`workers/durable_queue.py`).
- [x] Add internal snapshot/config sync endpoints for workers (`/api/internal/profiles/*`, `/api/internal/runtime-config`).
- [x] Move camera stream selection to config/env (`entry_cctv_stream_source`, `exit_cctv_stream_source`).

### Phase 2: Occupancy State and Capacity Gate (MOSTLY COMPLETE)

- [x] Implement `services/occupancy_service.py` with event-driven `record_event(camera_id, captured_at)`.
- [x] Add `daily_occupancy_state` live table and `occupancy_snapshots` history table with migrations.
- [x] Start `OccupancySnapshotScheduler` from host runtime (`app/host_stack.py`).
- [x] Implement `GET /api/occupancy/current`, `GET /api/occupancy/history`, `GET /api/occupancy/summary`.
- [x] Enforce entry capacity checks via `/api/internal/capacity-gate` from worker recognition flow.
- [x] Refactor `recognition_events` schema from persisted `camera_id`/`station_id` to `entered_at`/`exited_at` (`20260502_0007`).
- [x] Implement `POST /api/occupancy/adjust` for manual drift correction.
- [x] Implement nightly reconciliation job for drift detection and operator alerting.
- [x] Apply `occupancy_warning_threshold` config directly in occupancy warning computation.

### Phase 3: Alerting and Realtime Broadcasts (COMPLETE)

- [x] Implement Flask-SocketIO integration and `analytics_updated` event channel.
- [x] Add alert persistence (`occupancy_alerts`) and migration (`20260502_0008`).
- [x] Implement alert API endpoints (`GET /api/alerts`, `POST /api/alerts/<id>/dismiss`).
- [x] Create capacity-reached alert records when capacity gate blocks entry.
- [x] Keep `services/occupancy_alert_service.py` as a reusable evaluator module.
- [x] Integrate `occupancy_alert_service.py` into live ingest/capacity execution path.
- [x] Emit dedicated capacity-threshold realtime alerts to kiosk/dashboard clients.
- [x] Ensure every occupancy-related websocket payload includes `capacity_warning`.
- [x] Broadcast unrecognized-detection events with snapshot metadata for librarian workflows.

### Phase 4: Registration and Identity Flows (MOSTLY COMPLETE)

- [x] Implement registration session lifecycle APIs (`/api/register-info`, `/api/register-session/start`, `/api/register-session/cancel`, `/api/register-reset`).
- [x] Implement registration submit pipeline (`POST /register`) to save profile data and embeddings.
- [x] Extend canonical `users` schema with explicit `user_type` and `flow_type` columns.
- [x] Add `user_registrations` audit table and migration.
- [x] Add dedicated endpoints for unrecognized and visitor admission flows (`POST /api/register/unrecognized`, `POST /api/register/visitor`).
- [x] Connect unrecognized face events to librarian approval before entry.
- [x] Implement visitor registration flow fully tied to both entry and exit occupancy accounting.

### Phase 5: Dashboard and Analytics Alignment (COMPLETE)

- [x] Keep dashboard/analytics/logs pages subscribed to websocket-triggered refresh (`analytics_updated`).
- [x] Expose occupancy endpoints needed by frontend occupancy cards/trends.
- [x] Build occupancy-focused librarian/kiosk views (live count, utilization ratio, warning/full state).
- [x] Add contract-specific occupancy analytics endpoints (`daily-report`, `occupancy-trends`) or formalize equivalent existing endpoints.
- [x] Remove legacy entry-only assumptions in UI copy and behavior to match dual-camera occupancy model.
- [x] Add explicit UI for capacity alerts, acknowledgment, and manual override workflow.

### Phase 6: Integration and Reliability Validation (IN PROGRESS)

- [x] Add foundational tests for worker queue durability and realtime ingest resilience (`tests/test_worker_queue.py`, `tests/test_realtime_analytics_resilience.py`).
- [ ] Add automated end-to-end occupancy transition tests (entry/exit/capacity saturation/recovery).
- [ ] Add duplicate event replay tests against canonical ingest and `event_id` dedupe behavior.
- [ ] Add worker restart/failover tests for queue drain + profile/runtime re-sync.
- [ ] Add load tests for burst ingestion (for example 100 events/minute) with occupancy and websocket latency assertions.
- [ ] Add rollout verification checks (migrations applied, scheduler running, websocket healthy, dual workers connected).

### Execution Order From Here

1. Complete the remaining visitor lifecycle gap so visitor admissions are reconciled cleanly through both entry and exit occupancy accounting.
2. Align frontend dashboards/kiosk with the occupancy-first dual-camera architecture.
3. Add explicit UI for capacity alerts, acknowledgement, and manual override workflows.
4. Finish end-to-end, duplicate replay, failover, and load validation before production rollout.

---

## 14) Session Changes Summary (May 2, 2026)

This session completed **Phase 2: Event-Driven Occupancy Tracking & Monitoring** and refactored the event schema for clarity and staff usability.

### Major Accomplishments

#### âœ… Phase 1 (Completed & Validated)
- **Dual-Camera Routing**: Entry Camera (ID=1) and Exit Camera (ID=2) via environment-variable-based worker launching
- **Worker Architecture**: `entry_worker.py` and `exit_worker.py` thin wrapper modules with WORKER_ROLE/WORKER_CAMERA_ID env vars
- **Event Idempotency**: Unique `event_id` (UUID v4) constraint ensures duplicate events ignored
- **Config-Driven Camera Sources**: Replaced hardcoded `cctv_stream_source` with `entry_cctv_stream_source` and `exit_cctv_stream_source` for explicit camera assignment

#### âœ… Phase 2 (NEW - Completed)

**Event-Driven Occupancy Model:**
- Real-time occupancy tracking via `daily_occupancy_state` table (one row per date, updated immediately on every event ingest)
- No schedule lag: occupancy state reflects current count **instantly** after event ingestion
- Entry worker queries current occupancy in < 100ms for capacity checks

**Dual-Layer Occupancy Architecture:**
- **Live State** (`daily_occupancy_state`): Updated event-driven, serves real-time kiosk queries
- **Historical Snapshots** (`occupancy_snapshots`): Generated every 5 minutes (configurable) by `OccupancySnapshotScheduler` background job for trend analysis

**Occupancy Service Implementation (`services/occupancy_service.py`):**
- `record_event(camera_id, captured_at)` â€” Event-driven state update on every event ingest
- `get_current_occupancy(capacity_limit)` â€” Real-time occupancy query returning occupancy_count, ratio, capacity_warning
- `create_snapshot(capacity_limit)` â€” Point-in-time snapshot generation
- `get_daily_state(target_date)` â€” Daily occupancy totals
- `get_history(target_date, limit)` â€” Historical snapshots for a date

**Occupancy Snapshot Scheduler (`services/occupancy_scheduler.py`):**
- `OccupancySnapshotScheduler` background thread running on configurable interval (default 300s)
- Generates snapshots capturing occupancy_count, daily_entries, daily_exits, capacity_warning at each snapshot time
- Started/stopped gracefully in `app/host_stack.py`

**WebSocket Integration:**
- `analytics_updated` event broadcasts on every recognition event ingest with payload: `{occupancy_count, daily_entries, daily_exits, capacity_warning}`
- Enables real-time librarian dashboard and kiosk updates

**Event Schema Refactoring (Recognition Events):**
- **Old Schema**: Used `camera_id` (1=entry, 2=exit) to persist camera metadata in recognition_events table
- **New Schema**: Uses `entered_at` (for entry events) and `exited_at` (for exit events) timestamps instead
- **Benefits**: Clearer intent, simpler staff CSV exports (one timestamp per row), eliminates ambiguous camera metadata
- **Migration**: Alembic migration `20260502_0007_replace_camera_columns_with_entry_exit_timestamps.py` safely backfills, drops old columns, adds new ones
- **Internal Note**: `camera_id` still passed in event payload for routing; not persisted

**Configuration Enhancements:**
- `max_library_capacity` (int, default 300) â€” Library capacity limit
- `occupancy_snapshot_interval_seconds` (int, default 300) â€” Snapshot frequency
- `occupancy_warning_threshold` (float, default 0.90) â€” Capacity warning ratio (0.90 = 90% of capacity)
- `entry_cctv_stream_source` and `exit_cctv_stream_source` â€” Config-driven camera stream IDs
- Removed redundant single `cctv_stream_source` field

**API Endpoints (Phase 2):**
- `GET /api/occupancy/current` â€” Real-time occupancy with capacity warning flag (< 100ms)
- `GET /api/occupancy/history?date=YYYY-MM-DD&limit=N` â€” Historical 5-min snapshots for a date
- `GET /api/occupancy/summary?date=YYYY-MM-DD` â€” Daily peak occupancy, total entries/exits, warning count

**Database Schema Changes:**
1. `daily_occupancy_state` table (NEW): One row per date; tracks cumulative entries, exits, and current occupancy
2. `occupancy_snapshots` table (NEW): Many rows per date (5-min intervals); historical occupancy time-series
3. `recognition_events` refactoring: Replaced `camera_id`/`station_id` columns with `entered_at`/`exited_at` timestamps
4. New indexes for performance: `idx_daily_occupancy_state_date`, `idx_occupancy_snapshots_timestamp`, `idx_occupancy_snapshots_date`, `idx_recognition_events_date_type`

**Nightly Reconciliation:**
- Scheduled at 00:00 UTC to detect entry/exit drift
- Alerts admin if drift > 5 people; allows manual adjustment via `POST /api/occupancy/adjust`

#### âš ï¸ Phase 3 (Partially Started)
- Skeleton `services/occupancy_alert_service.py` created but incomplete (no notification logic or routing)
- Capacity breach detection infrastructure ready for integration
- Pending: Librarian notification workflows, manual overrides, alert dashboard display

### Implementation Status by Component

| Component | Status | Notes |
|-----------|--------|-------|
| Entry Worker | âœ… Complete | WORKER_ROLE=entry, WORKER_CAMERA_ID=1, capacity checks integrated |
| Exit Worker | âœ… Complete | WORKER_ROLE=exit, WORKER_CAMERA_ID=2, no blocking exits |
| Recognition Service | âœ… Complete | Unchanged; works with both workers |
| Occupancy Service | âœ… Complete | Event-driven, real-time state + scheduled snapshots |
| OccupancySnapshotScheduler | âœ… Complete | Background job generating 5-min snapshots |
| Event Idempotency | âœ… Complete | Unique event_id constraint, tested |
| Occupancy API Endpoints | âœ… Complete | current, history, summary endpoints ready |
| WebSocket `analytics_updated` | âœ… Complete | Broadcasts occupancy payload on every event |
| Event Schema Refactoring | âœ… Complete | entered_at/exited_at replace camera_id; migration ready |
| Alert Service (Skeleton) | âš ï¸ Partial | Created; capacity breach logic incomplete |
| Librarian Alert UI | âŒ Not Started | Depends on alert service completion |
| Manual Override Workflows | âŒ Not Started | Phase 3 blocker |
| Staff CSV Export | ðŸ”„ Design Ready | Recommended flat schema; implementation pending |

### Next Immediate Tasks (Priority Order)

1. **Database Migrations**: Run `alembic upgrade head` to apply all Phase 2 migrations (enter/exit timestamps, daily_occupancy_state, occupancy_snapshots)
2. **Integration Testing**: Verify end-to-end occupancy updates, snapshot generation, and WebSocket broadcasts
3. **Occupancy Alert Service**: Complete capacity breach detection and notification routing
4. **Librarian Dashboard**: Display occupancy in real-time, show capacity warnings
5. **Staff CSV Export**: Implement `GET /api/recognition-events/export?format=csv&date=YYYY-MM-DD` endpoint

---

## 15) Future Enhancements (Post-MVP)

- **Multi-Location Support**: Extend system to handle multiple library branches with centralized dashboard.
- **Advanced Analytics**: Machine learning for occupancy predictions, anomaly detection.
- **Mobile App**: Mobile dashboard for librarians on-the-go (occupancy status, unrecognized alerts).
- **Integration with Library Management System**: Sync user profiles with existing LMS.
- **Facial Expression & Behavior Analysis**: Detect congestion stress, fatigue patterns.
- **Queue Management**: Estimated wait times, lane recommendations during peak hours.
- **Accessibility Features**: Voice alerts, larger displays, voice-controlled overrides.

---

## 16) Glossary

| Term | Definition |
|---|---|
| **Entry Worker** | Process capturing frames from Camera 1 (library entrance) and making entry decisions. Launched with WORKER_ROLE=entry, WORKER_CAMERA_ID=1. |
| **Exit Worker** | Process capturing frames from Camera 2 (library exit) and logging exits. Launched with WORKER_ROLE=exit, WORKER_CAMERA_ID=2. |
| **Occupancy** | Real-time count of people inside library = `daily_entries - daily_exits`. Updated immediately on every event ingest (event-driven). |
| **Capacity** | Maximum number of people allowed inside library at once. Set via `max_library_capacity` config. |
| **Daily Occupancy State** | Single-row-per-date table (`daily_occupancy_state`) tracking cumulative entries, exits, and current occupancy. Updated atomically on every event. |
| **Occupancy Snapshot** | Point-in-time occupancy measurement taken every N seconds (default 5 min) and persisted to `occupancy_snapshots` table for historical analysis. |
| **OccupancySnapshotScheduler** | Background thread running on configurable interval, generating snapshots of current occupancy state. Enables historical trend analysis without querying individual events. |
| **Entered At** | Timestamp of entry event (entry events populate this field; exit events leave it NULL). Replaces `camera_id` column in recognition_events table. |
| **Exited At** | Timestamp of exit event (exit events populate this field; entry events leave it NULL). Replaces `camera_id` column in recognition_events table. |
| **Event ID** | Unique UUID generated by worker; ensures idempotent event ingestion. If duplicate event_id received, database constraint prevents duplicate insert. |
| **Enrolled Student** | User with SR Code and program; auto-entry on recognition. Stored as `user_type='enrolled'` in users table. |
| **Visitor** | Non-student guest; requires librarian registration before entry. Stored as `user_type='visitor'`. |
| **Unrecognized** | Person not in profile database; triggers librarian alert + registration prompt. Stored as `user_type='unrecognized'` until registered. |
| **Drift** | Discrepancy between entry count and exit count for a given day. Nightly reconciliation detects drift > 5 people and alerts admin. |
| **Profile Version** | Incremented by API whenever user profiles or embeddings change; workers sync on change. |
| **Settings Version** | Incremented by API whenever recognition thresholds or policies change; workers sync on change. |
| **Analytics Updated** | WebSocket event broadcast immediately after every entry/exit event is ingested. Payload includes `occupancy_count`, `daily_entries`, `daily_exits`, `capacity_warning`. |
| **Capacity Warning Threshold** | Ratio (default 0.90) at which occupancy is considered "warning" state. Set via `occupancy_warning_threshold` config. |

