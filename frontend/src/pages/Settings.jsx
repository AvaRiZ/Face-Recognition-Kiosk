import React from 'react';
import { fetchJson } from '../api.js';
import { confirmAction, getErrorMessage, showError, showSuccess } from '../alerts.js';
import { useSession } from '../contexts.jsx';

const DEFAULT_BOUNDS = {
  max_occupancy: { min: 50, max: 2000 },
  vector_index_top_k: { min: 1, max: 100 },
  threshold: { min: 0.1, max: 0.95 },
  quality_threshold: { min: 0.1, max: 0.95 },
  recognition_confidence_threshold: { min: 0.1, max: 0.99 },
  occupancy_warning_threshold: { min: 0.5, max: 0.99 },
  occupancy_snapshot_interval_seconds: { min: 60, max: 3600 },
  face_snapshot_retention_days: { min: 1, max: 365 },
  recognition_event_retention_days: { min: 1, max: 3650 }
};

function asFixedNumber(value, digits) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return '-';
  return parsed.toFixed(digits);
}

function normalizeRolePermissions(role, apiPermissions) {
  if (apiPermissions && typeof apiPermissions === 'object') {
    return apiPermissions;
  }
  const roleName = String(role || '').toLowerCase();
  return {
    can_edit_thresholds: roleName === 'super_admin',
    can_edit_operational: roleName === 'super_admin' || roleName === 'library_admin',
    can_manage_advanced_ops: roleName === 'super_admin',
    can_view_audit: roleName === 'super_admin' || roleName === 'library_admin',
    can_save: roleName === 'super_admin' || roleName === 'library_admin'
  };
}

function roleSummary(role) {
  if (role === 'super_admin') return 'Full control over all system settings and operations.';
  if (role === 'library_admin') return 'Manage capacity, warning thresholds, and candidate search depth.';
  return 'Read-only access to monitor system configuration.';
}

function RoleBadge({ role }) {
  const map = {
    super_admin: { label: 'Super Admin', cls: 'bg-danger' },
    library_admin: { label: 'Library Admin', cls: 'bg-primary' }
  };
  const cfg = map[role] || { label: 'Viewer', cls: 'bg-secondary' };
  return <span className={`badge ${cfg.cls} ms-2`}>{cfg.label}</span>;
}

function StatCard({ value, label, accent }) {
  return (
    <div
      className="rounded-3 p-3 d-flex flex-column align-items-center justify-content-center text-center h-100"
      style={{ background: 'var(--bs-tertiary-bg)', border: `2px solid ${accent || 'var(--bs-border-color)'}` }}
    >
      <div className="fw-bold fs-3 lh-1 mb-1" style={{ color: accent || 'inherit' }}>{value}</div>
      <div className="small text-muted">{label}</div>
    </div>
  );
}

function SliderField({ id, label, value, onChange, min, max, step = '0.01', disabled, helpText, displayValue }) {
  return (
    <div>
      <div className="d-flex justify-content-between align-items-center mb-1">
        <label htmlFor={id} className="form-label mb-0 fw-medium">{label}</label>
        <span
          className="badge rounded-pill px-2 py-1"
          style={{ background: 'var(--bs-primary)', color: '#fff', fontVariantNumeric: 'tabular-nums', minWidth: '3.5rem' }}
        >
          {displayValue ?? value}
        </span>
      </div>
      <input
        type="range"
        id={id}
        className="form-range"
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        value={value}
        onChange={onChange}
      />
      {helpText && <div className="form-text">{helpText}</div>}
    </div>
  );
}

function NumberField({ id, label, value, onChange, min, max, step = '1', disabled, helpText }) {
  return (
    <div>
      <label htmlFor={id} className="form-label fw-medium">{label}</label>
      <input
        type="number"
        id={id}
        className="form-control"
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        value={value}
        onChange={onChange}
      />
      {helpText && <div className="form-text">{helpText}</div>}
    </div>
  );
}

