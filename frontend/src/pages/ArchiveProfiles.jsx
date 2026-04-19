import React from 'react';
import { fetchJson } from '../api.js';
import { confirmAction, getErrorMessage, showError, showSuccess } from '../alerts.js';

export default function ArchiveProfilesPage() {
  const [rows, setRows] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [search, setSearch] = React.useState('');
  const [pageSize, setPageSize] = React.useState('10');
  const [sortBy, setSortBy] = React.useState('name_asc');
  const [programFilter, setProgramFilter] = React.useState('');
  const [selectMode, setSelectMode] = React.useState(false);
  const [selected, setSelected] = React.useState({});

  const loadRows = React.useCallback(() => {
    setLoading(true);
    fetchJson('/api/archive-profiles')
      .then((resp) => setRows(resp.rows || []))
      .catch(() => setRows([]))
      .finally(() => setLoading(false));
  }, []);

  React.useEffect(() => {
    loadRows();
  }, [loadRows]);

  const programs = React.useMemo(() => {
    const set = {};
    rows.forEach((r) => {
      if (r.program && r.program !== '-') {
        set[r.program] = true;
      }
    });
    return Object.keys(set).sort();
  }, [rows]);

  const filtered = React.useMemo(() => {
    const searchValue = (search || '').toLowerCase();
    const filteredRows = rows.filter((r) => {
      const nameMatch = r.name.toLowerCase().includes(searchValue);
      const programMatch = !programFilter || r.program === programFilter;
      return nameMatch && programMatch;
    });

    if (sortBy === 'name_asc') filteredRows.sort((a, b) => a.name.localeCompare(b.name));
    if (sortBy === 'name_desc') filteredRows.sort((a, b) => b.name.localeCompare(a.name));
    if (sortBy === 'created_desc') filteredRows.sort((a, b) => b.created_at.localeCompare(a.created_at));
    if (sortBy === 'created_asc') filteredRows.sort((a, b) => a.created_at.localeCompare(b.created_at));

    return filteredRows;
  }, [rows, search, sortBy, programFilter]);

  const pageLimit = parseInt(pageSize, 10) || 10;
  const pageRows = filtered.slice(0, pageLimit);

  function toggleSelectMode() {
    const next = !selectMode;
    setSelectMode(next);
    if (!next) {
      setSelected({});
    }
  }

  function toggleSelectAll(ev) {
    const checked = ev.target.checked;
    if (!checked) {
      setSelected({});
      return;
    }
    const next = {};
    pageRows.forEach((row) => {
      if (row.user_id) {
        next[row.user_id] = true;
      }
    });
    setSelected(next);
  }

  function toggleRow(userId, checked) {
    setSelected((prev) => {
      const next = { ...prev };
      if (checked) {
        next[userId] = true;
      } else {
        delete next[userId];
      }
      return next;
    });
  }

  async function archiveSelected() {
    const ids = Object.keys(selected).filter((id) => selected[id]);
    if (!ids.length) return;
    const confirmed = await confirmAction({
      title: 'Archive Selected Profiles?',
      text: `This will archive ${ids.length} selected profile${ids.length === 1 ? '' : 's'}.`,
      confirmButtonText: 'Archive',
      confirmButtonColor: '#6c757d'
    });
    if (!confirmed) return;

    try {
      await fetchJson('/api/archive-profiles/submit', {
        method: 'POST',
        body: JSON.stringify({ user_ids: ids })
      });
      setSelected({});
      setSelectMode(false);
      loadRows();
      await showSuccess('Profiles Archived', `Archived ${ids.length} profile${ids.length === 1 ? '' : 's'} successfully.`);
    } catch (error) {
      await showError('Archive Failed', getErrorMessage(error));
    }
  }

  async function archiveSingle(userId) {
    if (!userId) return;
    const confirmed = await confirmAction({
      title: 'Archive Profile?',
      text: 'This profile will be moved to archived profiles.',
      confirmButtonText: 'Archive',
      confirmButtonColor: '#6c757d'
    });
    if (!confirmed) return;

    try {
      await fetchJson('/api/archive-profiles/submit', {
        method: 'POST',
        body: JSON.stringify({ user_ids: [userId] })
      });
      loadRows();
      await showSuccess('Profile Archived', 'The profile was archived successfully.');
    } catch (error) {
      await showError('Archive Failed', getErrorMessage(error));
    }
  }

  if (loading) {
    return (
      <div className="d-flex justify-content-center align-items-center" style={{ minHeight: '30vh' }}>
        <div className="spinner-border text-primary" role="status"></div>
      </div>
    );
  }

  const selectAllChecked =
    selectMode && pageRows.length && pageRows.every((row) => row.user_id && selected[row.user_id]);

  return (
    <section className="section">
      <div className="pagetitle">
        <h1>Archive Profiles</h1>
        <nav>
          <ol className="breadcrumb mb-0">
            <li className="breadcrumb-item">
              <a href="/dashboard">Home</a>
            </li>
            <li className="breadcrumb-item">
              <a href="/registered-profiles">Registered Profiles</a>
            </li>
            <li className="breadcrumb-item active">Archive Profiles</li>
          </ol>
        </nav>
      </div>
      <div className="card mb-3 profiles-toolbar">
        <div className="card-body mt-3">
          <div className="d-flex flex-wrap justify-content-between align-items-center gap-2">
            <div className="input-group" style={{ maxWidth: '520px', width: '100%' }}>
              <input
                id="profileSearch"
                type="text"
                className="form-control"
                placeholder="Search Name"
                value={search}
                onChange={(ev) => setSearch(ev.target.value)}
              />
              <button className="btn btn-danger" type="button">
                <i className="bi bi-search"></i>
              </button>
            </div>
            <div className="d-flex flex-wrap justify-content-lg-end gap-2 ms-lg-auto">
              <a className="btn btn-outline-secondary" href="/registered-profiles">
                <i className="bi bi-arrow-left me-1"></i>Back
              </a>
              <button className="btn btn-outline-secondary" type="button" onClick={archiveSelected}>
                <i className="bi bi-archive me-1"></i>Archive
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-body">
          <h5 className="card-title">Active Profile List</h5>
          <div className="row g-2 mb-3 align-items-center">
            <div className="col-auto">
              <select
                id="pageSize"
                className="form-select"
                value={pageSize}
                onChange={(ev) => setPageSize(ev.target.value)}
              >
                <option value="10">10</option>
                <option value="25">25</option>
                <option value="50">50</option>
              </select>
            </div>
            <div className="col-auto">
              <select
                id="sortBy"
                className="form-select"
                value={sortBy}
                onChange={(ev) => setSortBy(ev.target.value)}
              >
                <option value="name_asc">Sort By: Name A-Z</option>
                <option value="name_desc">Sort By: Name Z-A</option>
                <option value="created_desc">Sort By: Newest</option>
                <option value="created_asc">Sort By: Oldest</option>
              </select>
            </div>
            <div className="col-auto">
              <select
                id="programFilter"
                className="form-select"
                value={programFilter}
                onChange={(ev) => setProgramFilter(ev.target.value)}
              >
                <option value="">All Programs</option>
                {programs.map((program) => (
                  <option key={program} value={program}>
                    {program}
                  </option>
                ))}
              </select>
            </div>
            <div className="col-auto ms-lg-auto">
              <button
                className={selectMode ? 'btn btn-secondary' : 'btn btn-outline-secondary'}
                type="button"
                onClick={toggleSelectMode}
              >
                <i className="bi bi-check2-square me-1"></i>Select Multiple
              </button>
            </div>
          </div>

          <div className="table-responsive">
            <table className="table table-hover align-middle">
              <thead>
                <tr>
                  <th className={`text-center select-col ${selectMode ? '' : 'd-none'}`}>
                    <input
                      type="checkbox"
                      id="selectAllProfiles"
                      checked={!!selectAllChecked}
                      onChange={toggleSelectAll}
                    />
                  </th>
                  <th>#</th>
                  <th>Name</th>
                  <th>SR Code</th>
                  <th>Program</th>
                  <th>Created</th>
                  <th>Last Updated</th>
                  <th className="text-center">Archive</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.length ? (
                  pageRows.map((row, idx) => {
                    const isChecked = !!(row.user_id && selected[row.user_id]);
                    return (
                      <tr key={row.user_id || idx}>
                        <td className={`text-center select-col ${selectMode ? '' : 'd-none'}`}>
                          <input
                            type="checkbox"
                            className="profile-checkbox"
                            name="user_ids"
                            value={row.user_id || ''}
                            checked={isChecked}
                            disabled={!selectMode}
                            onChange={(ev) => toggleRow(row.user_id, ev.target.checked)}
                          />
                        </td>
                        <td className="row-index">{String(idx + 1)}</td>
                        <td>{row.name}</td>
                        <td>{row.sr_code}</td>
                        <td>{row.program}</td>
                        <td>{row.created_at}</td>
                        <td>{row.last_updated}</td>
                        <td className="text-center">
                          <button
                            type="button"
                            className="btn btn-sm btn-outline-secondary"
                            onClick={() => archiveSingle(row.user_id)}
                          >
                            <i className="bi bi-archive"></i>
                          </button>
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr>
                    <td colSpan="8" className="text-center text-muted">
                      No registered profiles found.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="small text-muted mt-2">
            {Math.min(filtered.length, pageLimit)} of {filtered.length} profiles shown
          </div>
        </div>
      </div>
    </section>
  );
}
