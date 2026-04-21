import React from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { fetchJson } from '../api.js';
import { useSession } from '../App.jsx';

export default function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { refresh } = useSession();
  const [error, setError] = React.useState('');
  const [form, setForm] = React.useState({ username: '', password: '' });

  const from = location.state?.from?.pathname || '/dashboard';

  function updateForm(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function handleSubmit(ev) {
    ev.preventDefault();
    setError('');
    fetchJson('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify(form)
    })
      .then(() => {
        refresh();
        navigate(from, { replace: true });
      })
      .catch((err) => {
        setError(err.data?.message || 'Login failed');
      });
  }

  return (
    <main className="auth-page animate__animated animate__fadeIn animate__fast">
      <div className="container">
        <section className="section register min-vh-100 d-flex flex-column align-items-center justify-content-center py-4">
          <div className="container animate__animated animate__fadeInUp animate__fast">
            <div className="d-flex justify-content-center mb-3">
              <a href="/login">
                <img
                  src="/static/assets/img/bsu-new-logo.png"
                  alt="BatStateU Logo"
                  style={{ width: '24rem', height: 'auto' }}
                />
              </a>
            </div>

            <div className="row justify-content-center">
              <div className="col-lg-4 col-md-6 d-flex flex-column align-items-center justify-content-center">
                <div className="card mb-3">
                  <div className="card-body">
                    <div className="pt-4 pb-2 animate__animated animate__fadeInUp animate__fast">
                      <h5 className="card-title text-center pb-0 fs-4">Login to Your Account</h5>
                      <p className="text-center small">Enter your username and password to login</p>
                    </div>

                    {error ? <div className="alert alert-danger mb-3">{error}</div> : null}

                    <form className="row g-3 needs-validation animate__animated animate__fadeInUp animate__fast" onSubmit={handleSubmit}>
                      <div className="col-12">
                        <label htmlFor="username" className="form-label">Username</label>
                        <div className="input-group has-validation">
                          <input
                            id="username"
                            type="text"
                            className="form-control"
                            name="username"
                            required
                            placeholder="Enter your username"
                            autoComplete="username"
                            autoFocus
                            value={form.username}
                            onChange={(ev) => updateForm('username', ev.target.value)}
                          />
                        </div>
                      </div>

                      <div className="col-12">
                        <label htmlFor="password" className="form-label">Password</label>
                        <input
                          id="password"
                          type="password"
                          className="form-control"
                          name="password"
                          required
                          placeholder="Enter your password"
                          autoComplete="current-password"
                          value={form.password}
                          onChange={(ev) => updateForm('password', ev.target.value)}
                        />
                      </div>

                      <div className="col-12">
                        <button type="submit" className="btn btn-primary w-100">Login</button>
                      </div>

                      <div className="col-12 text-center">
                        <small className="text-muted">Default superadmin: <strong>superadmin / password</strong></small>
                      </div>
                    </form>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