function TextField({ id, label, value, onChange, disabled, placeholder, helpText }) {
  return (
    <div>
      <label htmlFor={id} className="form-label fw-medium">{label}</label>
      <input
        type="text"
        id={id}
        className="form-control font-monospace"
        disabled={disabled}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
      />
      {helpText && <div className="form-text">{helpText}</div>}
    </div>
  );
}

/* ── Tab definitions ── */
const TAB_OVERVIEW = 'overview';
const TAB_OCCUPANCY = 'occupancy';
const TAB_RECOGNITION = 'recognition';
const TAB_ADVANCED = 'advanced';
const TAB_AUDIT = 'audit';

export default function SettingsPage() {
  const { session } = useSession();
  const role = String(session?.role || '').toLowerCase();

  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [loadError, setLoadError] = React.useState('');
  const [activeTab, setActiveTab] = React.useState(TAB_OVERVIEW);

  const [threshold, setThreshold] = React.useState('0.3');
  const [qualityThreshold, setQualityThreshold] = React.useState('0.2');
  const [recognitionConfidenceThreshold, setRecognitionConfidenceThreshold] = React.useState('0.72');
  const [vectorIndexTopK, setVectorIndexTopK] = React.useState('20');
  const [maxOccupancy, setMaxOccupancy] = React.useState('300');
  const [occupancyWarningThreshold, setOccupancyWarningThreshold] = React.useState('0.9');
  const [occupancySnapshotIntervalSeconds, setOccupancySnapshotIntervalSeconds] = React.useState('300');
  const [faceSnapshotRetentionDays, setFaceSnapshotRetentionDays] = React.useState('30');
  const [recognitionEventRetentionDays, setRecognitionEventRetentionDays] = React.useState('365');
  const [entryCctvStreamSource, setEntryCctvStreamSource] = React.useState('');
  const [exitCctvStreamSource, setExitCctvStreamSource] = React.useState('');

  const permissions = React.useMemo(
    () => normalizeRolePermissions(role, data?.permissions),
    [role, data?.permissions]
  );
  const bounds = data?.bounds || DEFAULT_BOUNDS;

  const applySettingsPayload = React.useCallback((payload) => {
    setData(payload || null);
    setThreshold(String(payload?.threshold ?? '0.3'));
    setQualityThreshold(String(payload?.quality_threshold ?? '0.2'));
    setRecognitionConfidenceThreshold(String(payload?.recognition_confidence_threshold ?? '0.72'));
    setVectorIndexTopK(String(payload?.vector_index_top_k ?? '20'));
    setMaxOccupancy(String(payload?.max_occupancy ?? '300'));
    setOccupancyWarningThreshold(String(payload?.occupancy_warning_threshold ?? '0.9'));
    setOccupancySnapshotIntervalSeconds(String(payload?.occupancy_snapshot_interval_seconds ?? '300'));
    setFaceSnapshotRetentionDays(String(payload?.face_snapshot_retention_days ?? '30'));
    setRecognitionEventRetentionDays(String(payload?.recognition_event_retention_days ?? '365'));
    setEntryCctvStreamSource(String(payload?.entry_cctv_stream_source ?? ''));
    setExitCctvStreamSource(String(payload?.exit_cctv_stream_source ?? ''));
  }, []);

  const loadSettings = React.useCallback(async () => {
    setLoading(true);
    setLoadError('');
    try {
      const response = await fetchJson('/api/settings/recognition');
      applySettingsPayload(response);
    } catch (error) {
      setData(null);
      setLoadError(getErrorMessage(error));
    } finally {
      setLoading(false);
    }
  }, [applySettingsPayload]);

  React.useEffect(() => { loadSettings(); }, [loadSettings]);

  async function handleSubmit(ev) {
    ev.preventDefault();
    if (!permissions.can_save) {
      await showError('Read-Only Access', 'Your role does not have permission to update settings.');
      return;
    }
    const payload = {};
    if (permissions.can_edit_operational) {
      payload.max_occupancy = maxOccupancy;
      payload.vector_index_top_k = vectorIndexTopK;
      payload.occupancy_warning_threshold = occupancyWarningThreshold;
      payload.occupancy_snapshot_interval_seconds = occupancySnapshotIntervalSeconds;
    }
    if (permissions.can_edit_thresholds) {
      payload.threshold = threshold;
      payload.quality_threshold = qualityThreshold;
      payload.recognition_confidence_threshold = recognitionConfidenceThreshold;
    }
    if (permissions.can_manage_advanced_ops) {
      payload.face_snapshot_retention_days = faceSnapshotRetentionDays;
      payload.recognition_event_retention_days = recognitionEventRetentionDays;
      payload.entry_cctv_stream_source = entryCctvStreamSource;
      payload.exit_cctv_stream_source = exitCctvStreamSource;
    }
    setSaving(true);
    try {
      const response = await fetchJson('/api/settings/recognition', {
        method: 'POST',
        body: JSON.stringify(payload)
      });
      applySettingsPayload(response);
      await showSuccess('Settings Saved', 'Recognition settings were updated successfully.');
    } catch (error) {
      await showError('Save Failed', getErrorMessage(error));
    } finally {
      setSaving(false);
    }
  }

  async function resetDatabase() {
    const ok1 = await confirmAction({ title: 'Reset Database?', text: 'This will delete all registered users and face data. This action cannot be undone.', confirmButtonText: 'Continue', confirmButtonColor: '#dc3545' });
    if (!ok1) return;
    const ok2 = await confirmAction({ title: 'Final Confirmation', text: 'Confirm again to permanently reset the database.', confirmButtonText: 'Confirm Reset', confirmButtonColor: '#dc3545' });
    if (!ok2) return;
    try {
      await fetchJson('/api/reset_database', { method: 'POST' });
      await showSuccess('Completed', 'Database reset successfully. The system will restart.');
      window.location.reload();
    } catch (error) {
      await showError('Request Failed', getErrorMessage(error));
    }
  }

  async function clearRecognitionLog() {
    const ok1 = await confirmAction({ title: 'Clear Recognition Events?', text: 'This will clear all recognition history. This action cannot be undone.', confirmButtonText: 'Continue', confirmButtonColor: '#fd7e14' });
    if (!ok1) return;
    const ok2 = await confirmAction({ title: 'Final Confirmation', text: 'Confirm again to permanently clear all recognition events.', confirmButtonText: 'Confirm Clear', confirmButtonColor: '#fd7e14' });
    if (!ok2) return;
    try {
      await fetchJson('/api/clear_log', { method: 'POST' });
      await showSuccess('Completed', 'Recognition events cleared successfully.');
      window.location.reload();
    } catch (error) {
      await showError('Request Failed', getErrorMessage(error));
    }
  }

  /* ── Derived flags ── */
  const canEditThresholds = Boolean(permissions.can_edit_thresholds);
  const canEditOperational = Boolean(permissions.can_edit_operational);
  const canSave = Boolean(permissions.can_save);
  const canManageAdvancedOps = Boolean(permissions.can_manage_advanced_ops);
  const canViewAudit = Boolean(permissions.can_view_audit);
  const auditRows = Array.isArray(data?.audit_rows) ? data.audit_rows : [];
  const lastChange = data?.last_change || null;

  /* ── Build tab list based on permissions ── */
  const tabs = [
    { id: TAB_OVERVIEW, label: 'Overview', icon: 'bi-speedometer2' },
    { id: TAB_OCCUPANCY, label: 'Occupancy', icon: 'bi-people-fill' },
    { id: TAB_RECOGNITION, label: 'Recognition', icon: 'bi-eye-fill' },
    canManageAdvancedOps && { id: TAB_ADVANCED, label: 'Advanced', icon: 'bi-gear-wide-connected' },
    canViewAudit && { id: TAB_AUDIT, label: 'Audit Log', icon: 'bi-journal-text' }
  ].filter(Boolean);

  /* ── Loading / error states ── */
  if (loading) {
    return (
      <div className="d-flex justify-content-center align-items-center" style={{ minHeight: '30vh' }}>
        <div className="spinner-border text-primary" role="status" />
      </div>
    );
  }

  if (loadError) {
    return (
      <section className="section">
        <div className="pagetitle"><h1>System Settings</h1></div>
        <div className="alert alert-danger mb-3" role="alert">Failed to load settings: {loadError}</div>
        <button type="button" className="btn btn-outline-primary" onClick={loadSettings}>Retry</button>
      </section>
    );
  }

  /* ── Save footer (shared across form tabs) ── */
  const SaveFooter = () => (
    <div className="d-flex align-items-center gap-2 pt-3 mt-2" style={{ borderTop: '1px solid var(--bs-border-color)' }}>
      <button type="submit" className="btn btn-primary px-4" disabled={!canSave || saving}>
        {saving
          ? <><span className="spinner-border spinner-border-sm me-2" />Saving…</>
          : <><i className="bi bi-floppy me-2" />Save Settings</>}
      </button>
      {!canSave && <span className="badge bg-secondary"><i className="bi bi-lock me-1" />Read-only role</span>}
    </div>
  );

  return (
    <section className="section">
      {/* ── Page header ── */}
      <div className="pagetitle d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3">
        <div>
          <h1 className="mb-0">
            System Settings
            <RoleBadge role={role} />
          </h1>
          <p className="text-muted small mb-0 mt-1">{roleSummary(role)}</p>
        </div>
      </div>

      {/* ── Tab nav ── */}
      <ul className="nav nav-tabs mb-0" style={{ borderBottom: '2px solid var(--bs-border-color)' }}>
        {tabs.map(tab => (
          <li className="nav-item" key={tab.id}>
            <button
              type="button"
              className={`nav-link d-flex align-items-center gap-2${activeTab === tab.id ? ' active fw-semibold' : ''}`}
              onClick={() => setActiveTab(tab.id)}
            >
              <i className={`bi ${tab.icon}`} />
              <span className="d-none d-sm-inline">{tab.label}</span>
            </button>
          </li>
        ))}
      </ul>

      {/* ── Tab panels ── */}
      <div className="card rounded-top-0" style={{ borderTop: 'none' }}>
        <div className="card-body p-4">

          {/* ── Overview tab ── */}
          {activeTab === TAB_OVERVIEW && (
            <div>
              <h6 className="text-uppercase text-muted fw-semibold mb-3" style={{ letterSpacing: '.07em', fontSize: '.7rem' }}>
                Live Snapshot
              </h6>
              <div className="row g-3 mb-4">
                <div className="col-6 col-md-3">
                  <StatCard value={data?.user_count ?? 0} label="Registered Users" accent="#0d6efd" />
                </div>
                <div className="col-6 col-md-3">
                  <StatCard value={asFixedNumber(threshold, 3)} label="Recognition Threshold" accent="#198754" />
                </div>
                <div className="col-6 col-md-3">
                  <StatCard value={asFixedNumber(qualityThreshold, 2)} label="Min Face Quality" accent="#fd7e14" />
                </div>
                <div className="col-6 col-md-3">
                  <StatCard value={maxOccupancy} label="Max Occupancy" accent="#6f42c1" />
                </div>
              </div>

              <h6 className="text-uppercase text-muted fw-semibold mb-3" style={{ letterSpacing: '.07em', fontSize: '.7rem' }}>
                Access Summary
              </h6>
              <div className="row g-2">
                {[
                  { label: 'Edit Thresholds', ok: canEditThresholds },
                  { label: 'Edit Capacity', ok: canEditOperational },
                  { label: 'Advanced Ops', ok: canManageAdvancedOps },
                  { label: 'View Audit', ok: canViewAudit },
                  { label: 'Save Changes', ok: canSave }
                ].map(({ label, ok }) => (
                  <div key={label} className="col-auto">
                    <span className={`badge d-flex align-items-center gap-1 px-3 py-2 ${ok ? 'bg-success-subtle text-success border border-success-subtle' : 'bg-secondary-subtle text-secondary border border-secondary-subtle'}`}>
                      <i className={`bi ${ok ? 'bi-check-circle-fill' : 'bi-dash-circle'}`} />
                      {label}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Occupancy tab ── */}
          {activeTab === TAB_OCCUPANCY && (
            <form onSubmit={handleSubmit}>
              <div className="row g-4">
                <div className="col-12 col-lg-6">
                  <NumberField
                    id="max_occupancy"
                    label="Max Library Occupancy"
                    value={maxOccupancy}
                    onChange={ev => setMaxOccupancy(ev.target.value)}
                    min={bounds.max_occupancy?.min ?? DEFAULT_BOUNDS.max_occupancy.min}
                    max={bounds.max_occupancy?.max ?? DEFAULT_BOUNDS.max_occupancy.max}
                    disabled={!canEditOperational}
                    helpText={`Range: ${bounds.max_occupancy?.min ?? DEFAULT_BOUNDS.max_occupancy.min}–${bounds.max_occupancy?.max ?? DEFAULT_BOUNDS.max_occupancy.max}. Used by occupancy analytics and alerts.`}
                  />
                </div>

                <div className="col-12 col-lg-6">
                  <NumberField
                    id="occupancy_snapshot_interval_seconds"
                    label="Snapshot Interval (seconds)"
                    value={occupancySnapshotIntervalSeconds}
                    onChange={ev => setOccupancySnapshotIntervalSeconds(ev.target.value)}
                    min={bounds.occupancy_snapshot_interval_seconds?.min ?? DEFAULT_BOUNDS.occupancy_snapshot_interval_seconds.min}
                    max={bounds.occupancy_snapshot_interval_seconds?.max ?? DEFAULT_BOUNDS.occupancy_snapshot_interval_seconds.max}
                    disabled={!canEditOperational}
                    helpText={`Range: ${bounds.occupancy_snapshot_interval_seconds?.min ?? DEFAULT_BOUNDS.occupancy_snapshot_interval_seconds.min}–${bounds.occupancy_snapshot_interval_seconds?.max ?? DEFAULT_BOUNDS.occupancy_snapshot_interval_seconds.max}.`}
                  />
                </div>

                <div className="col-12">
                  <SliderField
                    id="occupancy_warning_threshold"
                    label="Occupancy Warning Threshold"
                    value={occupancyWarningThreshold}
                    displayValue={asFixedNumber(occupancyWarningThreshold, 2)}
                    onChange={ev => setOccupancyWarningThreshold(ev.target.value)}
                    min={bounds.occupancy_warning_threshold?.min ?? DEFAULT_BOUNDS.occupancy_warning_threshold.min}
                    max={bounds.occupancy_warning_threshold?.max ?? DEFAULT_BOUNDS.occupancy_warning_threshold.max}
                    disabled={!canEditOperational}
                    helpText="Alerts fire when occupancy reaches this ratio of max capacity."
                  />
                </div>
              </div>
              <SaveFooter />
            </form>
          )}

          {/* ── Recognition tab ── */}
          {activeTab === TAB_RECOGNITION && (
            <form onSubmit={handleSubmit}>
              <div className="row g-4">
                <div className="col-12 col-lg-6">
                  <NumberField
                    id="vector_index_top_k"
                    label="Vector Index Top-K"
                    value={vectorIndexTopK}
                    onChange={ev => setVectorIndexTopK(ev.target.value)}
                    min={bounds.vector_index_top_k?.min ?? DEFAULT_BOUNDS.vector_index_top_k.min}
                    max={bounds.vector_index_top_k?.max ?? DEFAULT_BOUNDS.vector_index_top_k.max}
                    disabled={!canEditOperational}
                    helpText={`Range: ${bounds.vector_index_top_k?.min ?? DEFAULT_BOUNDS.vector_index_top_k.min}–${bounds.vector_index_top_k?.max ?? DEFAULT_BOUNDS.vector_index_top_k.max}. Candidate embeddings checked per model.`}
                  />
                </div>

                <div className="col-12">
                  <hr className="my-1" />
                  <p className="small text-muted mb-3 mt-2">Threshold sliders below require <strong>Super Admin</strong> access.</p>
                </div>

                <div className="col-12">
                  <SliderField
                    id="threshold"
                    label="Recognition Threshold"
                    value={threshold}
                    displayValue={asFixedNumber(threshold, 3)}
                    onChange={ev => setThreshold(ev.target.value)}
                    min={bounds.threshold?.min ?? DEFAULT_BOUNDS.threshold.min}
                    max={bounds.threshold?.max ?? DEFAULT_BOUNDS.threshold.max}
                    disabled={!canEditThresholds}
                    helpText="Higher = stricter (requires higher confidence). Lower = more lenient."
                  />
                </div>

                <div className="col-12">
                  <SliderField
                    id="quality_threshold"
                    label="Minimum Face Quality"
                    value={qualityThreshold}
                    displayValue={asFixedNumber(qualityThreshold, 2)}
                    onChange={ev => setQualityThreshold(ev.target.value)}
                    min={bounds.quality_threshold?.min ?? DEFAULT_BOUNDS.quality_threshold.min}
                    max={bounds.quality_threshold?.max ?? DEFAULT_BOUNDS.quality_threshold.max}
                    disabled={!canEditThresholds}
                  />
                </div>

                <div className="col-12">
                  <SliderField
                    id="recognition_confidence_threshold"
                    label="Recognition Confidence Gate"
                    value={recognitionConfidenceThreshold}
                    displayValue={asFixedNumber(recognitionConfidenceThreshold, 3)}
                    onChange={ev => setRecognitionConfidenceThreshold(ev.target.value)}
                    min={bounds.recognition_confidence_threshold?.min ?? DEFAULT_BOUNDS.recognition_confidence_threshold.min}
                    max={bounds.recognition_confidence_threshold?.max ?? DEFAULT_BOUNDS.recognition_confidence_threshold.max}
                    disabled={!canEditThresholds}
                    helpText="Minimum confidence required before logging an entry or exit event."
                  />
                </div>
              </div>
              <SaveFooter />
            </form>
          )}

          {/* ── Advanced tab ── */}
          {activeTab === TAB_ADVANCED && canManageAdvancedOps && (
            <form onSubmit={handleSubmit}>
              <h6 className="text-uppercase text-muted fw-semibold mb-3" style={{ letterSpacing: '.07em', fontSize: '.7rem' }}>
                Data Retention
              </h6>
              <div className="row g-4 mb-4">
                <div className="col-12 col-lg-6">
                  <NumberField
                    id="face_snapshot_retention_days"
                    label="Face Snapshot Retention (days)"
                    value={faceSnapshotRetentionDays}
                    onChange={ev => setFaceSnapshotRetentionDays(ev.target.value)}
                    min={bounds.face_snapshot_retention_days?.min ?? DEFAULT_BOUNDS.face_snapshot_retention_days.min}
                    max={bounds.face_snapshot_retention_days?.max ?? DEFAULT_BOUNDS.face_snapshot_retention_days.max}
                    disabled={!canManageAdvancedOps}
                  />
                </div>
                <div className="col-12 col-lg-6">
                  <NumberField
                    id="recognition_event_retention_days"
                    label="Recognition Event Retention (days)"
                    value={recognitionEventRetentionDays}
                    onChange={ev => setRecognitionEventRetentionDays(ev.target.value)}
                    min={bounds.recognition_event_retention_days?.min ?? DEFAULT_BOUNDS.recognition_event_retention_days.min}
                    max={bounds.recognition_event_retention_days?.max ?? DEFAULT_BOUNDS.recognition_event_retention_days.max}
                    disabled={!canManageAdvancedOps}
                    helpText="Older snapshots and event logs are purged automatically."
                  />
                </div>
              </div>

              <h6 className="text-uppercase text-muted fw-semibold mb-3" style={{ letterSpacing: '.07em', fontSize: '.7rem' }}>
                Camera Sources
              </h6>
              <div className="row g-4 mb-4">
                <div className="col-12 col-lg-6">
                  <TextField
                    id="entry_cctv_stream_source"
                    label="Entry Camera Stream"
                    value={entryCctvStreamSource}
                    onChange={ev => setEntryCctvStreamSource(ev.target.value)}
                    disabled={!canManageAdvancedOps}
                    placeholder="0, 1, or rtsp://…"
                  />
                </div>
                <div className="col-12 col-lg-6">
                  <TextField
                    id="exit_cctv_stream_source"
                    label="Exit Camera Stream"
                    value={exitCctvStreamSource}
                    onChange={ev => setExitCctvStreamSource(ev.target.value)}
                    disabled={!canManageAdvancedOps}
                    placeholder="0, 1, or rtsp://…"
                    helpText="Changes take effect after restarting the entry/exit workers."
                  />
                </div>
              </div>

              <SaveFooter />

              {/* Danger zone */}
              <div
                className="mt-4 p-3 rounded-3"
                style={{ background: 'var(--bs-danger-bg-subtle)', border: '1px solid var(--bs-danger-border-subtle)' }}
              >
                <h6 className="text-danger fw-semibold mb-1">
                  <i className="bi bi-exclamation-triangle-fill me-2" />Danger Zone
                </h6>
                <p className="small text-muted mb-3">
                  These operations are <strong>irreversible</strong> and require two confirmation steps.
                </p>
                <div className="d-flex gap-2 flex-wrap">
                  <button onClick={resetDatabase} className="btn btn-danger btn-sm" type="button">
                    <i className="bi bi-trash3 me-1" />Reset Database
                  </button>
                  <button onClick={clearRecognitionLog} className="btn btn-warning btn-sm" type="button">
                    <i className="bi bi-eraser me-1" />Clear Recognition Events
                  </button>
                </div>
              </div>
            </form>
          )}

          {/* ── Audit tab ── */}
          {activeTab === TAB_AUDIT && canViewAudit && (
            <div>
              {lastChange ? (
                <div
                  className="rounded-3 p-3 mb-4 d-flex gap-3 align-items-start"
                  style={{ background: 'var(--bs-info-bg-subtle)', border: '1px solid var(--bs-info-border-subtle)' }}
                >
                  <i className="bi bi-clock-history text-info fs-5 mt-1 flex-shrink-0" />
                  <div>
                    <div className="fw-semibold small">Last Change</div>
                    <div className="small text-muted">
                      {lastChange.username || 'Unknown user'} &middot; {lastChange.timestamp || 'Unknown time'}
                    </div>
                    <div className="small mt-1">{lastChange.target || 'No detail recorded.'}</div>
                  </div>
                </div>
              ) : (
                <div className="text-muted small mb-4">No settings changes have been recorded yet.</div>
              )}

              <div className="table-responsive">
                <table className="table table-sm table-hover align-middle">
                  <thead className="table-light">
                    <tr>
                      <th style={{ width: '5rem' }}>ID</th>
                      <th>Changed By</th>
                      <th>Timestamp</th>
                      <th>Details</th>
                    </tr>
                  </thead>
                  <tbody>
                    {auditRows.length ? auditRows.map(row => (
                      <tr key={row.audit_id}>
                        <td className="text-muted small font-monospace">{row.audit_id}</td>
                        <td>
                          <i className="bi bi-person-circle me-1 text-muted" />
                          {row.username || '—'}
                        </td>
                        <td className="small text-muted">{row.timestamp || '—'}</td>
                        <td className="small">{row.target || '—'}</td>
                      </tr>
                    )) : (
                      <tr>
                        <td colSpan="4" className="text-center text-muted py-4">
                          <i className="bi bi-journal-x d-block fs-3 mb-1 opacity-25" />
                          No audit entries found.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

        </div>
      </div>
    </section>
  );
}