import React from 'react';

export default function UnauthorizedPage() {
  return (
    <section className="section error-403 d-flex flex-column align-items-center justify-content-center text-center">
      <h1>403</h1>
      <h2>Access Forbidden</h2>
      <p className="mb-4">
        You do not have permission to view this page with your current account.
      </p>
      <a className="btn" href="/dashboard">
        Back to Dashboard
      </a>
    </section>
  );
}
