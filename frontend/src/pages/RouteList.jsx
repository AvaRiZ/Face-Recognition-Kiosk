import React from 'react';
import { fetchJson } from '../api.js';

export default function RouteListPage() {
  const [rows, setRows] = React.useState([]);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    fetchJson('/api/route-list')
      .then((resp) => setRows(resp.routes || []))
      .catch(() => setRows([]))
      .finally(() => setLoading(false));
  }, []);

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
        <h1>Application Route List</h1>
      </div>
      <div className="row">
        <div className="col-12">
          <div className="card">
            <div className="card-body">
              <h5 className="card-title">All Registered Routes</h5>
              <div className="table-responsive">
                <table className="table table-bordered table-striped table-sm">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>URI</th>
                      <th>Name</th>
                      <th>Action</th>
                      <th>Methods</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.length ? (
                      rows.map((route) => (
                        <tr key={`${route.uri}-${route.name}-${route.i}`}>
                          <td>{route.i}</td>
                          <td>
                            <code>{route.uri}</code>
                          </td>
                          <td>{route.name}</td>
                          <td style={{ fontSize: '12px' }}>{route.action}</td>
                          <td>{route.methods?.join(', ')}</td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td colSpan="5" className="text-center text-muted">
                          No routes found.
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
