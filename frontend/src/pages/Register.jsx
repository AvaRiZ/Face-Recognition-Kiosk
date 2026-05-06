import React from 'react';
import { confirmAction, getErrorMessage, showError, showSuccess } from '../alerts.js';

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
    'Bachelor of Science in Automotive Engineering',
    'Bachelor of Science in Naval Architecture and Marine Engineering'
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
  web_session_active: false,
  allow_unknown_override: false,
  session_expired: false,
  ready_to_submit: false,
  status_reason_code: null,
  status_reason_message: '',
  status_updated_at: null,
  session_timeout_seconds: 0,
  session_started_at: null,
  last_activity_at: null,
  session_expires_at: null,
  seconds_until_expiry: null,
  required_poses: [],
  current_pose: null,
  current_pose_index: 0,
  pose_progress: {},
  total_progress: {
    captured: 0,
    required: 0,
    retained: 0,
    retained_required: 0
  },
  sample_previews: [],
  camera_stream: {
    state: 'unknown',
    message: 'Camera status unavailable.'
  }
};

const INITIAL_FORM = { name: '', sr_code: '', gender: '', college: '', program: '' };
const REGISTRATION_TYPES = {
  student: 'student',
  visitor: 'visitor'
};

const ALLOWED_GENDERS = new Set(['Male', 'Female', 'Other']);
const NAME_PATTERN = /^[A-Za-z][A-Za-z .,'-]{1,79}$/;
const SR_CODE_PATTERN = /^\d{2}-\d{5}$/;
const PROGRAM_PATTERN = /^[A-Za-z0-9&(),./' -]+$/;
const EXPIRING_SOON_THRESHOLD_SECONDS = 120;

const STATUS_REASON_VIEWS = {
  worker_unattached: {
    title: 'Worker runtime unavailable',
    message: 'The entry recognition worker is offline.',
    action: 'Start the entry worker, wait for heartbeat sync, then start a new session.'
  },
  session_already_active: {
    title: 'Session already active',
    message: 'A registration session is already active and waiting for a student.',
    action: 'Continue the current flow or cancel the session before starting a new one.'
  },
  capture_in_progress: {
    title: 'Capture in progress',
    message: 'A registration capture is already in progress.',
    action: 'Keep the student in frame until capture completes, or cancel/reset if needed.'
  },
  capture_complete: {
    title: 'Capture complete',
    message: 'Required face samples are complete and ready for profile submission.',
    action: 'Review previews, fill in details, and complete registration.'
  },
  session_expired: {
    title: 'Session expired',
    message: 'The registration session expired due to inactivity.',
    action: 'Start a new session to continue registration capture.'
  },
  session_started: {
    title: 'Session started',
    message: 'Session is active and waiting to lock onto one unregistered student.',
    action: 'Keep one student centered and well-lit in the camera flow.'
  },
  session_canceled: {
    title: 'Session canceled',
    message: 'The active registration session was canceled.',
    action: 'Start a new session when ready.'
  },
  detection_paused: {
    title: 'Detection paused',
    message: 'Detection is paused while website registration uses the camera stream.',
    action: 'Resume detection from the host flow when ready.'
  },
  stream_connecting: {
    title: 'Camera reconnecting',
    message: 'Camera stream is connecting.',
    action: 'Keep this page open while stream health recovers.'
  },
  stream_reconnecting: {
    title: 'Camera reconnecting',
    message: 'Camera stream is reconnecting.',
    action: 'Keep this page open while stream health recovers.'
  },
  stream_disconnected: {
    title: 'Camera offline',
    message: 'Camera stream is unavailable.',
    action: 'Check the CCTV source or camera index, then retry.'
  },
  possible_existing_match: {
    title: 'Possible existing profile match',
    message: 'A likely match to an existing user was detected, but not yet hard-confirmed.',
    action: 'Hold still for confirmation, or choose Continue as New Student if this person is truly unregistered.'
  },
  override_forced_unknown: {
    title: 'Manual override active',
    message: 'Registration is continuing as a new student by operator override.',
    action: 'Continue capture and submit details when sample collection is complete.'
  },
  registration_entry_camera_only: {
    title: 'Entry camera required',
    message: 'Registration capture is restricted to the entry camera worker.',
    action: 'Switch to the entry camera worker route and start a new registration session.'
  }
};

const FLOW_STEPS = ['Start Session', 'Capture', 'Fill Details', 'Submit'];

function MetricPill({ icon, label, value }) {
  return (
    <div className="d-flex align-items-center gap-3 px-3 py-3 rounded-3 bg-light border" style={{ minHeight: '72px' }}>
      <span
        className="d-inline-flex align-items-center justify-content-center rounded-circle"
        style={{ width: '34px', height: '34px', background: 'rgba(13, 110, 253, 0.12)', color: '#0d6efd' }}
      >
        <i className={icon}></i>
      </span>
      <div>
        <div className="text-uppercase text-muted" style={{ fontSize: '11px', letterSpacing: '0.08em' }}>
          {label}
        </div>
        <div className="fw-semibold" style={{ fontSize: '15px', color: '#012970' }}>
          {value}
        </div>
      </div>
    </div>
  );
}

function FlowStepper({ activeStep }) {
  return (
    <div className="d-flex flex-wrap gap-2 mb-4" role="list" aria-label="Registration progress">
      {FLOW_STEPS.map((label, index) => {
        const completed = index < activeStep;
        const active = index === activeStep;
        const itemClass = completed
          ? 'border-success-subtle bg-success-subtle text-success'
          : active
            ? 'border-primary-subtle bg-primary-subtle text-primary'
            : 'border-secondary-subtle bg-light text-muted';
        const iconClass = completed ? 'bi bi-check-circle-fill' : active ? 'bi bi-record-circle-fill' : 'bi bi-circle';
        return (
          <div key={label} role="listitem" className={`d-flex align-items-center gap-2 px-3 py-2 rounded-3 border ${itemClass}`}>
            <i className={iconClass} aria-hidden="true"></i>
            <span className="fw-semibold" style={{ fontSize: '14px' }}>
              {label}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function normalizeSpaces(value) {
  return value.trim().replace(/\s+/g, ' ');
}

function formatCountdown(secondsRemaining) {
  if (typeof secondsRemaining !== 'number' || !Number.isFinite(secondsRemaining)) {
    return '--:--';
  }
  const clamped = Math.max(0, Math.floor(secondsRemaining));
  const minutes = Math.floor(clamped / 60);
  const seconds = clamped % 60;
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

function validateField(field, form, registrationType) {
  const normalizedName = normalizeSpaces(form.name || '');
  const normalizedSrCode = (form.sr_code || '').trim();
  const normalizedProgram = normalizeSpaces(form.program || '');

  if (field === 'name') {
    if (!normalizedName) {
      return 'Name is required.';
    }
    if (!normalizedName.includes(',')) {
      return 'Use the name format: Last Name, First Name.';
    }
    const [lastName, firstName] = normalizedName.split(',', 2).map((part) => part.trim());
    if (!lastName || !firstName) {
      return 'Use the name format: Last Name, First Name.';
    }
    if (!NAME_PATTERN.test(normalizedName)) {
      return 'Name contains invalid characters.';
    }
  }

  if (field === 'sr_code' && registrationType !== REGISTRATION_TYPES.visitor) {
    if (!normalizedSrCode) {
      return 'SR Code is required.';
    }
    if (!SR_CODE_PATTERN.test(normalizedSrCode)) {
      return 'SR Code must use the format 23-12345.';
    }
  }

  if (field === 'gender') {
    if (!form.gender) {
      return 'Gender is required.';
    }
    if (!ALLOWED_GENDERS.has(form.gender)) {
      return 'Please select a valid gender.';
    }
  }

  if (field === 'college' && registrationType !== REGISTRATION_TYPES.visitor) {
    if (!form.college) {
      return 'College is required.';
    }
  }

  if (field === 'program' && registrationType !== REGISTRATION_TYPES.visitor) {
    if (!normalizedProgram) {
      return 'Program is required.';
    }
    if (normalizedProgram.length < 4 || normalizedProgram.length > 120) {
      return 'Program must be between 4 and 120 characters.';
    }
    if (!PROGRAM_PATTERN.test(normalizedProgram)) {
      return 'Program contains invalid characters.';
    }
  }

  return '';
}

function validateRegistrationForm(form, registrationType) {
  const errors = {};
  const normalized = {
    name: normalizeSpaces(form.name || ''),
    sr_code: (form.sr_code || '').trim(),
    gender: form.gender || '',
    college: form.college || '',
    program: normalizeSpaces(form.program || '')
  };

  const fieldsToValidate = ['name', 'gender'];
  if (registrationType !== REGISTRATION_TYPES.visitor) {
    fieldsToValidate.push('sr_code', 'college', 'program');
  }

  fieldsToValidate.forEach((field) => {
    const fieldError = validateField(field, normalized, registrationType);
    if (fieldError) {
      errors[field] = fieldError;
    }
  });

  return { errors, normalized };
}

function deriveUiState({ captureError, info, readyToSubmit, webSessionActive, captureInProgress }) {
  if (captureError) {
    return 'error';
  }
  if (info.session_expired) {
    return 'expired';
  }
  if (readyToSubmit) {
    return 'ready_to_submit';
  }
  if (webSessionActive && !captureInProgress) {
    return 'waiting_lock';
  }
  if (webSessionActive || captureInProgress) {
    return 'capturing';
  }
  return 'idle';
}

function getStatePresentation({ uiState, currentPose, currentPoseCaptured, currentPoseRequired }) {
  if (uiState === 'error') {
    return {
      icon: 'bi bi-exclamation-triangle-fill',
      title: 'Registration action requires attention',
      message: 'Resolve the error below before continuing this registration flow.',
      toneClass: 'border-danger-subtle bg-danger-subtle',
      badgeClass: 'text-bg-danger',
      badgeText: 'Attention Needed'
    };
  }

  if (uiState === 'expired') {
    return {
      icon: 'bi bi-hourglass-bottom',
      title: 'Session expired',
      message: 'Start a new session to continue collecting captures for this student.',
      toneClass: 'border-warning-subtle bg-warning-subtle',
      badgeClass: 'text-bg-warning',
      badgeText: 'Expired'
    };
  }

  if (uiState === 'ready_to_submit') {
    return {
      icon: 'bi bi-check2-circle',
      title: 'Capture complete',
      message: 'Required face samples are complete. Fill in student details and submit registration.',
      toneClass: 'border-success-subtle bg-success-subtle',
      badgeClass: 'text-bg-success',
      badgeText: 'Ready to Submit'
    };
  }

  if (uiState === 'waiting_lock') {
    return {
      icon: 'bi bi-camera-video',
      title: 'Session active - waiting for lock',
      message: 'Keep one unregistered student centered in the live camera flow to begin capture.',
      toneClass: 'border-primary-subtle bg-primary-subtle',
      badgeClass: 'text-bg-primary',
      badgeText: 'Waiting for Lock'
    };
  }

  if (uiState === 'capturing') {
    return {
      icon: 'bi bi-person-video3',
      title: 'Capture in progress',
      message: currentPose
        ? `Current pose: ${currentPose}. Captured ${currentPoseCaptured} of ${currentPoseRequired} samples for this pose.`
        : 'Capture is active. Keep the student steady and well-lit in frame.',
      toneClass: 'border-info-subtle bg-info-subtle',
      badgeClass: 'text-bg-info',
      badgeText: 'Capturing'
    };
  }

  return {
    icon: 'bi bi-play-circle',
    title: 'Waiting for session start',
    message: 'Start a registration session, then keep one unregistered student in frame until capture completes.',
    toneClass: 'border-secondary-subtle bg-light',
    badgeClass: 'text-bg-secondary',
    badgeText: 'Idle'
  };
}

export default function RegisterPage() {
  const [info, setInfo] = React.useState(INITIAL_INFO);
  const [loading, setLoading] = React.useState(true);
  const [captureError, setCaptureError] = React.useState('');
  const [fieldErrors, setFieldErrors] = React.useState({});
  const [touchedFields, setTouchedFields] = React.useState({});
  const [submitting, setSubmitting] = React.useState(false);
  const [sessionAction, setSessionAction] = React.useState('');
  const [result, setResult] = React.useState(null);
  const [programOptionsByCollege, setProgramOptionsByCollege] = React.useState(DEFAULT_COLLEGE_PROGRAM_MAP);
  const [collegeOptions, setCollegeOptions] = React.useState(DEFAULT_COLLEGE_OPTIONS);
  const [registrationType, setRegistrationType] = React.useState(REGISTRATION_TYPES.student);
  const [form, setForm] = React.useState(INITIAL_FORM);
  const isVisitor = registrationType === REGISTRATION_TYPES.visitor;

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
        // Ignore transient polling failures.
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
    document.body.classList.add('register-kiosk-page');
    return () => {
      document.body.classList.remove('register-kiosk-page');
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

  const allProgramOptions = React.useMemo(() => {
    const seen = new Set();
    const flattened = [];
    Object.values(programOptionsByCollege).forEach((programs) => {
      (programs || []).forEach((program) => {
        const normalized = normalizeSpaces(program);
        if (!normalized) {
          return;
        }
        if (!seen.has(normalized)) {
          seen.add(normalized);
          flattened.push(normalized);
        }
      });
    });
    return flattened.sort((a, b) => a.localeCompare(b));
  }, [programOptionsByCollege]);

  const programCollegeLookup = React.useMemo(() => {
    const lookup = {};
    Object.entries(programOptionsByCollege).forEach(([college, programs]) => {
      (programs || []).forEach((program) => {
        const normalizedKey = normalizeSpaces(program).toLowerCase();
        if (normalizedKey) {
          lookup[normalizedKey] = college;
        }
      });
    });
    return lookup;
  }, [programOptionsByCollege]);

  React.useEffect(() => {
    const touchedKeys = Object.keys(touchedFields).filter((key) => touchedFields[key]);
    if (!touchedKeys.length) {
      return;
    }
    setFieldErrors((prev) => {
      const next = { ...prev };
      let changed = false;
      touchedKeys.forEach((key) => {
        const message = validateField(key, form);
        if (message) {
          if (next[key] !== message) {
            next[key] = message;
            changed = true;
          }
          return;
        }
        if (next[key]) {
          delete next[key];
          changed = true;
        }
      });
      return changed ? next : prev;
    });
  }, [form, touchedFields]);

  function updateForm(key, value) {
    setCaptureError('');
    setResult(null);
    setForm((prev) => {
      const next = { ...prev, [key]: value };
      if (key === 'program') {
        const mappedCollege = programCollegeLookup[normalizeSpaces(value).toLowerCase()];
        if (mappedCollege) {
          next.college = mappedCollege;
        }
      }
      return next;
    });

    if (key === 'name' || key === 'sr_code' || key === 'program') {
      setTouchedFields((prev) => (prev[key] ? prev : { ...prev, [key]: true }));
    }
  }

  function handleFieldBlur(key) {
    setTouchedFields((prev) => ({ ...prev, [key]: true }));
  }

  const handleReset = React.useCallback(async () => {
    const confirmed = await confirmAction({
      title: 'Reset Captured Samples?',
      text: 'This will clear the current registration capture set for the student in progress.',
      confirmButtonText: 'Reset Samples',
      confirmButtonColor: '#dc3545'
    });
    if (!confirmed) {
      return;
    }

    setCaptureError('');
    setFieldErrors({});
    setTouchedFields({});
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
      setInfo((prev) => ({ ...prev, ...payload }));
      await showSuccess('Samples Reset', payload.message || 'The current registration samples were cleared successfully.');
    } catch (error) {
      const message = getErrorMessage(error, 'Unable to reset the current capture session.');
      setCaptureError(message);
      await showError('Reset Failed', message);
    }
  }, []);

  const handleStartSession = React.useCallback(async () => {
    setCaptureError('');
    setResult(null);
    setSessionAction('start');

    try {
      const response = await fetch('/api/register-session/start', {
        method: 'POST',
        credentials: 'include'
      });
      const payload = await response.json();
      if (!response.ok || payload.success === false) {
        setCaptureError(payload.message || 'Unable to start registration session.');
        await showError('Session Start Failed', payload.message || 'Unable to start registration session.');
        return;
      }
      setInfo((prev) => ({ ...prev, ...payload }));
      await showSuccess('Session Started', payload.message || 'Registration session started successfully.');
    } catch (error) {
      const message = getErrorMessage(error, 'Unable to start registration session.');
      setCaptureError(message);
      await showError('Session Start Failed', message);
    } finally {
      setSessionAction('');
    }
  }, []);

  const handleCancelSession = React.useCallback(async () => {
    setCaptureError('');
    setResult(null);
    setSessionAction('cancel');

    try {
      const response = await fetch('/api/register-session/cancel', {
        method: 'POST',
        credentials: 'include'
      });
      const payload = await response.json();
      if (!response.ok || payload.success === false) {
        setCaptureError(payload.message || 'Unable to cancel registration session.');
        await showError('Cancel Failed', payload.message || 'Unable to cancel registration session.');
        return;
      }
      setInfo((prev) => ({ ...prev, ...payload }));
      await showSuccess('Session Canceled', payload.message || 'Registration session canceled successfully.');
    } catch (error) {
      const message = getErrorMessage(error, 'Unable to cancel registration session.');
      setCaptureError(message);
      await showError('Cancel Failed', message);
    } finally {
      setSessionAction('');
    }
  }, []);

  const handleContinueUnknown = React.useCallback(async () => {
    setCaptureError('');
    setResult(null);
    setSessionAction('override');
    try {
      const response = await fetch('/api/register-session/continue-unknown', {
        method: 'POST',
        credentials: 'include'
      });
      const payload = await response.json();
      if (!response.ok || payload.success === false) {
        setCaptureError(payload.message || 'Unable to continue as unknown student.');
        await showError('Override Failed', payload.message || 'Unable to continue as unknown student.');
        return;
      }
      setInfo((prev) => ({ ...prev, ...payload }));
      await showSuccess('Override Enabled', payload.message || 'Capture will continue as a new student.');
    } catch (error) {
      const message = getErrorMessage(error, 'Unable to continue as unknown student.');
      setCaptureError(message);
      await showError('Override Failed', message);
    } finally {
      setSessionAction('');
    }
  }, []);

  async function handleSubmit(ev) {
    ev.preventDefault();
    setCaptureError('');
    setResult(null);

    if (!info.ready_to_submit || !info.has_pending_registration) {
      setCaptureError('Registration capture is not complete yet. Finish all required face samples before saving.');
      return;
    }

    const { errors, normalized } = validateRegistrationForm(form, registrationType);
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      setTouchedFields({
        name: true,
        sr_code: true,
        gender: true,
        college: true,
        program: true
      });
      setCaptureError(Object.values(errors)[0]);
      return;
    }

    const summary = [
      `${registrationType === REGISTRATION_TYPES.visitor ? 'Visitor' : 'Student'}: ${normalized.name}`,
      `Gender: ${normalized.gender}`,
      `Retained Samples: ${info.total_progress?.retained ?? info.sample_previews?.length ?? 0} / ${info.total_progress?.retained_required ?? 0}`,
      `Capture Ready: ${info.ready_to_submit ? 'Yes' : 'No'}`
    ];
    if (registrationType !== REGISTRATION_TYPES.visitor) {
      summary.splice(1, 0, `SR Code: ${normalized.sr_code}`, `College: ${normalized.college}`, `Program: ${normalized.program}`);
    }

    const reviewConfirmed = await confirmAction({
      icon: 'info',
      title: 'Review Registration Before Submit',
      text: summary.join('\n'),
      confirmButtonText: 'Submit Registration',
      confirmButtonColor: '#0d6efd'
    });
    if (!reviewConfirmed) {
      return;
    }

    setSubmitting(true);

    const formData = new FormData();
    formData.append('user_type', registrationType);
    formData.append('name', normalized.name);
    if (registrationType !== REGISTRATION_TYPES.visitor) {
      formData.append('sr_code', normalized.sr_code);
      formData.append('program', normalized.program);
      formData.append('college', normalized.college);
    }
    formData.append('gender', normalized.gender);

    try {
      const response = await fetch('/register', {
        method: 'POST',
        credentials: 'include',
        body: formData,
        headers: {
          Accept: 'application/json'
        }
      });

      let payload;
      try {
        payload = await response.json();
      } catch (jsonError) {
        const responseText = await response.text();
        console.error('Unexpected non-JSON /register response:', response.status, responseText);
        const message = response.status >= 500 ? 'Server error while saving registration.' : 'Unexpected server response while saving registration.';
        setCaptureError(message);
        await showError('Registration Failed', message);
        return;
      }

      if (!response.ok || !payload.success) {
        if (payload.field) {
          setFieldErrors((prev) => ({ ...prev, [payload.field]: payload.message || 'Please review this field.' }));
          setTouchedFields((prev) => ({ ...prev, [payload.field]: true }));
        } else if ((payload.message || '').toLowerCase().includes('sr code')) {
          setFieldErrors((prev) => ({ ...prev, sr_code: payload.message || 'This SR Code is already registered.' }));
          setTouchedFields((prev) => ({ ...prev, sr_code: true }));
        }
        setCaptureError(payload.message || 'Unable to save the registration.');
        return;
      }

      setResult(payload);
      setInfo((prev) => ({
        ...prev,
        ...payload,
        capture_count: 0,
        has_pending_registration: false,
        is_in_progress: false,
        ready_to_submit: false,
        web_session_active: false,
        session_expired: false,
        sample_previews: [],
        total_progress: {
          ...(prev.total_progress || {}),
          captured: 0,
          retained: 0
        }
      }));
      setForm(INITIAL_FORM);
      setFieldErrors({});
      setTouchedFields({});
      await showSuccess('Registration Complete', payload.message || 'Student registration saved successfully.');
    } catch (error) {
      const message = getErrorMessage(error, 'Unable to reach the server. Please try again.');
      setCaptureError(message);
      await showError('Registration Failed', message);
    } finally {
      setSubmitting(false);
    }
  }

  const readyToSubmit = Boolean(info.ready_to_submit && info.has_pending_registration);
  const webSessionActive = Boolean(info.web_session_active);
  const captureInProgress = !readyToSubmit && Boolean(info.capture_count > 0 || info.is_in_progress);
  const sessionControlBusy = sessionAction === 'start' || sessionAction === 'cancel' || sessionAction === 'override';
  const canStartSession = !readyToSubmit && !webSessionActive && !captureInProgress && !submitting && !sessionControlBusy;
  const canCancelSession = (webSessionActive || captureInProgress) && !submitting && !sessionControlBusy;
  const sampleCount = info.sample_previews?.length || 0;
  const visibleSamplePreviews = React.useMemo(() => (info.sample_previews || []).slice(0, 8), [info.sample_previews]);
  const firstPreview = visibleSamplePreviews[0] || null;

  const currentPose = info.current_pose || null;
  const currentPoseProgress = currentPose ? info.pose_progress?.[currentPose] : null;
  const currentPoseCaptured = currentPoseProgress?.captured ?? 0;
  const currentPoseRequired = currentPoseProgress?.required ?? 0;
  const completedPoses = Array.isArray(info.required_poses)
    ? info.required_poses.filter((pose) => info.pose_progress?.[pose]?.completed).length
    : 0;
  const totalPoses = Array.isArray(info.required_poses) ? info.required_poses.length : 0;

  const progressPercent = info.max_captures
    ? Math.min(100, Math.round((info.capture_count / info.max_captures) * 100))
    : 0;
  const uiState = deriveUiState({
    captureError,
    info,
    readyToSubmit,
    webSessionActive,
    captureInProgress
  });
  const statePresentation = getStatePresentation({
    uiState,
    currentPose,
    currentPoseCaptured,
    currentPoseRequired
  });

  const statusReasonCode = (info.status_reason_code || '').trim();
  const statusReasonMessage = (info.status_reason_message || '').trim();
  const reasonView = STATUS_REASON_VIEWS[statusReasonCode] || null;
  const reasonTitle = reasonView?.title || (statusReasonMessage ? 'Capture status' : '');
  const reasonMessage = reasonView?.message || statusReasonMessage;
  const reasonAction = reasonView?.action || '';
  const canContinueUnknown = statusReasonCode === 'possible_existing_match'
    && !info.allow_unknown_override
    && !submitting
    && !sessionControlBusy
    && (webSessionActive || captureInProgress);

  const cameraState = (info.camera_stream?.state || 'unknown').toLowerCase();
  const cameraMessage = info.camera_stream?.message || '';
  const frameAgeSeconds = typeof info.camera_stream?.last_frame_age_seconds === 'number'
    ? Math.round(info.camera_stream.last_frame_age_seconds)
    : null;
  const streamStale = frameAgeSeconds != null && frameAgeSeconds > 4;

  let healthVariant = 'text-bg-success';
  let healthLabel = 'Detection Live';
  let healthIcon = 'bi bi-broadcast';
  if (info.detection_paused) {
    healthVariant = 'text-bg-warning';
    healthLabel = 'Detection Paused';
    healthIcon = 'bi bi-pause-circle';
  } else if (cameraState === 'live' || cameraState === 'connected') {
    healthVariant = streamStale ? 'text-bg-warning' : 'text-bg-success';
    healthLabel = streamStale ? 'Stream Stale' : 'Detection Live';
    healthIcon = streamStale ? 'bi bi-exclamation-circle' : 'bi bi-camera-video';
  } else if (cameraState === 'connecting' || cameraState === 'reconnecting') {
    healthVariant = 'text-bg-warning';
    healthLabel = 'Reconnecting';
    healthIcon = 'bi bi-arrow-repeat';
  } else {
    healthVariant = 'text-bg-danger';
    healthLabel = 'Stream Offline';
    healthIcon = 'bi bi-slash-circle';
  }

  const secondsUntilExpiry = typeof info.seconds_until_expiry === 'number' ? info.seconds_until_expiry : null;
  const expiringSoon = secondsUntilExpiry != null
    && secondsUntilExpiry > 0
    && secondsUntilExpiry <= EXPIRING_SOON_THRESHOLD_SECONDS;

  const formLocked = !readyToSubmit;
  const canResetSamples = !submitting && !sessionControlBusy && (sampleCount > 0 || captureInProgress || readyToSubmit);
  const resetHelperText = 'Use reset only when the wrong student was captured or the sample set is incomplete.';

  const activeStep = result?.profile
    ? 3
    : readyToSubmit
      ? 2
      : (webSessionActive || captureInProgress)
        ? 1
        : 0;

  const primaryAction = readyToSubmit ? 'submit' : (webSessionActive || captureInProgress ? 'cancel' : 'start');
  const primaryActionBusy = (primaryAction === 'start' && sessionAction === 'start')
    || (primaryAction === 'cancel' && sessionAction === 'cancel')
    || (primaryAction === 'submit' && submitting);
  const primaryActionDisabled = primaryAction === 'start'
    ? !canStartSession
    : primaryAction === 'cancel'
      ? !canCancelSession
      : submitting || sessionControlBusy || !readyToSubmit;

  React.useEffect(() => {
    function onKeyDown(ev) {
      if (ev.key === 'Escape' && canCancelSession) {
        ev.preventDefault();
        void handleCancelSession();
      }
    }
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [canCancelSession, handleCancelSession]);

  function handleFormKeyDown(ev) {
    if (ev.key === 'Enter' && !readyToSubmit) {
      ev.preventDefault();
    }
  }

  if (loading) {
    return (
      <div className="d-flex justify-content-center align-items-center" style={{ minHeight: '30vh' }}>
        <div className="spinner-border text-primary" role="status"></div>
      </div>
    );
  }

  return (
    <main className="auth-page register-kiosk-shell animate__animated animate__fadeIn animate__fast">
      <div className="container-fluid register-kiosk-frame">
        <section className="section register register-kiosk-section min-vh-100 d-flex flex-column justify-content-center py-3">
          <div className="container-fluid register-kiosk-container animate__animated animate__fadeInUp animate__fast">
            <div className="d-flex justify-content-center mb-3 register-kiosk-brand">
              <a href="/login">
                <img
                  src="/static/assets/img/bsu-new-logo.png"
                  alt="BatStateU Logo"
                  style={{ width: '18rem', height: 'auto' }}
                />
              </a>
            </div>

            <div className="row justify-content-center">
              <div className="col-12 col-xxl-11">
                <div className="card register-kiosk-card">
                  <div className="card-body p-3 p-xl-4 register-kiosk-card-body">
                    <div className="d-flex flex-wrap justify-content-between align-items-start gap-3 mb-4">
                      <div>
                        <div className="text-uppercase text-muted fw-semibold" style={{ fontSize: '11px', letterSpacing: '0.08em' }}>
                          First-Time Registration Only
                        </div>
                        <h5 className="card-title mb-1">Complete first-time registration</h5>
                        <p className="text-muted mb-0" style={{ fontSize: '14px' }}>
                          Use this page for students or visitors who are not yet registered. Complete capture first, then submit details.
                        </p>
                      </div>
                      <span className={`badge rounded-pill ${statePresentation.badgeClass}`} style={{ fontSize: '13px' }}>
                        {statePresentation.badgeText}
                      </span>
                    </div>

                    <FlowStepper activeStep={activeStep} />

                    <div className={`rounded-3 border p-3 p-md-4 mb-4 ${statePresentation.toneClass}`}>
                      <div className="d-flex flex-wrap justify-content-between align-items-start gap-3">
                        <div className="d-flex align-items-start gap-3">
                          <span
                            className="d-inline-flex align-items-center justify-content-center rounded-circle bg-white border"
                            style={{ width: '40px', height: '40px', color: '#012970' }}
                          >
                            <i className={statePresentation.icon}></i>
                          </span>
                          <div>
                            <div className="fw-semibold mb-1" style={{ color: '#012970', fontSize: '16px' }}>
                              {statePresentation.title}
                            </div>
                            <div className="text-muted" style={{ fontSize: '14px', lineHeight: 1.6 }}>
                              {statePresentation.message}
                            </div>
                          </div>
                        </div>

                        <div className="d-flex flex-column gap-2 align-items-start align-items-md-end">
                          <span className={`badge ${healthVariant}`} style={{ fontSize: '12px' }}>
                            <i className={`${healthIcon} me-1`} aria-hidden="true"></i>
                            {healthLabel}
                          </span>
                          {secondsUntilExpiry != null ? (
                            <span className={`badge ${expiringSoon ? 'text-bg-warning' : 'text-bg-secondary'}`} style={{ fontSize: '12px' }}>
                              <i className="bi bi-clock me-1" aria-hidden="true"></i>
                              Session expires in {formatCountdown(secondsUntilExpiry)}
                            </span>
                          ) : null}
                        </div>
                      </div>

                      <div className="small text-muted mt-3" style={{ fontSize: '14px' }}>
                        {cameraMessage || 'Camera status is being monitored.'}
                        {frameAgeSeconds != null ? ` Last frame ${frameAgeSeconds}s ago.` : ''}
                      </div>

                      {reasonMessage ? (
                        <div className="rounded-3 border bg-white p-3 mt-3">
                          <div className="fw-semibold mb-1" style={{ color: '#012970', fontSize: '15px' }}>
                            <i className="bi bi-info-circle me-2" aria-hidden="true"></i>
                            {reasonTitle}
                          </div>
                          <div className="text-muted" style={{ fontSize: '14px', lineHeight: 1.6 }}>
                            {reasonMessage}
                          </div>
                          {reasonAction ? (
                            <div className="mt-2" style={{ fontSize: '14px' }}>
                              <span className="fw-semibold">Next action: </span>
                              {reasonAction}
                            </div>
                          ) : null}
                          {canContinueUnknown ? (
                            <div className="mt-3">
                              <button
                                className="btn btn-outline-primary btn-sm"
                                type="button"
                                onClick={handleContinueUnknown}
                                disabled={sessionAction === 'override'}
                              >
                                {sessionAction === 'override' ? (
                                  <>
                                    <span className="spinner-border spinner-border-sm me-2" role="status" />
                                    Applying Override...
                                  </>
                                ) : (
                                  <>
                                    <i className="bi bi-person-plus me-2"></i>
                                    Continue as New Student
                                  </>
                                )}
                              </button>
                            </div>
                          ) : null}
                        </div>
                      ) : null}

                      {expiringSoon ? (
                        <div className="rounded-3 border border-warning-subtle bg-warning-subtle px-3 py-2 mt-3">
                          <div className="fw-semibold text-warning-emphasis" style={{ fontSize: '14px' }}>
                            <i className="bi bi-exclamation-triangle me-2" aria-hidden="true"></i>
                            Session expires soon
                          </div>
                          <div className="text-muted" style={{ fontSize: '14px' }}>
                            Keep the student in frame or submit immediately if capture is complete.
                          </div>
                        </div>
                      ) : null}

                      {captureError ? (
                        <div className="rounded-3 border border-danger-subtle bg-danger-subtle px-3 py-2 mt-3">
                          <div className="fw-semibold text-danger" style={{ fontSize: '14px' }}>
                            <i className="bi bi-exclamation-octagon me-2" aria-hidden="true"></i>
                            Registration error
                          </div>
                          <div style={{ fontSize: '14px' }}>{captureError}</div>
                        </div>
                      ) : null}

                      {result?.profile ? (
                        <div className="rounded-3 border border-success-subtle bg-success-subtle px-3 py-2 mt-3">
                          <div className="fw-semibold text-success" style={{ fontSize: '14px' }}>
                            <i className="bi bi-check-circle me-2" aria-hidden="true"></i>
                            {result.message}
                          </div>
                          <div style={{ fontSize: '14px' }}>
                            Saved as {result.profile.name} ({result.profile.sr_code}) - {result.profile.gender}, {result.profile.program}.
                          </div>
                        </div>
                      ) : null}

                      <div className="row g-3 mt-2">
                        <div className="col-md-4">
                          <MetricPill icon="bi bi-camera" label="Captured" value={`${info.capture_count}/${info.max_captures}`} />
                        </div>
                        <div className="col-md-4">
                          <MetricPill icon="bi bi-arrows-angle-expand" label="Completed Poses" value={`${completedPoses}/${totalPoses || 0}`} />
                        </div>
                        <div className="col-md-4">
                          <MetricPill
                            icon="bi bi-person-check"
                            label="Submission"
                            value={readyToSubmit ? 'Ready to save' : 'Locked until capture is complete'}
                          />
                        </div>
                      </div>

                      <div className="mt-3">
                        <div className="d-flex justify-content-between align-items-center text-muted mb-1" style={{ fontSize: '14px' }}>
                          <span>Capture progress</span>
                          <span>{progressPercent}%</span>
                        </div>
                        <div className="progress" style={{ height: '8px' }}>
                          <div className="progress-bar" style={{ width: `${progressPercent}%`, transition: 'width 0.35s ease' }} />
                        </div>
                      </div>

                      {firstPreview ? (
                        <div className="d-flex align-items-start gap-3 mt-3 p-2 rounded-3 border bg-white">
                          <img
                            src={firstPreview.image_url}
                            alt="Primary captured preview"
                            className="rounded-3"
                            style={{ width: '88px', height: '88px', objectFit: 'cover' }}
                          />
                          <div>
                            <div className="fw-semibold" style={{ fontSize: '14px', color: '#012970' }}>
                              Quick identity preview
                            </div>
                            <div className="text-muted" style={{ fontSize: '14px', lineHeight: 1.5 }}>
                              Verify this is the correct student before completing registration.
                            </div>
                            <div className="small text-muted" style={{ fontSize: '13px' }}>
                              Quality score: {firstPreview.quality_score}
                            </div>
                          </div>
                        </div>
                      ) : null}
                    </div>

                    <div className="rounded-3 border bg-light p-3 p-md-4 mb-4 register-kiosk-previews-card">
                      <div className="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-3">
                        <div className="fw-semibold" style={{ color: '#012970', fontSize: '16px' }}>
                          Captured face previews
                        </div>
                        <div className="text-muted" style={{ fontSize: '14px' }}>
                          {sampleCount} preview{sampleCount === 1 ? '' : 's'} available
                          {sampleCount > visibleSamplePreviews.length ? ` - showing ${visibleSamplePreviews.length}` : ''}
                        </div>
                      </div>

                      {sampleCount ? (
                        <div className="d-flex gap-2 flex-wrap register-kiosk-preview-grid">
                          {visibleSamplePreviews.map((sample, index) => (
                            <div
                              key={sample.id ?? index}
                              className="text-center register-kiosk-preview-tile"
                              style={{ padding: '6px', border: '1px solid #e6e7eb', background: '#fff' }}
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
                        <div
                          className="rounded-3 d-flex align-items-center justify-content-center text-center px-4 border border-secondary-subtle bg-white text-muted register-kiosk-empty-previews"
                          style={{ minHeight: '112px', borderStyle: 'dashed', fontSize: '14px' }}
                        >
                          No preview tiles yet. Stay on the CCTV capture flow until the sample set is completed.
                        </div>
                      )}
                    </div>

                    <div className="register-kiosk-form-pane">
                      <form className="row g-3" onSubmit={handleSubmit} onKeyDown={handleFormKeyDown}>
                        <div className="col-12">
                          <div className="rounded-3 border bg-light p-3 register-kiosk-saving-card">
                            <div className="fw-semibold mb-2" style={{ color: '#012970', fontSize: '15px' }}>
                              Before saving
                            </div>
                            <ul className="mb-0 ps-3 text-muted" style={{ fontSize: '14px', lineHeight: 1.7 }}>
                              <li>Use the format Last Name, First Name.</li>
                              <li>{isVisitor ? 'Visitor registration does not require SR Code, college, or program.' : 'SR Code must follow the format 23-12345.'}</li>
                              {!isVisitor ? <li>Program search is available without selecting college first.</li> : null}
                              <li>Reset samples only if the wrong person was captured or the set is incomplete.</li>
                            </ul>
                          </div>
                        </div>

                        <div className="col-12">
                          <div className="btn-group" role="group" aria-label="Registration type selector">
                            <button
                              type="button"
                              className={`btn ${registrationType === REGISTRATION_TYPES.student ? 'btn-primary' : 'btn-outline-secondary'}`}
                              onClick={() => setRegistrationType(REGISTRATION_TYPES.student)}
                            >
                              Student
                            </button>
                            <button
                              type="button"
                              className={`btn ${registrationType === REGISTRATION_TYPES.visitor ? 'btn-primary' : 'btn-outline-secondary'}`}
                              onClick={() => {
                                setRegistrationType(REGISTRATION_TYPES.visitor);
                                setForm((prev) => ({ ...prev, sr_code: '', college: '', program: '' }));
                              }}
                            >
                              Visitor
                            </button>
                          </div>
                          <div className="text-muted mt-2" style={{ fontSize: '14px' }}>
                            Select the registration type before completing the details form.
                          </div>
                        </div>

                        {formLocked ? (
                          <div className="col-12">
                            <div className="alert alert-secondary d-flex align-items-start gap-2 mb-0">
                              <i className="bi bi-lock-fill mt-1"></i>
                              <div style={{ fontSize: '14px' }}>
                                Complete face capture first to unlock form.
                              </div>
                            </div>
                          </div>
                        ) : null}

                        <div className="col-12">
                          <label htmlFor="name" className="form-label" style={{ fontSize: '14px' }}>
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
                            onBlur={() => handleFieldBlur('name')}
                            disabled={formLocked}
                            required
                          />
                          {fieldErrors.name ? <div className="invalid-feedback">{fieldErrors.name}</div> : null}
                          <div className="form-text" style={{ fontSize: '14px' }}>
                            Enter the official {registrationType === REGISTRATION_TYPES.visitor ? 'visitor' : 'student'} name using Last Name, First Name.
                          </div>
                        </div>

                        {!isVisitor ? (
                          <div className="col-md-6">
                            <label htmlFor="sr_code" className="form-label" style={{ fontSize: '14px' }}>
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
                              onBlur={() => handleFieldBlur('sr_code')}
                              disabled={formLocked}
                              required={!isVisitor}
                            />
                            {fieldErrors.sr_code ? <div className="invalid-feedback">{fieldErrors.sr_code}</div> : null}
                            <div className="form-text" style={{ fontSize: '14px' }}>
                              Duplicate SR Codes are blocked to prevent registration conflicts.
                            </div>
                          </div>
                        ) : null}

                        <div className="col-md-6">
                          <label htmlFor="gender" className="form-label" style={{ fontSize: '14px' }}>
                            Gender
                          </label>
                          <select
                            id="gender"
                            name="gender"
                            className={`form-select ${fieldErrors.gender ? 'is-invalid' : ''}`}
                            value={form.gender}
                            onChange={(ev) => updateForm('gender', ev.target.value)}
                            onBlur={() => handleFieldBlur('gender')}
                            disabled={formLocked}
                            required
                          >
                            <option value="">Select gender</option>
                            <option value="Male">Male</option>
                            <option value="Female">Female</option>
                            <option value="Other">Other</option>
                          </select>
                          {fieldErrors.gender ? <div className="invalid-feedback">{fieldErrors.gender}</div> : null}
                        </div>

                        {!isVisitor ? (
                          <>
                            <div className="col-md-6">
                              <label htmlFor="college" className="form-label" style={{ fontSize: '14px' }}>
                                College
                              </label>
                              <select
                                id="college"
                                name="college"
                                className={`form-select ${fieldErrors.college ? 'is-invalid' : ''}`}
                                value={form.college}
                                onChange={(ev) => updateForm('college', ev.target.value)}
                                onBlur={() => handleFieldBlur('college')}
                                disabled={formLocked}
                                required={!isVisitor}
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
                              <label htmlFor="program" className="form-label" style={{ fontSize: '14px' }}>
                                Program
                              </label>
                              <input
                                type="text"
                                id="program"
                                name="program"
                                className={`form-control ${fieldErrors.program ? 'is-invalid' : ''}`}
                                list="program-options"
                                placeholder="Search or type a program"
                                value={form.program}
                                onChange={(ev) => updateForm('program', ev.target.value)}
                                onBlur={() => handleFieldBlur('program')}
                                disabled={formLocked}
                                required={!isVisitor}
                              />
                              {fieldErrors.program ? <div className="invalid-feedback">{fieldErrors.program}</div> : null}
                              <datalist id="program-options">
                                {allProgramOptions.map((program) => (
                                  <option key={program} value={program} />
                                ))}
                              </datalist>
                              <div className="form-text" style={{ fontSize: '14px' }}>
                                Program is searchable across all colleges. Known programs auto-fill the matching college.
                              </div>
                            </div>
                          </>
                        ) : null}

                        <div className="col-12">
                          <div className="alert alert-info d-flex align-items-start gap-3 mb-0">
                            <i className="bi bi-info-circle-fill mt-1"></i>
                            <div style={{ fontSize: '14px' }}>
                              The live recognition camera continues running while this page is open. Only captured samples
                              for the current unregistered student are used for this registration record.
                            </div>
                          </div>
                        </div>

                        <div className="col-12 pt-1">
                          <div className="d-flex flex-wrap gap-2 align-items-center">
                            {primaryAction === 'start' ? (
                              <button className="btn btn-primary px-4" type="button" onClick={handleStartSession} disabled={primaryActionDisabled}>
                                {primaryActionBusy ? (
                                  <>
                                    <span className="spinner-border spinner-border-sm me-2" role="status" />
                                    Starting Session...
                                  </>
                                ) : (
                                  <>
                                    <i className="bi bi-play-circle me-2"></i>
                                    Start Session
                                  </>
                                )}
                              </button>
                            ) : null}

                            {primaryAction === 'cancel' ? (
                              <button className="btn btn-primary px-4" type="button" onClick={handleCancelSession} disabled={primaryActionDisabled}>
                                {primaryActionBusy ? (
                                  <>
                                    <span className="spinner-border spinner-border-sm me-2" role="status" />
                                    Canceling Session...
                                  </>
                                ) : (
                                  <>
                                    <i className="bi bi-x-circle me-2"></i>
                                    Cancel Session
                                  </>
                                )}
                              </button>
                            ) : null}

                            {primaryAction === 'submit' ? (
                              <button className="btn btn-primary px-4" type="submit" disabled={primaryActionDisabled}>
                                {primaryActionBusy ? (
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
                            ) : null}

                            <button className="btn btn-outline-secondary" type="button" onClick={handleReset} disabled={!canResetSamples}>
                              <i className="bi bi-arrow-counterclockwise me-2"></i>
                              Reset Samples
                            </button>
                          </div>
                          <div className="text-muted mt-2" style={{ fontSize: '14px' }}>
                            {resetHelperText}
                          </div>
                        </div>
                      </form>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <footer className="text-center mt-4 register-kiosk-footer">
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
