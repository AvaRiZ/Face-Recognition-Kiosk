import React from 'react';

const DEFAULT_COLLEGE_PROGRAM_MAP = {
  'College of Engineering': [
    'Bachelor of Science in Chemical Engineering',
    'Bachelor of Science in Food Engineering',
    'Bachelor of Science in Ceramics Engineering',
    'Bachelor of Science in Metallurgical Engineering',
    'Bachelor of Science in Civil Engineering',
    'Bachelor of Science in Sanitary Engineering',
    'Bachelor of Science in Geodetic Engineering',
    'Bachelor of Science in Geological Engineering',
    'Bachelor of Science in Transportation Systems Engineering',
    'Bachelor of Science in Electrical Engineering',
    'Bachelor of Science in Computer Engineering',
    'Bachelor of Science in Electronics Engineering',
    'Bachelor of Science in Instrumentation and Control Engineering',
    'Bachelor of Science in Mechatronics Engineering',
    'Bachelor of Science in Aerospace Engineering',
    'Bachelor of Science in Biomedical Engineering',
    'Bachelor of Science in Industrial Engineering',
    'Bachelor of Science in Mechanical Engineering',
    'Bachelor of Science in Petroleum Engineering',
    'Bachelor of Science in Automotive Engineering'
  ],
  'College of Architecture, Fine Arts and Design': [
    'Bachelor of Fine Arts and Design Major in Visual Communication',
    'Bachelor of Science in Architecture',
    'Bachelor of Science in Interior Design'
  ],
  'College of Arts and Sciences': [
    'Bachelor of Arts in English Language Studies',
    'Bachelor of Arts in Communication',
    'Bachelor of Science in Biology',
    'Bachelor of Science in Chemistry',
    'Bachelor of Science in Criminology',
    'Bachelor of Science in Development Communication',
    'Bachelor of Science in Mathematics',
    'Bachelor of Science in Psychology',
    'Bachelor of Science in Fisheries and Aquatic Sciences'
  ],
  'College of Accountancy, Business, Economics, and International Hospitality Management': [
    'Bachelor of Science in Accountancy',
    'Bachelor of Science in Business Administration Major in Business Economics',
    'Bachelor of Science in Business Administration Major in Financial Management',
    'Bachelor of Science in Business Administration Major in Human Resource Management',
    'Bachelor of Science in Business Administration Major in Marketing Management',
    'Bachelor of Science in Business Administration Major in Operations Management',
    'Bachelor of Science in Hospitality Management',
    'Bachelor of Science in Tourism Management',
    'Bachelor in Public Administration',
    'Bachelor of Science in Customs Administration',
    'Bachelor of Science in Entrepreneurship'
  ],
  'College of Informatics and Computing Sciences': [
    'Bachelor of Science in Computer Science',
    'Bachelor of Science in Information Technology'
  ],
  'College of Nursing and Allied Health Sciences': [
    'Bachelor of Science in Nursing',
    'Bachelor of Science in Nutrition and Dietetics',
    'Bachelor of Science in Public Health (Disaster Response)'
  ],
  'College of Engineering Technology': [
    'Bachelor of Automotive Engineering Technology',
    'Bachelor of Civil Engineering Technology',
    'Bachelor of Computer Engineering Technology',
    'Bachelor of Drafting Engineering Technology',
    'Bachelor of Electrical Engineering Technology',
    'Bachelor of Electronics Engineering Technology',
    'Bachelor of Food Engineering Technology',
    'Bachelor of Instrumentation and Control Engineering Technology',
    'Bachelor of Mechanical Engineering Technology',
    'Bachelor of Mechatronics Engineering Technology',
    'Bachelor of Welding and Fabrication Engineering Technology'
  ],
  'College of Agriculture and Forestry': [
    'Bachelor of Science in Agriculture',
    'Bachelor of Science in Forestry'
  ],
  'College of Teacher Education': [
    'Bachelor of Elementary Education',
    'Bachelor of Early Childhood Education',
    'Bachelor of Secondary Education Major in Science',
    'Bachelor of Secondary Education Major in English',
    'Bachelor of Secondary Education Major in Filipino',
    'Bachelor of Secondary Education Major in Mathematics',
    'Bachelor of Secondary Education Major in Social Studies',
    'Bachelor of Technology & Livelihood Education Major in Home Economics',
    'Bachelor of Technical-Vocational Teacher Education Major in Garments, Fashion and Design',
    'Bachelor of Technical-Vocational Teacher Education Major in Electronics Technology',
    'Bachelor of Physical Education'
  ]
};

