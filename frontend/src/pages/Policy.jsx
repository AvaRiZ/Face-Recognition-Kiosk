import React from 'react';
import { fetchJson } from '../api.js';

export default function PolicyPage() {
  const [policyHtml, setPolicyHtml] = React.useState('');
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    fetchJson('/api/policy')
      .then((resp) => setPolicyHtml(resp.policy || ''))
      .catch(() => setPolicyHtml(''))
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
    <main className="animate__animated animate__fadeIn animate__fast">
      <div className="container">
        <section className="section register min-vh-100 d-flex flex-column align-items-center justify-content-center py-4">
          <div className="container animate__animated animate__fadeInUp animate__fast">
            <div className="row justify-content-center">
              <div className="col-lg-8 col-md-10 d-flex flex-column align-items-center">
                <div className="d-flex justify-content-center mb-3">
                  <a href="/dashboard">
                    <img
                      src="/static/assets/img/bsu-new-logo.png"
                      alt="Logo"
                      style={{ width: '24rem', height: 'auto' }}
                    />
                  </a>
                </div>

                <div className="card mb-3 w-100">
                  <div className="card-body">
                    <div className="pt-4 pb-2 text-center">
                      <h5 className="card-title pb-0 fs-4">Privacy Policy</h5>
                      <p className="text-center small text-muted">Please read the policy carefully.</p>
                    </div>

                    <div className="policy-content" dangerouslySetInnerHTML={{ __html: policyHtml }}></div>
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
