import React from 'react';

const DEFAULT_PROGRAM_OPTIONS = [
  'BS Information Technology',
  'BS Computer Science',
  'BS Information Systems',
  'BS Computer Engineering',
  'BS Industrial Engineering',
  'BS Civil Engineering',
  'BS Electrical Engineering',
  'BS Mechanical Engineering',
  'BS Electronics Engineering',
  'BS Nursing',
  'BS Psychology',
  'BS Accountancy',
  'BS Business Administration',
  'BS Hospitality Management',
  'BS Education'
];

const INITIAL_INFO = {
  capture_count: 0,
  max_captures: 10,
  has_pending_registration: false,
  is_in_progress: false,
  sample_previews: []
};

export default function RegisterPage() {
  const [info, setInfo] = React.useState(INITIAL_INFO);
  const [loading, setLoading] = React.useState(true);
  const [captureError, setCaptureError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);
  const [result, setResult] = React.useState(null);
  const [courseOptions, setCourseOptions] = React.useState(DEFAULT_PROGRAM_OPTIONS);
  const [form, setForm] = React.useState({ name: '', sr_code: '', gender: '', course: '' });

  React.useEffect(() => {
    let cancelled = false;

    async function loadInfo() {
      try {
        const response = await fetch('/api/register-info', { credentials: 'include' });
        if (!response.ok) {
          return;
        }
        const payload = await response.json();
        if (!cancelled) {
          setInfo((prev) => ({ ...prev, ...payload }));
        }
      } catch {
        // Ignore polling failures and keep the latest state we have.
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    loadInfo();
    const timer = window.setInterval(loadInfo, 1500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  React.useEffect(() => {
    let cancelled = false;

    async function loadCourseOptions() {
      try {
        const response = await fetch('/api/registered-profiles', { credentials: 'include' });
        if (!response.ok) {
          return;
        }
        const payload = await response.json();
        if (cancelled) {
          return;
        }
        const dynamicCourses = (payload.rows || [])
          .map((row) => (row.course || '').trim())
          .filter((course) => course && course !== '-');
        const merged = Array.from(new Set([...dynamicCourses, ...DEFAULT_PROGRAM_OPTIONS])).sort((a, b) =>
          a.localeCompare(b)
        );
        setCourseOptions(merged);
      } catch {
        // Keep fallback options.
      }
    }

    loadCourseOptions();
    return () => {
      cancelled = true;
    };
  }, []);

  function updateForm(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function handleReset() {
    setCaptureError('');
    setResult(null);

    try {
      const response = await fetch('/api/register-reset', {
        method: 'POST',
        credentials: 'include'
      });
      const payload = await response.json();
      if (!response.ok || payload.success === false) {
        setCaptureError(payload.message || 'Unable to reset the capture session.');
        return;
      }
      setInfo((prev) => ({
        ...prev,
        capture_count: payload.capture_count ?? 0,
        max_captures: payload.max_captures ?? prev.max_captures,
        has_pending_registration: false,
        is_in_progress: false
      }));
    } catch {
      setCaptureError('Unable to reset the current capture session.');
    }
  }

  async function handleSubmit(ev) {
    ev.preventDefault();
    setCaptureError('');
    setResult(null);

    if (!form.name.includes(',')) {
      setCaptureError('Use the name format: Last Name, First Name.');
      return;
    }

    setSubmitting(true);

    const formData = new FormData();
    formData.append('name', form.name);
    formData.append('sr_code', form.sr_code);
    formData.append('gender', form.gender);
    formData.append('course', form.course);

    try {
      const response = await fetch('/register', {
        method: 'POST',
        credentials: 'include',
        body: formData
      });
      const payload = await response.json();

      if (!response.ok || !payload.success) {
        setCaptureError(payload.message || 'Unable to save the registration.');
        return;
      }

      setResult(payload);
      setInfo((prev) => ({
        ...prev,
        capture_count: 0,
        has_pending_registration: false,
        is_in_progress: false
      }));
      window.setTimeout(() => {
        window.location.href = payload.redirect_url || '/registered-profiles';
      }, 1200);
    } catch {
      setCaptureError('Unable to reach the server. Please try again.');
    } finally {
      setSubmitting(false);
    }
  }

  const progressPercent = info.max_captures
    ? Math.min(100, Math.round((info.capture_count / info.max_captures) * 100))
    : 0;
  const readyToSubmit = Boolean(info.has_pending_registration || info.is_in_progress);

  if (loading) {
    return (
      <div className="d-flex justify-content-center align-items-center" style={{ minHeight: '30vh' }}>
        <div className="spinner-border text-primary" role="status"></div>
      </div>
    );
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
                  style={{ width: '20rem', height: 'auto' }}
                />
              </a>
            </div>

            <div className="row justify-content-center">
              <div className="col-xl-8 col-lg-10">
                <div className="card mb-3 shadow-sm border-0">
                  <div className="card-body p-4 p-xl-5">
                    <div className="d-flex justify-content-between align-items-start mb-3">
                      <div>
                        <span className="badge bg-primary-subtle text-primary mb-2">CLI Capture</span>
                        <h5 className="card-title fs-4 mb-1">Complete registration</h5>
                        <p className="text-muted small mb-0">
                          Face samples are captured from the live CCTV window. This page only collects student details and saves the profile.
                        </p>
                      </div>
                      <span className="badge bg-light text-dark">{info.capture_count}/{info.max_captures}</span>
                    </div>

                    <div className="progress mb-3" style={{ height: '8px' }}>
                      <div className="progress-bar" style={{ width: `${progressPercent}%` }}></div>
                    </div>

                    {captureError ? (
                      <div className="alert alert-danger mb-3" role="alert">
                        <i className="bi bi-exclamation-triangle me-2"></i>
                        {captureError}
                      </div>
                    ) : null}

                    {result?.profile ? (
                      <div className="alert alert-success mb-3" role="alert">
                        <div className="fw-semibold mb-1">{result.message}</div>
                        <div className="small">
                          Saved as {result.profile.name} ({result.profile.sr_code}) - {result.profile.gender}, {result.profile.course}.
                        </div>
                        <div className="small">Redirecting to the website record view...</div>
                      </div>
                    ) : null}

                    {!readyToSubmit ? (
                      <div className="alert alert-info mb-4" role="alert">
                        <div className="fw-semibold mb-1">Waiting for CLI capture</div>
                        <div className="small">
                          Press <strong>N</strong> in the CCTV window and let the CLI capture {info.max_captures} stable samples. This page will unlock automatically once capture is complete.
                        </div>
                      </div>
                    ) : (
                      <div className="alert alert-primary mb-4" role="alert">
                        <div className="fw-semibold mb-1">Samples ready</div>
                        <div className="small">
                          Required CLI samples are already captured. Fill in the form below to save this profile to the database.
                        </div>
                      </div>
                    )}

                    {info.sample_previews?.length ? (
                      <div className="mb-4">
                        <div className="small text-muted mb-2">Captured face samples</div>
                        <div className="d-flex gap-2 flex-wrap">
                          {info.sample_previews.map((sample, index) => (
                            <div key={sample.id ?? index} className="border rounded p-1 bg-white" style={{ width: '88px' }}>
                              <img
                                src={sample.image_url}
                                alt={`Captured sample ${index + 1}`}
                                className="rounded w-100"
                                style={{ aspectRatio: '1 / 1', objectFit: 'cover' }}
                              />
                              <div className="small text-center text-muted mt-1">Q {sample.quality_score}</div>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    <form className="row g-3" onSubmit={handleSubmit}>
                      <div className="col-12">
                        <label htmlFor="name" className="form-label">Last Name, First Name</label>
                        <input
                          type="text"
                          id="name"
                          name="name"
                          className="form-control"
                          placeholder="Dela Cruz, Juan"
                          value={form.name}
                          onChange={(ev) => updateForm('name', ev.target.value)}
                          required
                        />
                        <div className="form-text">Enter the full name as: Last Name, First Name.</div>
                      </div>
                      <div className="col-md-6">
                        <label htmlFor="sr_code" className="form-label">SR Code</label>
                        <input
                          type="text"
                          id="sr_code"
                          name="sr_code"
                          className="form-control"
                          value={form.sr_code}
                          onChange={(ev) => updateForm('sr_code', ev.target.value)}
                          required
                        />
                      </div>
                      <div className="col-md-6">
                        <label htmlFor="gender" className="form-label">Gender</label>
                        <select
                          id="gender"
                          name="gender"
                          className="form-select"
                          value={form.gender}
                          onChange={(ev) => updateForm('gender', ev.target.value)}
                          required
                        >
                          <option value="">Select gender</option>
                          <option value="Male">Male</option>
                          <option value="Female">Female</option>
                          <option value="Other">Other</option>
                        </select>
                      </div>
                      <div className="col-md-6">
                        <label htmlFor="course" className="form-label">Course / Program</label>
                        <input
                          type="text"
                          id="course"
                          name="course"
                          className="form-control"
                          list="course-options"
                          placeholder="Select or type a course/program"
                          value={form.course}
                          onChange={(ev) => updateForm('course', ev.target.value)}
                          required
                        />
                        <datalist id="course-options">
                          {courseOptions.map((course) => (
                            <option key={course} value={course} />
                          ))}
                        </datalist>
                      </div>
                      <div className="col-12">
                        <div className="border rounded p-3 bg-light small text-muted">
                          The live recognition camera stays running while this page is open. Only the captured CLI samples will be used for registration.
                        </div>
                      </div>
                      <div className="col-12 pt-2 d-grid gap-2 d-sm-flex">
                        <button className="btn btn-primary px-4" type="submit" disabled={submitting || !readyToSubmit}>
                          {submitting ? 'Saving Registration...' : 'Complete Registration'}
                        </button>
                        <button className="btn btn-outline-secondary" type="button" onClick={handleReset}>
                          Reset Captured Samples
                        </button>
                        <a className="btn btn-light border" href="/registered-profiles">
                          View Registered Profiles
                        </a>
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