const PROGRAM_TO_COLLEGE = Object.entries(DEFAULT_COLLEGE_PROGRAM_MAP).reduce((acc, [college, programs]) => {
  programs.forEach((program) => {
    acc[program] = college;
  });
  return acc;
}, {});

const DEFAULT_COLLEGE_OPTIONS = Object.keys(DEFAULT_COLLEGE_PROGRAM_MAP);
const OTHER_COLLEGE_LABEL = 'Other / Unassigned';

const INITIAL_INFO = {
  capture_count: 0,
  max_captures: 30,
  has_pending_registration: false,
  is_in_progress: false,
  ready_to_submit: false,
  sample_previews: []
};

const ALLOWED_GENDERS = new Set(['Male', 'Female', 'Other']);
const NAME_PATTERN = /^[A-Za-z][A-Za-z .,'-]{1,79}$/;
const SR_CODE_PATTERN = /^\d{2}-\d{5}$/;
const PROGRAM_PATTERN = /^[A-Za-z0-9&(),./' -]+$/;

function StatusAlert({ tone, icon, title, children }) {
  const variants = {
    danger: { className: 'alert-danger', iconClass: 'bi bi-exclamation-triangle-fill' },
    success: { className: 'alert-success', iconClass: 'bi bi-check-circle-fill' },
    info: { className: 'alert-info', iconClass: 'bi bi-hourglass-split' },
    ready: { className: 'alert-primary', iconClass: 'bi bi-check2-all' }
  };

  const variant = variants[tone] || variants.info;

  return (
    <div className={`alert ${variant.className} d-flex align-items-start gap-2 mb-3`} role="alert">
      <i className={`${icon || variant.iconClass} flex-shrink-0 mt-1`}></i>
      <div>
        <div className="fw-semibold mb-1">{title}</div>
        <div className="small">{children}</div>
      </div>
    </div>
  );
}

function MetricPill({ icon, label, value }) {
  return (
    <div
      className="d-flex align-items-center gap-3 px-3 py-3 rounded-3 bg-light border"
      style={{
        minHeight: '72px'
      }}
    >
      <span
        className="d-inline-flex align-items-center justify-content-center rounded-circle"
        style={{
          width: '34px',
          height: '34px',
          background: 'rgba(65, 84, 241, 0.12)',
          color: '#4154f1'
        }}
      >
        <i className={icon}></i>
      </span>
      <div>
        <div
          className="text-uppercase text-muted"
          style={{ fontSize: '10px', letterSpacing: '0.08em' }}
        >
          {label}
        </div>
        <div className="fw-semibold" style={{ fontSize: '14px', color: '#012970' }}>
          {value}
        </div>
      </div>
    </div>
  );
}

function normalizeSpaces(value) {
  return value.trim().replace(/\s+/g, ' ');
}

function validateRegistrationForm(form) {
  const errors = {};
  const normalizedName = normalizeSpaces(form.name);
  const normalizedSrCode = form.sr_code.trim();
  const normalizedProgram = normalizeSpaces(form.program);

  if (!normalizedName) {
    errors.name = 'Name is required.';
  } else if (!normalizedName.includes(',')) {
    errors.name = 'Use the name format: Last Name, First Name.';
  } else {
    const [lastName, firstName] = normalizedName.split(',', 2).map((part) => part.trim());
    if (!lastName || !firstName) {
      errors.name = 'Use the name format: Last Name, First Name.';
    } else if (!NAME_PATTERN.test(normalizedName)) {
      errors.name = 'Name contains invalid characters.';
    }
  }

  if (!normalizedSrCode) {
    errors.sr_code = 'SR Code is required.';
  } else if (!SR_CODE_PATTERN.test(normalizedSrCode)) {
    errors.sr_code = 'SR Code must use the format 23-12345.';
  }

  if (!form.gender) {
    errors.gender = 'Gender is required.';
  } else if (!ALLOWED_GENDERS.has(form.gender)) {
    errors.gender = 'Please select a valid gender.';
  }

  if (!form.college) {
    errors.college = 'Please select a college before choosing a program.';
  }

  if (!normalizedProgram) {
    errors.program = 'Program is required.';
  } else if (normalizedProgram.length < 4 || normalizedProgram.length > 120) {
    errors.program = 'Program must be between 4 and 120 characters.';
  } else if (!PROGRAM_PATTERN.test(normalizedProgram)) {
    errors.program = 'Program contains invalid characters.';
  }

  return {
    errors,
    normalized: {
      name: normalizedName,
      sr_code: normalizedSrCode,
      gender: form.gender,
      college: form.college,
      program: normalizedProgram
    }
  };
}

export default function RegisterPage() {
  const [info, setInfo] = React.useState(INITIAL_INFO);
  const [loading, setLoading] = React.useState(true);
  const [captureError, setCaptureError] = React.useState('');
  const [fieldErrors, setFieldErrors] = React.useState({});
  const [submitting, setSubmitting] = React.useState(false);
  const [result, setResult] = React.useState(null);
  const [programOptionsByCollege, setProgramOptionsByCollege] = React.useState(DEFAULT_COLLEGE_PROGRAM_MAP);
  const [collegeOptions, setCollegeOptions] = React.useState(DEFAULT_COLLEGE_OPTIONS);
  const [form, setForm] = React.useState({ name: '', sr_code: '', gender: '', college: '', program: '' });

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

    async function loadProgramOptions() {
      try {
        const response = await fetch('/api/registered-profiles', { credentials: 'include' });
        if (!response.ok) {
          return;
        }
        const payload = await response.json();
        if (cancelled) {
          return;
        }

        const groupedPrograms = Object.entries(DEFAULT_COLLEGE_PROGRAM_MAP).reduce((acc, [college, programs]) => {
          acc[college] = [...programs];
          return acc;
        }, {});

        const dynamicPrograms = (payload.rows || [])
          .map((row) => (row.program || '').trim())
          .filter((program) => program && program !== '-');

        dynamicPrograms.forEach((program) => {
          const mappedCollege = PROGRAM_TO_COLLEGE[program] || OTHER_COLLEGE_LABEL;
          if (!groupedPrograms[mappedCollege]) {
            groupedPrograms[mappedCollege] = [];
          }
          if (!groupedPrograms[mappedCollege].includes(program)) {
            groupedPrograms[mappedCollege].push(program);
          }
        });

        const normalizedGroups = Object.fromEntries(
          Object.entries(groupedPrograms)
            .filter(([, programs]) => programs.length > 0)
            .map(([college, programs]) => [college, [...programs].sort((a, b) => a.localeCompare(b))])
        );

        setProgramOptionsByCollege(normalizedGroups);
        setCollegeOptions(Object.keys(normalizedGroups));
      } catch {
        // Keep fallback options.
      }
    }

    loadProgramOptions();
    return () => {
      cancelled = true;
    };
  }, []);

  function updateForm(key, value) {
    setFieldErrors((prev) => {
      if (!prev[key]) {
        return prev;
      }
      const next = { ...prev };
      delete next[key];
      return next;
    });
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function updateCollege(value) {
    setForm((prev) => {
      const allowedPrograms = programOptionsByCollege[value] || [];
      return {
        ...prev,
        college: value,
        program: allowedPrograms.includes(prev.program) ? prev.program : ''
      };
    });
  }

  async function handleReset() {
    setCaptureError('');
    setFieldErrors({});
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
        is_in_progress: false,
        ready_to_submit: false
      }));
    } catch {
      setCaptureError('Unable to reset the current capture session.');
    }
  }

  async function handleSubmit(ev) {
    ev.preventDefault();
    setCaptureError('');
    setFieldErrors({});
    setResult(null);

    if (!info.ready_to_submit || !info.has_pending_registration) {
      setCaptureError('Registration capture is not complete yet. Finish all required face samples before saving.');
      return;
    }

    const { errors, normalized } = validateRegistrationForm(form);
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      setCaptureError(Object.values(errors)[0]);
      return;
    }

    setSubmitting(true);

    const formData = new FormData();
    formData.append('name', normalized.name);
    formData.append('sr_code', normalized.sr_code);
    formData.append('gender', normalized.gender);
    formData.append('program', normalized.program);

    try {
      const response = await fetch('/register', {
        method: 'POST',
        credentials: 'include',
        body: formData
      });
      const payload = await response.json();

      if (!response.ok || !payload.success) {
        if (payload.field) {
          setFieldErrors({ [payload.field]: payload.message || 'Please review this field.' });
        } else if ((payload.message || '').toLowerCase().includes('sr code')) {
          setFieldErrors({ sr_code: payload.message || 'This SR Code is already registered.' });
        }
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
  const readyToSubmit = Boolean(info.ready_to_submit && info.has_pending_registration);
  const filteredProgramOptions = form.college ? programOptionsByCollege[form.college] || [] : [];
  const sampleCount = info.sample_previews?.length || 0;

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
        <section className="section register min-vh-100 d-flex flex-column justify-content-center py-4">
          <div className="container animate__animated animate__fadeInUp animate__fast">
            <div className="d-flex justify-content-center mb-4">
              <a href="/login">
                <img
                  src="/static/assets/img/bsu-new-logo.png"
                  alt="BatStateU Logo"
                  style={{ width: '24rem', height: 'auto' }}
                />
              </a>
            </div>

            <div className="row justify-content-center">
              <div className="col-xl-8 col-lg-9">
                <div className="card">
                  <div className="card-body p-4 p-xl-5">
                    <div className="d-flex flex-wrap justify-content-between align-items-start gap-3 mb-4">
                      <div>
                        <div className="text-uppercase text-muted fw-semibold" style={{ fontSize: '11px', letterSpacing: '0.08em' }}>
                          Registration Queue
                        </div>
                        <h5 className="card-title mb-1">
                          Complete student registration
                        </h5>
                        <p className="text-muted mb-0 small">
                          Review the captured samples, then fill out the form below to complete the student profile.
                        </p>
                      </div>
                      <span className={`badge rounded-pill ${readyToSubmit ? 'bg-success-subtle text-success' : 'bg-warning-subtle text-warning'}`}>
                        {readyToSubmit ? 'Ready for submission' : 'Capture in progress'}
                      </span>
                    </div>

                    {captureError ? (
                      <StatusAlert tone="danger" title="Registration error">
                        {captureError}
                      </StatusAlert>
                    ) : null}

                    {result?.profile ? (
                      <StatusAlert tone="success" title={result.message}>
                        Saved as {result.profile.name} ({result.profile.sr_code}) - {result.profile.gender},{' '}
                        {result.profile.program}. Redirecting to the profile list now.
                      </StatusAlert>
                    ) : null}

                    {!readyToSubmit ? (
                      <StatusAlert tone="info" title="Waiting for CLI capture">
                        Capture samples from the live camera flow until the system completes the required set. This page
                        unlocks automatically when registration is ready.
                      </StatusAlert>
                    ) : (
                      <StatusAlert tone="ready" title="Samples ready">
                        The required face samples are already available. You can now submit the student details below.
                      </StatusAlert>
                    )}

                    <div className="row g-3 mb-4">
                      <div className="col-md-4">
                        <MetricPill icon="bi bi-camera" label="Captured" value={`${info.capture_count}/${info.max_captures}`} />
                      </div>
                      <div className="col-md-4">
                        <MetricPill
                          icon="bi bi-images"
                          label="Preview Tiles"
                          value={sampleCount ? `${sampleCount} sample${sampleCount > 1 ? 's' : ''}` : 'No previews yet'}
                        />
                      </div>
                      <div className="col-md-4">
                        <MetricPill
                          icon="bi bi-patch-check"
                          label="Submission"
                          value={readyToSubmit ? 'Ready to save' : 'Waiting for CLI capture'}
                        />
                      </div>
                    </div>

                    <div className="mb-4">
                      <div className="d-flex justify-content-between small text-muted mb-2">
                        <span>Capture progress</span>
                        <span>{progressPercent}%</span>
                      </div>
                      <div className="progress" style={{ height: '8px' }}>
                        <div
                          className="progress-bar"
                          style={{
                            width: `${progressPercent}%`,
                            transition: 'width 0.35s ease'
                          }}
                        />
                      </div>
                    </div>

                    <div className="rounded-3 border bg-light p-3 p-md-4 mb-4">
                      <div className="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-3">
                        <div className="fw-semibold" style={{ color: '#012970' }}>Captured face previews</div>
                        <div className="small text-muted">{sampleCount} preview{sampleCount === 1 ? '' : 's'} available</div>
                      </div>

                      {sampleCount ? (
                        <div className="d-flex gap-2 flex-wrap">
                          {info.sample_previews.map((sample, index) => (
                            <div
                              key={sample.id ?? index}
                              className="text-center"
                              style={{
                                width: '88px',
                                padding: '6px',
                                border: '1px solid #e6e7eb',
                                background: '#fff'
                              }}
                            >
                              <img
                                src={sample.image_url}
                                alt={`Captured sample ${index + 1}`}
                                className="rounded-3 w-100"
                                style={{ aspectRatio: '1 / 1', objectFit: 'cover', display: 'block' }}
                              />
                              <div className="small text-muted mt-2">Q {sample.quality_score}</div>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className="rounded-3 d-flex align-items-center justify-content-center text-center px-4 border border-secondary-subtle bg-white text-muted" style={{ minHeight: '112px', borderStyle: 'dashed' }}>
                          No preview tiles yet. Stay on the CCTV capture flow until the sample set is completed.
                        </div>
                      )}
                    </div>

                    <form className="row g-3" onSubmit={handleSubmit}>
                      <div className="col-12">
                        <div className="rounded-3 border bg-light p-3">
                          <div className="fw-semibold mb-2" style={{ color: '#012970' }}>Before saving</div>
                          <ul className="mb-0 ps-3 text-muted" style={{ fontSize: '13px', lineHeight: 1.7 }}>
                            <li>Use the format Last Name, First Name.</li>
                            <li>Select the correct college first so the program list is filtered properly.</li>
                            <li>Reset samples only if the captured face set is wrong or incomplete.</li>
                          </ul>
                        </div>
                      </div>

                      <div className="col-12">
                        <label htmlFor="name" className="form-label" style={{ fontSize: '13px' }}>
                          Last Name, First Name
                        </label>
                        <input
                          type="text"
                          id="name"
                          name="name"
                          className={`form-control ${fieldErrors.name ? 'is-invalid' : ''}`}
                          placeholder="Dela Cruz, Juan"
                          value={form.name}
                          onChange={(ev) => updateForm('name', ev.target.value)}
                          required
                        />
                        {fieldErrors.name ? <div className="invalid-feedback">{fieldErrors.name}</div> : null}
                        <div className="form-text">Enter the student name using the official Last Name, First Name format.</div>
                      </div>

                      <div className="col-md-6">
                        <label htmlFor="sr_code" className="form-label" style={{ fontSize: '13px' }}>
                          SR Code
                        </label>
                        <input
                          type="text"
                          id="sr_code"
                          name="sr_code"
                          className={`form-control ${fieldErrors.sr_code ? 'is-invalid' : ''}`}
                          placeholder="23-12345"
                          value={form.sr_code}
                          onChange={(ev) => updateForm('sr_code', ev.target.value)}
                          required
                        />
                        {fieldErrors.sr_code ? <div className="invalid-feedback">{fieldErrors.sr_code}</div> : null}
                        <div className="form-text">Each student must have one unique SR Code. Duplicate SR Codes cannot be registered.</div>
                      </div>

                      <div className="col-md-6">
                        <label htmlFor="gender" className="form-label" style={{ fontSize: '13px' }}>
                          Gender
                        </label>
                        <select
                          id="gender"
                          name="gender"
                          className={`form-select ${fieldErrors.gender ? 'is-invalid' : ''}`}
                          value={form.gender}
                          onChange={(ev) => updateForm('gender', ev.target.value)}
                          required
                        >
                          <option value="">Select gender</option>
                          <option value="Male">Male</option>
                          <option value="Female">Female</option>
                          <option value="Other">Other</option>
                        </select>
                        {fieldErrors.gender ? <div className="invalid-feedback">{fieldErrors.gender}</div> : null}
                      </div>

                      <div className="col-md-6">
                        <label htmlFor="college" className="form-label" style={{ fontSize: '13px' }}>
                          College
                        </label>
                        <select
                          id="college"
                          name="college"
                          className={`form-select ${fieldErrors.college ? 'is-invalid' : ''}`}
                          value={form.college}
                          onChange={(ev) => updateCollege(ev.target.value)}
                          required
                        >
                          <option value="">Select college</option>
                          {collegeOptions.map((college) => (
                            <option key={college} value={college}>
                              {college}
                            </option>
                          ))}
                        </select>
                        {fieldErrors.college ? <div className="invalid-feedback">{fieldErrors.college}</div> : null}
                      </div>

                      <div className="col-md-6">
                        <label htmlFor="program" className="form-label" style={{ fontSize: '13px' }}>
                          Program
                        </label>
                        <input
                          type="text"
                          id="program"
                          name="program"
                          className={`form-control ${fieldErrors.program ? 'is-invalid' : ''}`}
                          list="program-options"
                          placeholder={form.college ? 'Select or type a program' : 'Select a college first'}
                          value={form.program}
                          onChange={(ev) => updateForm('program', ev.target.value)}
                          disabled={!form.college}
                          required
                        />
                        {fieldErrors.program ? <div className="invalid-feedback">{fieldErrors.program}</div> : null}
                        <datalist id="program-options">
                          {filteredProgramOptions.map((program) => (
                            <option key={program} value={program} />
                          ))}
                        </datalist>
                        <div className="form-text">Program suggestions are filtered based on the selected college.</div>
                      </div>

                      <div className="col-12">
                        <div className="alert alert-info d-flex align-items-start gap-3 mb-0">
                          <i className="bi bi-info-circle-fill mt-1"></i>
                          <div className="small">
                            The live recognition camera continues running while this page is open. Only the captured CLI
                            face samples are used for this registration record.
                          </div>
                        </div>
                      </div>

                      <div className="col-12 pt-1">
                        <div className="d-flex flex-wrap gap-2 align-items-center">
                          <button
                            className="btn btn-primary px-4"
                            type="submit"
                            disabled={submitting || !readyToSubmit}
                          >
                            {submitting ? (
                              <>
                                <span className="spinner-border spinner-border-sm me-2" role="status" />
                                Saving Registration...
                              </>
                            ) : (
                              <>
                                <i className="bi bi-person-check-fill me-2"></i>
                                Complete Registration
                              </>
                            )}
                          </button>

                          <button
                            className="btn btn-outline-secondary"
                            type="button"
                            onClick={handleReset}
                          >
                            <i className="bi bi-arrow-counterclockwise me-2"></i>
                            Reset Samples
                          </button>

                          <a
                            className="btn btn-outline-danger ms-lg-auto"
                            href="/registered-profiles"
                          >
                            <i className="bi bi-people me-2"></i>
                            View Profiles
                          </a>
                        </div>
                      </div>
                    </form>
                  </div>
                </div>
              </div>
            </div>

            <footer className="text-center mt-4">
              <div className="copyright">
                <strong>Batangas State University The National Engineering University</strong>
              </div>
            </footer>
          </div>
        </section>
      </div>
    </main>
  );
}
