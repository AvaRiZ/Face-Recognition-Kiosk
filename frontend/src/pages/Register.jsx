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

export default function RegisterPage() {
  const [info, setInfo] = React.useState(INITIAL_INFO);
  const [loading, setLoading] = React.useState(true);
  const [captureError, setCaptureError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);
  const [result, setResult] = React.useState(null);
  const [courseOptionsByCollege, setCourseOptionsByCollege] = React.useState(DEFAULT_COLLEGE_PROGRAM_MAP);
  const [collegeOptions, setCollegeOptions] = React.useState(DEFAULT_COLLEGE_OPTIONS);
  const [form, setForm] = React.useState({ name: '', sr_code: '', gender: '', college: '', course: '' });

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
        const groupedPrograms = Object.entries(DEFAULT_COLLEGE_PROGRAM_MAP).reduce((acc, [college, programs]) => {
          acc[college] = [...programs];
          return acc;
        }, {});

        const dynamicCourses = (payload.rows || [])
          .map((row) => (row.course || '').trim())
          .filter((course) => course && course !== '-');

        dynamicCourses.forEach((program) => {
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

        setCourseOptionsByCollege(normalizedGroups);
        setCollegeOptions(Object.keys(normalizedGroups));
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

  function updateCollege(value) {
    setForm((prev) => {
      const allowedPrograms = courseOptionsByCollege[value] || [];
      const nextCourse = allowedPrograms.includes(prev.course) ? prev.course : '';
      return { ...prev, college: value, course: nextCourse };
    });
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
    setResult(null);

    if (!form.name.includes(',')) {
      setCaptureError('Use the name format: Last Name, First Name.');
      return;
    }

    if (!form.college) {
      setCaptureError('Please select a college before choosing a program.');
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
  const readyToSubmit = Boolean(info.ready_to_submit || info.has_pending_registration || info.is_in_progress);
  const filteredCourseOptions = form.college ? courseOptionsByCollege[form.college] || [] : [];

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
                          Capture samples from the live camera flow until completion. This page will unlock automatically once all required captures are complete.
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
                        <label htmlFor="college" className="form-label">College</label>
                        <select
                          type="text"
                          id="college"
                          name="college"
                          className="form-select"
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
                      </div>
                      <div className="col-md-6">
                        <label htmlFor="course" className="form-label">Program</label>
                        <input
                          type="text"
                          id="course"
                          name="course"
                          className="form-control"
                          list="course-options"
                          placeholder={form.college ? 'Select or type a program' : 'Select a college first'}
                          value={form.course}
                          onChange={(ev) => updateForm('course', ev.target.value)}
                          disabled={!form.college}
                          required
                        />
                        <datalist id="course-options">
                          {filteredCourseOptions.map((course) => (
                            <option key={course} value={course} />
                          ))}
                        </datalist>
                        <div className="form-text">Program suggestions are filtered by the selected college.</div>
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
