import React from 'react';
import { fetchJson } from '../api.js';
import { socket } from '../socket.js';

function formatDate(ts) {
  if (!ts) return '-';
  return ts.slice(0, 10);
}

function formatTime(ts) {
  if (!ts) return '-';
  return ts.length > 10 ? ts.slice(11, 19) : '-';
}

export default function EntryExitLogsPage() {
  const [rows, setRows] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(false);

  const [search, setSearch] = React.useState('');
  const [pageSize, setPageSize] = React.useState('10');
  const [confFilter, setConfFilter] = React.useState('');
  const [dateFilter, setDateFilter] = React.useState('');
  const [activeTab, setActiveTab] = React.useState('all');
  const refreshInFlightRef = React.useRef(false);
  const hasLoadedDataRef = React.useRef(false);

  async function loadLogs({ silent = false } = {}) {
    if (refreshInFlightRef.current) return;
    refreshInFlightRef.current = true;
    if (!silent) {
      setLoading(true);
    }
    try {
      const resp = await fetchJson('/api/events');
      setRows(resp.rows || []);
      hasLoadedDataRef.current = true;
      setError(false);
    } catch {
      if (!hasLoadedDataRef.current) {
        setError(true);
      }
      if (!silent) {
        setRows([]);
      }
    } finally {
      if (!silent) {
        setLoading(false);
      }
      refreshInFlightRef.current = false;
    }
  }

  React.useEffect(() => {
    loadLogs();
    const timer = window.setInterval(() => {
      loadLogs({ silent: true });
    }, 15000);
    return () => window.clearInterval(timer);
  }, []);

  React.useEffect(() => {
    function handleAnalyticsUpdated() {
      loadLogs({ silent: true });
    }

    socket.connect();
    socket.on('analytics_updated', handleAnalyticsUpdated);
    return () => {
      socket.off('analytics_updated', handleAnalyticsUpdated);
      socket.disconnect();
    };
  }, []);

  const today = React.useMemo(() => new Date().toISOString().slice(0, 10), []);
  const weekAgo = React.useMemo(
    () => new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10),
    []
  );

  const filtered = React.useMemo(() => {
    const searchValue = (search || '').toLowerCase();
    const selectedDate = dateFilter || '';

    return rows.filter((item) => {
      const matchSearch =
        !searchValue ||
        item.name.toLowerCase().includes(searchValue) ||
        item.sr_code.toLowerCase().includes(searchValue);
      const matchConf =
        !confFilter ||
        (confFilter === 'high' && item.conf_pct >= 80) ||
        (confFilter === 'med' && item.conf_pct >= 60 && item.conf_pct < 80) ||
        (confFilter === 'low' && item.conf_pct < 60);
      const matchDate = !selectedDate || item.date === selectedDate;
      const matchTab =
        activeTab === 'today'
          ? item.date === today
          : activeTab === 'week'
            ? item.date >= weekAgo
            : true;

      return matchSearch && matchConf && matchDate && matchTab;
    });
  }, [rows, search, confFilter, dateFilter, activeTab, today, weekAgo]);

  const pageLimit = parseInt(pageSize, 10) || 10;
  const pageRows = filtered.slice(0, pageLimit);
  const exportHref = dateFilter ? `/entry-logs/export?date=${dateFilter}` : '/entry-logs/export';

  function handleTab(tab) {
    setActiveTab(tab);
    if (tab === 'today') {
      setDateFilter(today);
    } else {
      setDateFilter('');
    }
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
        <h1>Entry Logs</h1>
        <nav>
          <ol className="breadcrumb mb-0">
            <li className="breadcrumb-item">
              <a href="/dashboard">Home</a>
            </li>
            <li className="breadcrumb-item active">Entry Logs</li>
          </ol>
        </nav>
      </div>
      {error ? (
        <div className="alert alert-danger">
          <i className="bi bi-exclamation-triangle me-2"></i>
          Failed to load entry logs. Please refresh the page.
        </div>
      ) : null}
      <div className="card mb-3">
        <div className="card-body">
          <div className="d-flex flex-wrap justify-content-between align-items-center gap-2 mt-3">
            <div className="input-group" style={{ maxWidth: '520px', width: '100%' }}>
              <input
                id="logSearch"
                type="text"
                className="form-control"
                placeholder="Search name or SR code..."
                value={search}
                onChange={(ev) => setSearch(ev.target.value)}
              />
              <button className="btn btn-danger" type="button">
                <i className="bi bi-search"></i>
              </button>
            </div>
            <div className="d-flex flex-nowrap gap-2 ms-lg-auto align-items-stretch justify-content-end">
              <input
                type="date"
                id="dateFilter"
                className="form-control"
                style={{ minWidth: '170px', height: '38px' }}
                title="Filter by date"
                max={today}
                value={dateFilter}
                onChange={(ev) => setDateFilter(ev.target.value)}
              />
              <a
                href={exportHref}
                id="exportLogs"
                className="btn btn-outline-success d-flex align-items-center"
                style={{ height: '38px', whiteSpace: 'nowrap' }}
              >
                <i className="bi bi-download me-1"></i>Export CSV
              </a>
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-body">
          <h5 className="card-title">Recognition Log</h5>

          <div className="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-3">
            <div className="d-flex gap-2 flex-wrap">
              <select
                id="pageSize"
                className="form-select"
                style={{ width: 'auto' }}
                value={pageSize}
                onChange={(ev) => setPageSize(ev.target.value)}
              >
                <option value="10">10</option>
                <option value="25">25</option>
                <option value="50">50</option>
                <option value="100">100</option>
              </select>
              <select
                id="confFilter"
                className="form-select"
                style={{ width: 'auto' }}
                value={confFilter}
                onChange={(ev) => setConfFilter(ev.target.value)}
              >
                <option value="">All Confidence</option>
                <option value="high">High (80%+)</option>
                <option value="med">Medium (60-79%)</option>
                <option value="low">Low (&lt;60%)</option>
              </select>
            </div>

            <div className="btn-group" role="group">
              <button
                type="button"
                className={`btn btn-outline-secondary ${activeTab === 'all' ? 'active' : ''}`}
                onClick={() => handleTab('all')}
              >
                All Logs
              </button>
              <button
                type="button"
                className={`btn btn-outline-secondary ${activeTab === 'today' ? 'active' : ''}`}
                onClick={() => handleTab('today')}
              >
                Today
              </button>
              <button
                type="button"
                className={`btn btn-outline-secondary ${activeTab === 'week' ? 'active' : ''}`}
                onClick={() => handleTab('week')}
              >
                This Week
              </button>
            </div>
          </div>

          <div className="table-responsive">
            <table className="table table-hover align-middle">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Name</th>
                  <th>SR Code</th>
                  <th>Confidence</th>
                  <th>Date</th>
                  <th>Time</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.length ? (
                  pageRows.map((row, idx) => {
                    let badgeClass = 'bg-danger';
                    if (row.conf_pct >= 80) badgeClass = 'bg-success';
                    else if (row.conf_pct >= 60) badgeClass = 'bg-warning text-dark';

                    return (
                      <tr key={`${row.name}-${idx}`}>
                        <td>{String(idx + 1)}</td>
                        <td>{row.name}</td>
                        <td>
                          <code>{row.sr_code}</code>
                        </td>
                        <td>
                          <span className={`badge ${badgeClass}`}>{row.conf_pct}%</span>
                        </td>
                        <td>{row.date || formatDate(row.timestamp)}</td>
                        <td>
                          <code>{row.time || formatTime(row.timestamp)}</code>
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr>
                    <td colSpan="6" className="text-center text-muted py-4">
                      <i className="bi bi-journal-x fs-3 d-block mb-2"></i>
                      No recognition logs found.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="small text-muted mt-2">
            {Math.min(filtered.length, pageLimit)} of {filtered.length} logs shown
          </div>
        </div>
      </div>
    </section>
  );
}
