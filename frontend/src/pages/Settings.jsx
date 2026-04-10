import React from 'react';
import { fetchJson } from '../api.js';

async function confirmDialog(title, text, confirmText, confirmButtonColor) {
  if (window.Swal) {
    const result = await window.Swal.fire({
      icon: 'warning',
      title,
      text,
      showCancelButton: true,
      confirmButtonText: confirmText,
      cancelButtonText: 'Cancel',
      confirmButtonColor,
      cancelButtonColor: '#6c757d',
      reverseButtons: true
    });
    return result.isConfirmed;
  }
  return window.confirm(text);
}

async function showDialog(icon, title, text) {
  if (window.Swal) {
    await window.Swal.fire({ icon, title, text });
    return;
  }
  window.alert(`${title}: ${text}`);
}

export default function SettingsPage() {
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);

  const [threshold, setThreshold] = React.useState('0.3');
  const [qualityThreshold, setQualityThreshold] = React.useState('0.2');
  const [vectorIndexTopK, setVectorIndexTopK] = React.useState('20');
  const [maxOccupancy, setMaxOccupancy] = React.useState('300');

  React.useEffect(() => {
    fetchJson('/api/settings')
      .then((resp) => {
        setData(resp);
        setThreshold(String(resp.threshold ?? '0.3'));
        setQualityThreshold(String(resp.quality_threshold ?? '0.2'));
        setVectorIndexTopK(String(resp.vector_index_top_k ?? '20'));
        setMaxOccupancy(String(resp.max_occupancy ?? '300'));
      })
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, []);

  function handleSubmit(ev) {
    ev.preventDefault();
    fetchJson('/api/settings', {
      method: 'POST',
      body: JSON.stringify({
        max_occupancy: maxOccupancy,
        threshold,
        quality_threshold: qualityThreshold,
        vector_index_top_k: vectorIndexTopK
      })
    })
      .then((resp) => setData(resp))
      .catch(() => undefined);
  }

  async function resetDatabase() {
    const confirmed = await confirmDialog(
      'Reset Database?',
      'This will delete all registered users and face data. This action cannot be undone.',
      'Yes, reset',
      '#dc3545'
    );
    if (!confirmed) return;

    fetchJson('/api/reset_database', { method: 'POST' })
      .then(async () => {
        await showDialog('success', 'Completed', 'Database reset successfully. The system will restart.');
        window.location.reload();
      })
      .catch(async (error) => {
        await showDialog('error', 'Request Failed', error.message || 'Unexpected error occurred.');
      });
  }

  async function clearRecognitionLog() {
    const confirmed = await confirmDialog(
      'Clear Recognition Log?',
      'This will clear all recognition history. This action cannot be undone.',
      'Yes, clear',
      '#fd7e14'
    );
    if (!confirmed) return;

    fetchJson('/api/clear_log', { method: 'POST' })
      .then(async () => {
        await showDialog('success', 'Completed', 'Recognition log cleared successfully.');
        window.location.reload();
      })
      .catch(async (error) => {
        await showDialog('error', 'Request Failed', error.message || 'Unexpected error occurred.');
      });
  }

  if (loading) {
    return (
      <div className="d-flex justify-content-center align-items-center" style={{ minHeight: '30vh' }}>
        <div className="spinner-border text-primary" role="status"></div>
      </div>
    );
  }

  return (
    <section className="section">
      <div className="pagetitle">
        <h1>System Settings</h1>
        <p className="text-muted mb-0">Configure face recognition parameters</p>
      </div>
      <div className="row g-3">
        <div className="col-12">
          <div className="card">
            <div className="card-body">
              <h5 className="card-title">System Statistics</h5>
              <div className="row g-3">
                <div className="col-md-4">
                  <div className="border rounded p-3 text-center h-100">
                    <div className="h3 mb-1">{data?.user_count}</div>
                    <small className="text-muted">Registered Users</small>
                  </div>
                </div>
                <div className="col-md-4">
                  <div className="border rounded p-3 text-center h-100">
                    <div className="h3 mb-1">{Number(threshold).toFixed(3)}</div>
                    <small className="text-muted">Current Threshold</small>
                  </div>
                </div>
                <div className="col-md-4">
                  <div className="border rounded p-3 text-center h-100">
                    <div className="h3 mb-1">{Number(qualityThreshold).toFixed(2)}</div>
                    <small className="text-muted">Min Face Quality</small>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="col-12">
          <div className="card">
            <div className="card-body">
              <h5 className="card-title">Recognition Settings</h5>
              <form className="row g-4" onSubmit={handleSubmit}>
                <div className="col-12">
                  <label htmlFor="max_occupancy" className="form-label">
                    Max Library Occupancy
                  </label>
                  <input
                    type="number"
                    id="max_occupancy"
                    name="max_occupancy"
                    className="form-control"
                    min="1"
                    step="1"
                    value={maxOccupancy}
                    onChange={(ev) => setMaxOccupancy(ev.target.value)}
                  />
                  <div className="form-text">
                    This value is used for occupancy calculations in Analytics & Reports.
                  </div>
                </div>

                <div className="col-12">
                  <label htmlFor="vector_index_top_k" className="form-label">
                    Vector Index Top-K
                  </label>
                  <input
                    type="number"
                    id="vector_index_top_k"
                    name="vector_index_top_k"
                    className="form-control"
                    min="1"
                    step="1"
                    value={vectorIndexTopK}
                    onChange={(ev) => setVectorIndexTopK(ev.target.value)}
                  />
                  <div className="form-text">
                    Number of nearest embedding candidates examined per model before two-factor verification.
                  </div>
                </div>

                <div className="col-12">
                  <label htmlFor="threshold" className="form-label">
                    Recognition Threshold:{' '}
                    <span className="badge bg-light text-dark" id="thresholdValue">
                      {Number(threshold).toFixed(3)}
                    </span>
                  </label>
                  <input
                    type="range"
                    id="threshold"
                    name="threshold"
                    className="form-range"
                    min="0.1"
                    max="0.8"
                    step="0.01"
                    value={threshold}
                    onChange={(ev) => setThreshold(ev.target.value)}
                  />
                  <div className="form-text">Lower values = stricter. Higher values = more lenient.</div>
                </div>

                <div className="col-12">
                  <label htmlFor="quality_threshold" className="form-label">
                    Minimum Face Quality:{' '}
                    <span className="badge bg-light text-dark" id="qualityValue">
                      {Number(qualityThreshold).toFixed(2)}
                    </span>
                  </label>
                  <input
                    type="range"
                    id="quality_threshold"
                    name="quality_threshold"
                    className="form-range"
                    min="0.1"
                    max="1.0"
                    step="0.05"
                    value={qualityThreshold}
                    onChange={(ev) => setQualityThreshold(ev.target.value)}
                  />
                </div>

                <div className="col-12 d-flex gap-2 flex-wrap">
                  <button type="submit" className="btn btn-primary">Save Settings</button>
                  <a href="/api/stats" className="btn btn-outline-secondary" target="_blank" rel="noreferrer">
                    View Detailed Stats
                  </a>
                </div>
              </form>
            </div>
          </div>
        </div>

        <div className="col-12">
          <div className="card border-danger">
            <div className="card-body">
              <h5 className="card-title text-danger">Advanced Operations</h5>
              <p className="text-muted">
                <strong>Warning:</strong> These operations cannot be undone.
              </p>
              <div className="d-flex gap-2 flex-wrap">
                <button onClick={resetDatabase} className="btn btn-danger" type="button">
                  Reset Database
                </button>
                <button onClick={clearRecognitionLog} className="btn btn-warning" type="button">
                  Clear Recognition Log
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
