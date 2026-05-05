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
  if (role === 'super_admin') {
    return 'Full control: recognition thresholds, capacity settings, retention, and hardware configuration.';
  }
  if (role === 'library_admin') {
    return 'Operational control: update capacity, warning thresholds, and candidate search depth.';
  }
  return 'Read-only view: monitor thresholds, occupancy settings, and recent configuration changes.';
}

export default function SettingsPage() {
  const { session } = useSession();
  const role = String(session?.role || '').toLowerCase();

  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [loadError, setLoadError] = React.useState('');

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

  React.useEffect(() => {
    loadSettings();
  }, [loadSettings]);

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
    const firstConfirmation = await confirmAction({
      title: 'Reset Database?',
      text: 'This will delete all registered users and face data. This action cannot be undone.',
      confirmButtonText: 'Continue',
      confirmButtonColor: '#dc3545'
    });
    if (!firstConfirmation) return;

    const secondConfirmation = await confirmAction({
      title: 'Final Confirmation',
      text: 'Confirm again to permanently reset the database.',
      confirmButtonText: 'Confirm Reset',
      confirmButtonColor: '#dc3545'
    });
    if (!secondConfirmation) return;

    try {
      await fetchJson('/api/reset_database', { method: 'POST' });
      await showSuccess('Completed', 'Database reset successfully. The system will restart.');
      window.location.reload();
    } catch (error) {
      await showError('Request Failed', getErrorMessage(error));
    }
  }

  async function clearRecognitionLog() {
    const firstConfirmation = await confirmAction({
      title: 'Clear Recognition Events?',
      text: 'This will clear all recognition history. This action cannot be undone.',
      confirmButtonText: 'Continue',
      confirmButtonColor: '#fd7e14'
    });
    if (!firstConfirmation) return;

    const secondConfirmation = await confirmAction({
      title: 'Final Confirmation',
      text: 'Confirm again to permanently clear all recognition events.',
      confirmButtonText: 'Confirm Clear',
      confirmButtonColor: '#fd7e14'
    });
    if (!secondConfirmation) return;

    try {
      await fetchJson('/api/clear_log', { method: 'POST' });
      await showSuccess('Completed', 'Recognition events cleared successfully.');
      window.location.reload();
    } catch (error) {
      await showError('Request Failed', getErrorMessage(error));
    }
  }

  if (loading) {
    return (
      <div className="d-flex justify-content-center align-items-center" style={{ minHeight: '30vh' }}>
        <div className="spinner-border text-primary" role="status"></div>
      </div>
    );
  }

  if (loadError) {
    return (
      <section className="section">
        <div className="pagetitle">
          <h1>System Settings</h1>
        </div>
        <div className="alert alert-danger mb-3" role="alert">
          Failed to load settings: {loadError}
        </div>
        <button type="button" className="btn btn-outline-primary" onClick={loadSettings}>
          Retry
        </button>
      </section>
    );
  }

  const canEditThresholds = Boolean(permissions.can_edit_thresholds);
  const canEditOperational = Boolean(permissions.can_edit_operational);
  const canSave = Boolean(permissions.can_save);
  const canManageAdvancedOps = Boolean(permissions.can_manage_advanced_ops);
  const canViewAudit = Boolean(permissions.can_view_audit);
  const auditRows = Array.isArray(data?.audit_rows) ? data.audit_rows : [];
  const lastChange = data?.last_change || null;

  return (
    <section className="section">
      <div className="pagetitle">
        <h1>System Settings</h1>
        <p className="text-muted mb-0">{roleSummary(role)}</p>
      </div>

      <div className="row g-3">
        <div className="col-12">
          <div className="card">
            <div className="card-body">
              <h5 className="card-title">System Statistics</h5>
              <div className="row g-3">
                <div className="col-md-3">
                  <div className="border rounded p-3 text-center h-100">
                    <div className="h3 mb-1">{data?.user_count ?? 0}</div>
                    <small className="text-muted">Registered Users</small>
                  </div>
                </div>
                <div className="col-md-3">
                  <div className="border rounded p-3 text-center h-100">
                    <div className="h3 mb-1">{asFixedNumber(threshold, 3)}</div>
                    <small className="text-muted">Current Threshold</small>
                  </div>
                </div>
                <div className="col-md-3">
                  <div className="border rounded p-3 text-center h-100">
                    <div className="h3 mb-1">{asFixedNumber(qualityThreshold, 2)}</div>
                    <small className="text-muted">Min Face Quality</small>
                  </div>
                </div>
                <div className="col-md-3">
                  <div className="border rounded p-3 text-center h-100">
                    <div className="h3 mb-1">{maxOccupancy}</div>
                    <small className="text-muted">Max Occupancy</small>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="col-12">
          <form className="row g-3" onSubmit={handleSubmit}>
            <div className="col-12">
              <div className="card">
                <div className="card-body">
                  <h5 className="card-title">Occupancy & Capacity</h5>
                  <div className="row g-4">
                    <div className="col-12">
                      <label htmlFor="max_occupancy" className="form-label">
                        Max Library Occupancy
                      </label>
                      <input
                        type="number"
                        id="max_occupancy"
                        name="max_occupancy"
                        className="form-control"
                        min={bounds.max_occupancy?.min ?? DEFAULT_BOUNDS.max_occupancy.min}
                        max={bounds.max_occupancy?.max ?? DEFAULT_BOUNDS.max_occupancy.max}
                        step="1"
                        disabled={!canEditOperational}
                        value={maxOccupancy}
                        onChange={(ev) => setMaxOccupancy(ev.target.value)}
                      />
                      <div className="form-text">
                        Range: {bounds.max_occupancy?.min ?? DEFAULT_BOUNDS.max_occupancy.min} to{' '}
                        {bounds.max_occupancy?.max ?? DEFAULT_BOUNDS.max_occupancy.max}. Used by occupancy analytics and
                        alerts.
                      </div>
                    </div>

                    <div className="col-12">
                      <label htmlFor="occupancy_warning_threshold" className="form-label">
                        Occupancy Warning Threshold:{' '}
                        <span className="badge bg-light text-dark">{asFixedNumber(occupancyWarningThreshold, 2)}</span>
                      </label>
                      <input
                        type="range"
                        id="occupancy_warning_threshold"
                        name="occupancy_warning_threshold"
                        className="form-range"
                        min={bounds.occupancy_warning_threshold?.min ?? DEFAULT_BOUNDS.occupancy_warning_threshold.min}
                        max={bounds.occupancy_warning_threshold?.max ?? DEFAULT_BOUNDS.occupancy_warning_threshold.max}
                        step="0.01"
                        disabled={!canEditOperational}
                        value={occupancyWarningThreshold}
                        onChange={(ev) => setOccupancyWarningThreshold(ev.target.value)}
                      />
                      <div className="form-text">
                        Alerts fire when occupancy reaches this ratio of max capacity.
                      </div>
                    </div>

                    <div className="col-12">
                      <label htmlFor="occupancy_snapshot_interval_seconds" className="form-label">
                        Snapshot Interval (seconds)
                      </label>
                      <input
                        type="number"
                        id="occupancy_snapshot_interval_seconds"
                        name="occupancy_snapshot_interval_seconds"
                        className="form-control"
                        min={
                          bounds.occupancy_snapshot_interval_seconds?.min ??
                          DEFAULT_BOUNDS.occupancy_snapshot_interval_seconds.min
                        }
                        max={
                          bounds.occupancy_snapshot_interval_seconds?.max ??
                          DEFAULT_BOUNDS.occupancy_snapshot_interval_seconds.max
                        }
                        step="1"
                        disabled={!canEditOperational}
                        value={occupancySnapshotIntervalSeconds}
                        onChange={(ev) => setOccupancySnapshotIntervalSeconds(ev.target.value)}
                      />
                      <div className="form-text">
                        Range: {bounds.occupancy_snapshot_interval_seconds?.min ?? DEFAULT_BOUNDS.occupancy_snapshot_interval_seconds.min}
                        {' '}to {bounds.occupancy_snapshot_interval_seconds?.max ?? DEFAULT_BOUNDS.occupancy_snapshot_interval_seconds.max}.
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div className="col-12">
              <div className="card">
                <div className="card-body">
                  <h5 className="card-title">Recognition Tuning</h5>
                  <div className="row g-4">
                    <div className="col-12">
                      <label htmlFor="vector_index_top_k" className="form-label">
                        Vector Index Top-K
                      </label>
                      <input
                        type="number"
                        id="vector_index_top_k"
                        name="vector_index_top_k"
                        className="form-control"
                        min={bounds.vector_index_top_k?.min ?? DEFAULT_BOUNDS.vector_index_top_k.min}
                        max={bounds.vector_index_top_k?.max ?? DEFAULT_BOUNDS.vector_index_top_k.max}
                        step="1"
                        disabled={!canEditOperational}
                        value={vectorIndexTopK}
                        onChange={(ev) => setVectorIndexTopK(ev.target.value)}
                      />
                      <div className="form-text">
                        Range: {bounds.vector_index_top_k?.min ?? DEFAULT_BOUNDS.vector_index_top_k.min} to{' '}
                        {bounds.vector_index_top_k?.max ?? DEFAULT_BOUNDS.vector_index_top_k.max}. Candidate embeddings
                        checked per model.
                      </div>
                    </div>

                    <div className="col-12">
                      <label htmlFor="threshold" className="form-label">
                        Recognition Threshold:{' '}
                        <span className="badge bg-light text-dark">{asFixedNumber(threshold, 3)}</span>
                      </label>
                      <input
                        type="range"
                        id="threshold"
                        name="threshold"
                        className="form-range"
                        min={bounds.threshold?.min ?? DEFAULT_BOUNDS.threshold.min}
                        max={bounds.threshold?.max ?? DEFAULT_BOUNDS.threshold.max}
                        step="0.01"
                        disabled={!canEditThresholds}
                        value={threshold}
                        onChange={(ev) => setThreshold(ev.target.value)}
                      />
                      <div className="form-text">
                        Higher values = stricter (requires higher confidence). Lower values = more lenient.
                      </div>
                    </div>

                    <div className="col-12">
                      <label htmlFor="quality_threshold" className="form-label">
                        Minimum Face Quality:{' '}
                        <span className="badge bg-light text-dark">{asFixedNumber(qualityThreshold, 2)}</span>
                      </label>
                      <input
                        type="range"
                        id="quality_threshold"
                        name="quality_threshold"
                        className="form-range"
                        min={bounds.quality_threshold?.min ?? DEFAULT_BOUNDS.quality_threshold.min}
                        max={bounds.quality_threshold?.max ?? DEFAULT_BOUNDS.quality_threshold.max}
                        step="0.01"
                        disabled={!canEditThresholds}
                        value={qualityThreshold}
                        onChange={(ev) => setQualityThreshold(ev.target.value)}
                      />
                    </div>

                    <div className="col-12">
                      <label htmlFor="recognition_confidence_threshold" className="form-label">
                        Recognition Confidence Gate:{' '}
                        <span className="badge bg-light text-dark">
                          {asFixedNumber(recognitionConfidenceThreshold, 3)}
                        </span>
                      </label>
                      <input
                        type="range"
                        id="recognition_confidence_threshold"
                        name="recognition_confidence_threshold"
                        className="form-range"
                        min={
                          bounds.recognition_confidence_threshold?.min ??
                          DEFAULT_BOUNDS.recognition_confidence_threshold.min
                        }
                        max={
                          bounds.recognition_confidence_threshold?.max ??
                          DEFAULT_BOUNDS.recognition_confidence_threshold.max
                        }
                        step="0.01"
                        disabled={!canEditThresholds}
                        value={recognitionConfidenceThreshold}
                        onChange={(ev) => setRecognitionConfidenceThreshold(ev.target.value)}
                      />
                      <div className="form-text">
                        Used to require higher confidence before logging an entry or exit.
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {canManageAdvancedOps ? (
              <div className="col-12">
                <div className="card">
                  <div className="card-body">
                    <h5 className="card-title">Data Retention</h5>
                    <div className="row g-4">
                      <div className="col-12">
                        <label htmlFor="face_snapshot_retention_days" className="form-label">
                          Face Snapshot Retention (days)
                        </label>
                        <input
                          type="number"
                          id="face_snapshot_retention_days"
                          name="face_snapshot_retention_days"
                          className="form-control"
                          min={
                            bounds.face_snapshot_retention_days?.min ??
                            DEFAULT_BOUNDS.face_snapshot_retention_days.min
                          }
                          max={
                            bounds.face_snapshot_retention_days?.max ??
                            DEFAULT_BOUNDS.face_snapshot_retention_days.max
                          }
                          step="1"
                          disabled={!canManageAdvancedOps}
                          value={faceSnapshotRetentionDays}
                          onChange={(ev) => setFaceSnapshotRetentionDays(ev.target.value)}
                        />
                      </div>

                      <div className="col-12">
                        <label htmlFor="recognition_event_retention_days" className="form-label">
                          Recognition Event Retention (days)
                        </label>
                        <input
                          type="number"
                          id="recognition_event_retention_days"
                          name="recognition_event_retention_days"
                          className="form-control"
                          min={
                            bounds.recognition_event_retention_days?.min ??
                            DEFAULT_BOUNDS.recognition_event_retention_days.min
                          }
                          max={
                            bounds.recognition_event_retention_days?.max ??
                            DEFAULT_BOUNDS.recognition_event_retention_days.max
                          }
                          step="1"
                          disabled={!canManageAdvancedOps}
                          value={recognitionEventRetentionDays}
                          onChange={(ev) => setRecognitionEventRetentionDays(ev.target.value)}
                        />
                        <div className="form-text">
                          Older snapshots and event logs are purged automatically on the host.
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            ) : null}

            {canManageAdvancedOps ? (
              <div className="col-12">
                <div className="card">
                  <div className="card-body">
                    <h5 className="card-title">Hardware & Camera Sources</h5>
                    <div className="row g-4">
                      <div className="col-12">
                        <label htmlFor="entry_cctv_stream_source" className="form-label">
                          Entry Camera Stream Source
                        </label>
                        <input
                          type="text"
                          id="entry_cctv_stream_source"
                          name="entry_cctv_stream_source"
                          className="form-control"
                          disabled={!canManageAdvancedOps}
                          value={entryCctvStreamSource}
                          onChange={(ev) => setEntryCctvStreamSource(ev.target.value)}
                          placeholder="0, 1, or rtsp://..."
                        />
                      </div>

                      <div className="col-12">
                        <label htmlFor="exit_cctv_stream_source" className="form-label">
                          Exit Camera Stream Source
                        </label>
                        <input
                          type="text"
                          id="exit_cctv_stream_source"
                          name="exit_cctv_stream_source"
                          className="form-control"
                          disabled={!canManageAdvancedOps}
                          value={exitCctvStreamSource}
                          onChange={(ev) => setExitCctvStreamSource(ev.target.value)}
                          placeholder="0, 1, or rtsp://..."
                        />
                        <div className="form-text">
                          Changes take effect after restarting the entry/exit workers.
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            ) : null}

            <div className="col-12 d-flex gap-2 flex-wrap align-items-center">
              <button type="submit" className="btn btn-primary" disabled={!canSave || saving}>
                {saving ? 'Saving...' : 'Save Settings'}
              </button>
              {!canSave ? <span className="badge bg-secondary">Read-only role</span> : null}
            </div>
          </form>
        </div>

        {canManageAdvancedOps ? (
          <div className="col-12">
            <div className="card border-danger">
              <div className="card-body">
                <h5 className="card-title text-danger">Advanced Operations</h5>
                <p className="text-muted">
                  <strong>Warning:</strong> These operations cannot be undone and require two confirmation steps.
                </p>
                <div className="d-flex gap-2 flex-wrap">
                  <button onClick={resetDatabase} className="btn btn-danger" type="button">
                    Reset Database
                  </button>
                  <button onClick={clearRecognitionLog} className="btn btn-warning" type="button">
                    Clear Recognition Events
                  </button>
                </div>
              </div>
            </div>
          </div>
        ) : null}

        {canViewAudit ? (
          <div className="col-12">
            <div className="card">
              <div className="card-body">
                <h5 className="card-title">Settings Audit</h5>
                {lastChange ? (
                  <div className="alert alert-light border mb-3">
                    <div className="fw-semibold">Last Change</div>
                    <div className="small text-muted">
                      {lastChange.username || 'Unknown user'} • {lastChange.timestamp || 'Unknown time'}
                    </div>
                    <div className="small mt-1">{lastChange.target || 'No detail recorded.'}</div>
                  </div>
                ) : (
                  <div className="text-muted small mb-3">No settings changes have been recorded yet.</div>
                )}

                <div className="table-responsive">
                  <table className="table table-sm align-middle">
                    <thead>
                      <tr>
                        <th>ID</th>
                        <th>Changed By</th>
                        <th>Timestamp</th>
                        <th>Details</th>
                      </tr>
                    </thead>
                    <tbody>
                      {auditRows.length ? (
                        auditRows.map((row) => (
                          <tr key={row.audit_id}>
                            <td>{row.audit_id}</td>
                            <td>{row.username || '-'}</td>
                            <td>{row.timestamp || '-'}</td>
                            <td>{row.target || '-'}</td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td colSpan="4" className="text-center text-muted">
                            No audit entries found.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
