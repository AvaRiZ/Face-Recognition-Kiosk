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
      <section className="section">
        <div className="d-flex justify-content-center align-items-center" style={{ minHeight: '30vh' }}>
          <div className="spinner-border text-primary" role="status"></div>
        </div>
      </section>
    );
  }

  return (
    <section className="section policy-page animate__animated animate__fadeIn animate__fast">
      <div className="pagetitle">
        <h1>Privacy Policy</h1>
        <p className="policy-page-subtitle">Please read the policy carefully.</p>
      </div>

      <div className="card policy-card">
        <div className="card-body">
          <div className="policy-card-header">
            <img
              src="/static/assets/img/bsu-neu-logo.png"
              alt="BatStateU Logo"
              className="policy-card-logo"
            />
            <div>
              <h5 className="card-title">System Privacy Policy</h5>
              <p className="policy-card-copy">This policy is part of the library management system experience.</p>
            </div>
          </div>

          <div className="policy-content" dangerouslySetInnerHTML={{ __html: policyHtml }}></div>
        </div>
      </div>
    </section>
  );
}
