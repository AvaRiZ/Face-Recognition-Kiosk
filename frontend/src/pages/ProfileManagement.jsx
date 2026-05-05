import React from 'react';
import { useLocation } from 'react-router-dom';
import { fetchJson } from '../api.js';
import { confirmAction, getErrorMessage, showError, showSuccess } from '../alerts.js';

const PAGE_SIZE_OPTIONS = ['10', '25', '50'];
const ACTIVE_SORT_OPTIONS = [
  { value: 'created_desc', label: 'Sort By: Newest' },
  { value: 'created_asc', label: 'Sort By: Oldest' },
  { value: 'name_asc', label: 'Sort By: Name A-Z' },
  { value: 'name_desc', label: 'Sort By: Name Z-A' },
  { value: 'updated_desc', label: 'Sort By: Recently Updated' },
  { value: 'updated_asc', label: 'Sort By: Least Recently Updated' }
];
const ARCHIVED_SORT_OPTIONS = [
  { value: 'archived_desc', label: 'Sort By: Recently Archived' },
  { value: 'archived_asc', label: 'Sort By: Oldest Archived' },
  { value: 'name_asc', label: 'Sort By: Name A-Z' },
  { value: 'name_desc', label: 'Sort By: Name Z-A' }
];

function normalizeStatus(value) {
  const normalized = String(value || '').trim().toLowerCase();
  return normalized === 'archived' ? 'archived' : 'active';
}

function getInitialStatus(location) {
  const params = new URLSearchParams(location.search || '');
  const statusParam = params.get('status');
  if (statusParam) {
    return normalizeStatus(statusParam);
  }
  if (location.pathname === '/archived-profiles') {
    return 'archived';
  }
  return 'active';
}

function createInitialCounts() {
  return { active: 0, archived: 0 };
}

