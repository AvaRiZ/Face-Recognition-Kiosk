import React from 'react';
import { fetchJson } from '../api.js';

function formatRole(role) {
  if (role === 'super_admin') return 'SuperAdmin';
  if (role === 'library_admin') return 'Admin';
  return 'Staff';
}

export default function ManageUsersPage() {
  const [rows, setRows] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [form, setForm] = React.useState({
    full_name: '',
    username: '',
    password: '',
    role: 'library_admin'
  });

  const loadRows = React.useCallback(() => {
    setLoading(true);
    fetchJson('/api/manage-users')
      .then((resp) => setRows(resp.rows || []))
      .catch(() => setRows([]))
      .finally(() => setLoading(false));
  }, []);

  React.useEffect(() => {
    loadRows();
  }, [loadRows]);

  function updateForm(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function handleCreate(ev) {
    ev.preventDefault();
    fetchJson('/api/manage-users/create', {
      method: 'POST',
      body: JSON.stringify(form)
    })
      .then(() => {
        setForm({ full_name: '', username: '', password: '', role: 'library_admin' });
        loadRows();
      })
      .catch(() => undefined);
  }

  function toggleStaff(staffId) {
    fetchJson(`/api/manage-users/toggle/${staffId}`, { method: 'POST' })
      .then(loadRows)
      .catch(() => undefined);
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
        <h1>Manage Users</h1>
      </div>
      <div className="row g-3">
        <div className="col-lg-5">
          <div className="card">
            <div className="card-body">
              <h5 className="card-title">Add Admin or Staff</h5>
              <form className="row g-3" onSubmit={handleCreate}>
                <div className="col-12">
                  <label className="form-label" htmlFor="full_name">Full Name</label>
                  <input
                    className="form-control"
                    id="full_name"
                    name="full_name"
                    value={form.full_name}
                    onChange={(ev) => updateForm('full_name', ev.target.value)}
                    required
                  />
                </div>
                <div className="col-12">
                  <label className="form-label" htmlFor="username">Username</label>
                  <input
                    className="form-control"
                    id="username"
                    name="username"
                    value={form.username}
                    onChange={(ev) => updateForm('username', ev.target.value)}
                    required
                  />
                </div>
                <div className="col-12">
                  <label className="form-label" htmlFor="password">Password</label>
                  <input
                    type="password"
                    className="form-control"
                    id="password"
                    name="password"
                    value={form.password}
                    onChange={(ev) => updateForm('password', ev.target.value)}
                    minLength={8}
                    required
                  />
                  <div className="form-text">Minimum 8 characters.</div>
                </div>
                <div className="col-12">
                  <label className="form-label" htmlFor="role">Role</label>
                  <select
                    className="form-select"
                    id="role"
                    name="role"
                    value={form.role}
                    onChange={(ev) => updateForm('role', ev.target.value)}
                    required
                  >
                    <option value="library_admin">Admin</option>
                    <option value="library_staff">Staff</option>
                  </select>
                </div>
                <div className="col-12">
                  <button className="btn btn-primary" type="submit">Create User</button>
                </div>
              </form>
            </div>
          </div>
        </div>

        <div className="col-lg-7">
          <div className="card">
            <div className="card-body">
              <h5 className="card-title">Staff Accounts</h5>
              <div className="table-responsive">
                <table className="table table-hover align-middle">
                  <thead>
                    <tr>
                      <th>Full Name</th>
                      <th>Username</th>
                      <th>Role</th>
                      <th>Status</th>
                      <th className="text-end">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.length ? (
                      rows.map((row) => (
                        <tr key={row.staff_id}>
                          <td>{row.full_name}</td>
                          <td>{row.username}</td>
                          <td>{formatRole(row.role)}</td>
                          <td>
                            {row.is_active ? (
                              <span className="badge bg-success">Active</span>
                            ) : (
                              <span className="badge bg-secondary">Inactive</span>
                            )}
                          </td>
                          <td className="text-end">
                            {row.role !== 'super_admin' ? (
                              <button
                                type="button"
                                className={`btn btn-sm ${row.is_active ? 'btn-outline-danger' : 'btn-outline-success'}`}
                                onClick={() => toggleStaff(row.staff_id)}
                              >
                                {row.is_active ? 'Deactivate' : 'Activate'}
                              </button>
                            ) : (
                              <span className="text-muted small">Protected</span>
                            )}
                          </td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td colSpan="5" className="text-center text-muted">
                          No staff accounts found.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