export default function ProfileManagementPage() {
  const location = useLocation();
  const [status, setStatus] = React.useState(() => getInitialStatus(location));

  const [rows, setRows] = React.useState([]);
  const [programs, setPrograms] = React.useState([]);
  const [counts, setCounts] = React.useState(createInitialCounts());
  const [loading, setLoading] = React.useState(true);
  const [errorMessage, setErrorMessage] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);

  const [search, setSearch] = React.useState('');
  const [pageSize, setPageSize] = React.useState('10');
  const [sortBy, setSortBy] = React.useState('created_desc');
  const [programFilter, setProgramFilter] = React.useState('');
  const [page, setPage] = React.useState(1);
  const [total, setTotal] = React.useState(0);
  const [totalPages, setTotalPages] = React.useState(1);
  const [selected, setSelected] = React.useState({});

  const [editOpen, setEditOpen] = React.useState(false);
  const [editBusy, setEditBusy] = React.useState(false);
  const [editingUserId, setEditingUserId] = React.useState(null);
  const [editForm, setEditForm] = React.useState({
    name: '',
    sr_code: '',
    gender: '',
    program: ''
  });
  const [editErrors, setEditErrors] = React.useState({});

  React.useEffect(() => {
    const next = getInitialStatus(location);
    setStatus((prev) => (prev === next ? prev : next));
  }, [location.pathname, location.search]);

  React.useEffect(() => {
    setSortBy(status === 'active' ? 'created_desc' : 'archived_desc');
    setPage(1);
    setSelected({});
  }, [status]);

  const sortOptions = status === 'active' ? ACTIVE_SORT_OPTIONS : ARCHIVED_SORT_OPTIONS;
  const pageSizeInt = parseInt(pageSize, 10) || 10;

  const loadRows = React.useCallback(async () => {
    setLoading(true);
    setErrorMessage('');
    try {
      const params = new URLSearchParams({
        status,
        page: String(page),
        page_size: String(pageSizeInt),
        sort: sortBy
      });
      if (search.trim()) {
        params.set('q', search.trim());
      }
      if (programFilter) {
        params.set('program', programFilter);
      }

      const response = await fetchJson(`/api/profiles?${params.toString()}`);
      setRows(response.rows || []);
      setPrograms(Array.isArray(response.programs) ? response.programs : []);
      setCounts(response.counts || createInitialCounts());
      setTotal(Number(response.total) || 0);
      setTotalPages(Math.max(1, Number(response.total_pages) || 1));
      if (response.page && Number(response.page) !== page) {
        setPage(Number(response.page));
      }
    } catch (error) {
      setRows([]);
      setPrograms([]);
      setTotal(0);
      setTotalPages(1);
      setErrorMessage(getErrorMessage(error, 'Failed to load profiles.'));
    } finally {
      setLoading(false);
    }
  }, [status, page, pageSizeInt, sortBy, search, programFilter]);

  React.useEffect(() => {
    loadRows();
  }, [loadRows]);

  function resetSelection() {
    setSelected({});
  }

  function handleStatusChange(nextStatus) {
    if (nextStatus === status) {
      return;
    }
    setStatus(nextStatus);
    setProgramFilter('');
    setSearch('');
    setPage(1);
    resetSelection();
  }

  function handleSearchChange(value) {
    setSearch(value);
    setPage(1);
    resetSelection();
  }

  function handleSortChange(value) {
    setSortBy(value);
    setPage(1);
    resetSelection();
  }

  function handleProgramFilterChange(value) {
    setProgramFilter(value);
    setPage(1);
    resetSelection();
  }

  function handlePageSizeChange(value) {
    setPageSize(value);
    setPage(1);
    resetSelection();
  }

  function handlePageChange(nextPage) {
    if (nextPage < 1 || nextPage > totalPages || nextPage === page) {
      return;
    }
    setPage(nextPage);
    resetSelection();
  }

  function toggleSelectAll(checked) {
    if (!checked) {
      resetSelection();
      return;
    }
    const next = {};
    rows.forEach((row) => {
      if (row.user_id) {
        next[row.user_id] = true;
      }
    });
    setSelected(next);
  }

  function toggleRowSelection(userId, checked) {
    if (!userId) {
      return;
    }
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

  function getSelectedIds() {
    return Object.keys(selected)
      .filter((id) => selected[id])
      .map((id) => Number(id))
      .filter((id) => Number.isFinite(id));
  }

  async function archiveIds(userIds) {
    if (!userIds.length) {
      return;
    }
    const confirmed = await confirmAction({
      title: userIds.length === 1 ? 'Archive Profile?' : 'Archive Selected Profiles?',
      text:
        userIds.length === 1
          ? 'This profile will be moved to archived profiles.'
          : `This will archive ${userIds.length} selected profile${userIds.length === 1 ? '' : 's'}.`,
      confirmButtonText: 'Archive',
      confirmButtonColor: '#6c757d'
    });
    if (!confirmed) {
      return;
    }
    setSubmitting(true);
    try {
      await fetchJson('/api/archive-profiles/submit', {
        method: 'POST',
        body: JSON.stringify({ user_ids: userIds })
      });
      resetSelection();
      await loadRows();
      await showSuccess('Profiles Archived', `Archived ${userIds.length} profile${userIds.length === 1 ? '' : 's'} successfully.`);
    } catch (error) {
      await showError('Archive Failed', getErrorMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  async function restoreIds(userIds) {
    if (!userIds.length) {
      return;
    }
    const confirmed = await confirmAction({
      title: userIds.length === 1 ? 'Restore Profile?' : 'Restore Selected Profiles?',
      text:
        userIds.length === 1
          ? 'This profile will be returned to active profiles.'
          : `This will restore ${userIds.length} selected profile${userIds.length === 1 ? '' : 's'}.`,
      confirmButtonText: 'Restore',
      confirmButtonColor: '#198754'
    });
    if (!confirmed) {
      return;
    }
    setSubmitting(true);
    try {
      await fetchJson('/api/archived-profiles/restore', {
        method: 'POST',
        body: JSON.stringify({ user_ids: userIds })
      });
      resetSelection();
      await loadRows();
      await showSuccess('Profiles Restored', `Restored ${userIds.length} profile${userIds.length === 1 ? '' : 's'} successfully.`);
    } catch (error) {
      await showError('Restore Failed', getErrorMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  async function deleteIds(userIds) {
    if (!userIds.length) {
      return;
    }
    const confirmed = await confirmAction({
      title: userIds.length === 1 ? 'Delete Archived Profile Permanently?' : 'Delete Archived Profiles Permanently?',
      text:
        userIds.length === 1
          ? 'This action permanently deletes the profile and cannot be undone.'
          : `This permanently deletes ${userIds.length} archived profiles. This cannot be undone.`,
      confirmButtonText: 'Delete Permanently',
      confirmButtonColor: '#dc3545'
    });
    if (!confirmed) {
      return;
    }
    setSubmitting(true);
    try {
      for (const userId of userIds) {
        await fetchJson(`/api/profiles/${userId}`, { method: 'DELETE' });
      }
      resetSelection();
      await loadRows();
      await showSuccess('Profiles Deleted', `Deleted ${userIds.length} archived profile${userIds.length === 1 ? '' : 's'}.`);
    } catch (error) {
      await showError('Delete Failed', getErrorMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  function openEditModal(row) {
    setEditingUserId(row.user_id || null);
    setEditForm({
      name: row.name && row.name !== '-' ? row.name : '',
      sr_code: row.sr_code && row.sr_code !== '-' ? row.sr_code : '',
      gender: row.gender && row.gender !== '-' ? row.gender : '',
      program: row.program && row.program !== '-' ? row.program : ''
    });
    setEditErrors({});
    setEditOpen(true);
  }

  function closeEditModal() {
    if (editBusy) {
      return;
    }
    setEditOpen(false);
    setEditingUserId(null);
    setEditErrors({});
  }

  function updateEditField(field, value) {
    setEditForm((prev) => ({ ...prev, [field]: value }));
    setEditErrors((prev) => ({ ...prev, [field]: '' }));
  }

  function validateEditForm() {
    const nextErrors = {};
    if (!String(editForm.name || '').trim()) {
      nextErrors.name = 'Name is required.';
    }
    if (!String(editForm.sr_code || '').trim()) {
      nextErrors.sr_code = 'SR Code is required.';
    }
    if (!String(editForm.gender || '').trim()) {
      nextErrors.gender = 'Gender is required.';
    }
    if (!String(editForm.program || '').trim()) {
      nextErrors.program = 'Program is required.';
    }
    setEditErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  }

  async function saveEditProfile() {
    if (!editingUserId) {
      return;
    }
    if (!validateEditForm()) {
      return;
    }
    setEditBusy(true);
    try {
      await fetchJson(`/api/profiles/${editingUserId}`, {
        method: 'PUT',
        body: JSON.stringify({
          name: String(editForm.name || '').trim(),
          sr_code: String(editForm.sr_code || '').trim(),
          gender: String(editForm.gender || '').trim(),
          program: String(editForm.program || '').trim()
        })
      });
      setEditOpen(false);
      setEditingUserId(null);
      await loadRows();
      await showSuccess('Profile Updated', 'The profile was updated successfully.');
    } catch (error) {
      const message = getErrorMessage(error);
      if (error?.data?.field) {
        setEditErrors((prev) => ({ ...prev, [error.data.field]: message }));
      }
      await showError('Update Failed', message);
    } finally {
      setEditBusy(false);
    }
  }

  const selectedIds = getSelectedIds();
  const hasRows = rows.length > 0;
  const selectAllChecked = hasRows && rows.every((row) => row.user_id && selected[row.user_id]);
  const firstRowIndex = total ? (page - 1) * pageSizeInt + 1 : 0;
  const lastRowIndex = total ? firstRowIndex + rows.length - 1 : 0;

  return (
    <section className="section">
      <div className="pagetitle">
        <h1>Profile Management</h1>
        <nav>
          <ol className="breadcrumb mb-0">
            <li className="breadcrumb-item">
              <a href="/dashboard">Home</a>
            </li>
            <li className="breadcrumb-item active">Profile Management</li>
          </ol>
        </nav>
      </div>

      <div className="card mb-3">
        <div className="card-body mt-3">
          <div className="d-flex flex-wrap justify-content-between align-items-center gap-2">
            <div className="btn-group" role="group" aria-label="Profile status tabs">
              <button
                type="button"
                className={status === 'active' ? 'btn btn-primary' : 'btn btn-outline-primary'}
                onClick={() => handleStatusChange('active')}
                disabled={submitting}
              >
                Active ({counts.active || 0})
              </button>
              <button
                type="button"
                className={status === 'archived' ? 'btn btn-secondary' : 'btn btn-outline-secondary'}
                onClick={() => handleStatusChange('archived')}
                disabled={submitting}
              >
                Archived ({counts.archived || 0})
              </button>
            </div>
            <div className="d-flex gap-2">
              <a className="btn btn-primary" href="/register">
                <i className="bi bi-person-plus me-1"></i>Add Profile
              </a>
            </div>
          </div>
        </div>
      </div>

      <div className="card mb-3 profiles-toolbar">
        <div className="card-body mt-3">
          <div className="row g-2 align-items-center">
            <div className="col-12 col-xl-5">
              <input
                id="profileSearch"
                type="text"
                className="form-control"
                placeholder="Search name or SR Code"
                value={search}
                onChange={(ev) => handleSearchChange(ev.target.value)}
                disabled={submitting}
              />
            </div>
            <div className="col-6 col-md-3 col-xl-2">
              <select
                id="pageSize"
                className="form-select"
                value={pageSize}
                onChange={(ev) => handlePageSizeChange(ev.target.value)}
                disabled={submitting}
              >
                {PAGE_SIZE_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option} / page
                  </option>
                ))}
              </select>
            </div>
            <div className="col-6 col-md-3 col-xl-3">
              <select
                id="sortBy"
                className="form-select"
                value={sortBy}
                onChange={(ev) => handleSortChange(ev.target.value)}
                disabled={submitting}
              >
                {sortOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="col-12 col-md-6 col-xl-2">
              <select
                id="programFilter"
                className="form-select"
                value={programFilter}
                onChange={(ev) => handleProgramFilterChange(ev.target.value)}
                disabled={submitting}
              >
                <option value="">All Programs</option>
                {programs.map((program) => (
                  <option key={program} value={program}>
                    {program}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="d-flex flex-wrap gap-2 mt-3">
            {status === 'active' ? (
              <button
                className="btn btn-outline-secondary"
                type="button"
                disabled={!selectedIds.length || submitting}
                onClick={() => archiveIds(selectedIds)}
              >
                <i className="bi bi-archive me-1"></i>Archive Selected
              </button>
            ) : (
              <>
                <button
                  className="btn btn-success"
                  type="button"
                  disabled={!selectedIds.length || submitting}
                  onClick={() => restoreIds(selectedIds)}
                >
                  <i className="bi bi-arrow-counterclockwise me-1"></i>Restore Selected
                </button>
                <button
                  className="btn btn-danger"
                  type="button"
                  disabled={!selectedIds.length || submitting}
                  onClick={() => deleteIds(selectedIds)}
                >
                  <i className="bi bi-trash me-1"></i>Delete Selected
                </button>
              </>
            )}
            <button className="btn btn-outline-primary" type="button" onClick={loadRows} disabled={submitting}>
              <i className="bi bi-arrow-repeat me-1"></i>Refresh
            </button>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-body">
          <h5 className="card-title">{status === 'active' ? 'Active Profiles' : 'Archived Profiles'}</h5>

          {loading ? (
            <div className="d-flex align-items-center gap-2 text-muted mb-2" aria-live="polite">
              <div className="spinner-border spinner-border-sm" role="status"></div>
              <span>Loading profiles...</span>
            </div>
          ) : null}

          {errorMessage ? (
            <div className="alert alert-danger d-flex justify-content-between align-items-center" role="alert">
              <span>{errorMessage}</span>
              <button type="button" className="btn btn-sm btn-light" onClick={loadRows}>
                Retry
              </button>
            </div>
          ) : null}

          <div className="table-responsive">
            <table className="table table-hover align-middle">
              <thead>
                <tr>
                  <th className="text-center">
                    <input
                      type="checkbox"
                      checked={!!selectAllChecked}
                      onChange={(ev) => toggleSelectAll(ev.target.checked)}
                      disabled={!hasRows || submitting}
                    />
                  </th>
                  <th>#</th>
                  <th>Name</th>
                  <th>SR Code</th>
                  <th>Gender</th>
                  <th>Program</th>
                  <th>Created</th>
                  <th>Last Updated</th>
                  {status === 'archived' ? <th>Archived At</th> : null}
                  <th className="text-center">Actions</th>
                </tr>
              </thead>
              <tbody>
                {hasRows ? (
                  rows.map((row, idx) => {
                    const userId = row.user_id;
                    const isChecked = !!(userId && selected[userId]);
                    return (
                      <tr key={userId || idx}>
                        <td className="text-center">
                          <input
                            type="checkbox"
                            checked={isChecked}
                            disabled={!userId || submitting}
                            onChange={(ev) => toggleRowSelection(userId, ev.target.checked)}
                          />
                        </td>
                        <td className="row-index">{String((page - 1) * pageSizeInt + idx + 1)}</td>
                        <td>{row.name}</td>
                        <td>{row.sr_code}</td>
                        <td>{row.gender}</td>
                        <td>{row.program}</td>
                        <td>{row.created_at}</td>
                        <td>{row.last_updated}</td>
                        {status === 'archived' ? <td>{row.archived_at}</td> : null}
                        <td className="text-center">
                          {status === 'active' ? (
                            <div className="d-flex justify-content-center gap-1">
                              <button
                                type="button"
                                className="btn btn-sm btn-outline-primary"
                                title="Edit"
                                disabled={submitting}
                                onClick={() => openEditModal(row)}
                              >
                                <i className="bi bi-pencil"></i>
                              </button>
                              <button
                                type="button"
                                className="btn btn-sm btn-outline-secondary"
                                title="Archive"
                                disabled={submitting}
                                onClick={() => archiveIds([userId])}
                              >
                                <i className="bi bi-archive"></i>
                              </button>
                            </div>
                          ) : (
                            <div className="d-flex justify-content-center gap-1">
                              <button
                                type="button"
                                className="btn btn-sm btn-outline-success"
                                title="Restore"
                                disabled={submitting}
                                onClick={() => restoreIds([userId])}
                              >
                                <i className="bi bi-arrow-counterclockwise"></i>
                              </button>
                              <button
                                type="button"
                                className="btn btn-sm btn-outline-danger"
                                title="Delete Permanently"
                                disabled={submitting}
                                onClick={() => deleteIds([userId])}
                              >
                                <i className="bi bi-trash"></i>
                              </button>
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })
                ) : loading ? (
                  <tr>
                    <td colSpan={status === 'archived' ? '10' : '9'} className="text-center text-muted">
                      Loading profiles...
                    </td>
                  </tr>
                ) : (
                  <tr>
                    <td colSpan={status === 'archived' ? '10' : '9'} className="text-center text-muted">
                      {status === 'active' ? 'No active profiles found.' : 'No archived profiles found.'}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="d-flex flex-wrap justify-content-between align-items-center gap-2 mt-2">
            <div className="small text-muted">
              {firstRowIndex && lastRowIndex ? `Showing ${firstRowIndex}-${lastRowIndex} of ${total} profiles` : 'Showing 0 profiles'}
            </div>
            <div className="d-flex align-items-center gap-2">
              <button
                className="btn btn-sm btn-outline-secondary"
                type="button"
                onClick={() => handlePageChange(page - 1)}
                disabled={page <= 1 || submitting}
              >
                Previous
              </button>
              <span className="small text-muted">
                Page {page} of {totalPages}
              </span>
              <button
                className="btn btn-sm btn-outline-secondary"
                type="button"
                onClick={() => handlePageChange(page + 1)}
                disabled={page >= totalPages || submitting}
              >
                Next
              </button>
            </div>
          </div>
        </div>
      </div>

      {editOpen ? (
        <>
          <div className="modal d-block" tabIndex="-1" role="dialog" aria-modal="true">
            <div className="modal-dialog">
              <div className="modal-content">
                <div className="modal-header">
                  <h5 className="modal-title">Edit Profile</h5>
                  <button type="button" className="btn-close" aria-label="Close" onClick={closeEditModal}></button>
                </div>
                <div className="modal-body">
                  <div className="mb-2">
                    <label htmlFor="editProfileName" className="form-label">
                      Name
                    </label>
                    <input
                      id="editProfileName"
                      type="text"
                      className={`form-control ${editErrors.name ? 'is-invalid' : ''}`}
                      value={editForm.name}
                      onChange={(ev) => updateEditField('name', ev.target.value)}
                      disabled={editBusy}
                    />
                    {editErrors.name ? <div className="invalid-feedback">{editErrors.name}</div> : null}
                  </div>
                  <div className="mb-2">
                    <label htmlFor="editProfileSrCode" className="form-label">
                      SR Code
                    </label>
                    <input
                      id="editProfileSrCode"
                      type="text"
                      className={`form-control ${editErrors.sr_code ? 'is-invalid' : ''}`}
                      value={editForm.sr_code}
                      onChange={(ev) => updateEditField('sr_code', ev.target.value)}
                      disabled={editBusy}
                    />
                    {editErrors.sr_code ? <div className="invalid-feedback">{editErrors.sr_code}</div> : null}
                  </div>
                  <div className="mb-2">
                    <label htmlFor="editProfileGender" className="form-label">
                      Gender
                    </label>
                    <select
                      id="editProfileGender"
                      className={`form-select ${editErrors.gender ? 'is-invalid' : ''}`}
                      value={editForm.gender}
                      onChange={(ev) => updateEditField('gender', ev.target.value)}
                      disabled={editBusy}
                    >
                      <option value="">Select gender</option>
                      <option value="Male">Male</option>
                      <option value="Female">Female</option>
                      <option value="Other">Other</option>
                    </select>
                    {editErrors.gender ? <div className="invalid-feedback">{editErrors.gender}</div> : null}
                  </div>
                  <div className="mb-2">
                    <label htmlFor="editProfileProgram" className="form-label">
                      Program
                    </label>
                    <input
                      id="editProfileProgram"
                      type="text"
                      className={`form-control ${editErrors.program ? 'is-invalid' : ''}`}
                      value={editForm.program}
                      onChange={(ev) => updateEditField('program', ev.target.value)}
                      list="edit-program-options"
                      disabled={editBusy}
                    />
                    <datalist id="edit-program-options">
                      {programs.map((program) => (
                        <option key={program} value={program} />
                      ))}
                    </datalist>
                    {editErrors.program ? <div className="invalid-feedback">{editErrors.program}</div> : null}
                  </div>
                </div>
                <div className="modal-footer">
                  <button type="button" className="btn btn-outline-secondary" onClick={closeEditModal} disabled={editBusy}>
                    Cancel
                  </button>
                  <button type="button" className="btn btn-primary" onClick={saveEditProfile} disabled={editBusy}>
                    {editBusy ? 'Saving...' : 'Save Changes'}
                  </button>
                </div>
              </div>
            </div>
          </div>
          <div className="modal-backdrop fade show"></div>
        </>
      ) : null}
    </section>
  );
}
