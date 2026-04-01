import React from 'react';

const INITIAL_INFO = {
  capture_count: 0,
  max_captures: 3,
  has_pending_registration: false,
  is_in_progress: false
};

export default function RegisterPage() {
  const videoRef = React.useRef(null);
  const canvasRef = React.useRef(null);
  const streamRef = React.useRef(null);

  const [info, setInfo] = React.useState(INITIAL_INFO);
  const [loading, setLoading] = React.useState(true);
  const [cameraReady, setCameraReady] = React.useState(false);
  const [cameraError, setCameraError] = React.useState('');
  const [captureError, setCaptureError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);
  const [capturing, setCapturing] = React.useState(false);
  const [result, setResult] = React.useState(null);
  const [captures, setCaptures] = React.useState([]);
  const [form, setForm] = React.useState({ name: '', sr_code: '', course: '' });

  React.useEffect(() => {
    fetch('/api/register-info', { credentials: 'include' })
      .then((res) => res.json())
      .then((resp) => setInfo((prev) => ({ ...prev, ...resp })))
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, []);

  React.useEffect(() => {
    let cancelled = false;

    async function startCamera() {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: {
            facingMode: 'user',
            width: { ideal: 960 },
            height: { ideal: 720 }
          },
          audio: false
        });

        if (cancelled) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }

        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
        }
        setCameraReady(true);
        setCameraError('');
      } catch {
        setCameraError('Camera access was denied or unavailable. Please allow camera access and reload the page.');
      }
    }

    startCamera();

    return () => {
      cancelled = true;
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
      }
    };
  }, []);

  React.useEffect(() => () => {
    captures.forEach((capture) => URL.revokeObjectURL(capture.url));
  }, [captures]);

  function updateForm(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function captureFrameBlob() {
    return new Promise((resolve) => {
      const video = videoRef.current;
      const canvas = canvasRef.current;
      if (!video || !canvas) {
        resolve(null);
        return;
      }

      canvas.width = video.videoWidth || 960;
      canvas.height = video.videoHeight || 720;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((blob) => resolve(blob), 'image/jpeg', 0.92);
    });
  }

  async function handleCapture() {
    setCapturing(true);
    setCaptureError('');
    setResult(null);

    try {
      const blob = await captureFrameBlob();
      if (!blob) {
        setCaptureError('Unable to capture a frame from the camera.');
        return;
      }

      const formData = new FormData();
      formData.append('frame', blob, `capture-${Date.now()}.jpg`);

      const response = await fetch('/api/register-capture', {
        method: 'POST',
        body: formData
      });
      const payload = await response.json();

      if (!response.ok || !payload.success) {
        setCaptureError(payload.message || 'Capture failed. Please try again.');
        return;
      }

      setInfo((prev) => ({
        ...prev,
        capture_count: payload.capture_count,
        max_captures: payload.max_captures,
        has_pending_registration: payload.ready_to_submit,
        is_in_progress: payload.ready_to_submit
      }));
      setCaptures((prev) => [
        ...prev,
        {
          id: `${Date.now()}-${prev.length}`,
          url: URL.createObjectURL(blob),
          quality: payload.quality_status
        }
      ]);
    } catch {
      setCaptureError('Unable to reach the server while processing the capture.');
    } finally {
      setCapturing(false);
    }
  }

  async function handleReset() {
    setCaptureError('');
    setResult(null);

    try {
      const response = await fetch('/api/register-reset', { method: 'POST' });
      const payload = await response.json();
      if (!response.ok || payload.success === false) {
        setCaptureError(payload.message || 'Unable to reset the capture session.');
        return;
      }
      captures.forEach((capture) => URL.revokeObjectURL(capture.url));
      setCaptures([]);
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
    setSubmitting(true);
    setCaptureError('');
    setResult(null);

    const formData = new FormData();
    formData.append('name', form.name);
    formData.append('sr_code', form.sr_code);
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
              <div className="col-xl-10 col-lg-11">
                <div className="card mb-3 shadow-sm border-0">
                  <div className="row g-0">
                    <div className="col-lg-6 border-end">
                      <div className="card-body p-4 p-xl-5">
                        <div className="d-flex justify-content-between align-items-start mb-3">
                          <div>
                            <span className="badge bg-primary-subtle text-primary mb-2">Live Capture</span>
                            <h5 className="card-title fs-4 mb-1">Capture face samples</h5>
                            <p className="text-muted small mb-0">
                              Capture {info.max_captures} clear samples directly from your camera.
                            </p>
                          </div>
                          <span className="badge bg-light text-dark">{info.capture_count}/{info.max_captures}</span>
                        </div>

                        <div className="progress mb-3" style={{ height: '8px' }}>
                          <div className="progress-bar" style={{ width: `${progressPercent}%` }}></div>
                        </div>

                        <div className="ratio ratio-4x3 rounded overflow-hidden bg-dark mb-3">
                          {cameraError ? (
                            <div className="d-flex align-items-center justify-content-center text-center text-white p-4">
                              <div>
                                <i className="bi bi-camera-video-off fs-2 d-block mb-2"></i>
                                <div>{cameraError}</div>
                              </div>
                            </div>
                          ) : (
                            <video
                              ref={videoRef}
                              autoPlay
                              playsInline
                              muted
                              className="w-100 h-100"
                              style={{ objectFit: 'cover', transform: 'scaleX(-1)' }}
                            />
                          )}
                        </div>

                        <div className="d-flex gap-2 flex-wrap mb-3">
                          <button
                            className="btn btn-primary"
                            type="button"
                            onClick={handleCapture}
                            disabled={!cameraReady || capturing || readyToSubmit}
                          >
                            {capturing ? 'Capturing...' : 'Capture Sample'}
                          </button>
                          <button className="btn btn-outline-secondary" type="button" onClick={handleReset}>
                            Reset Samples
                          </button>
                        </div>

                        <div className="small text-muted mb-2">Recent captures</div>
                        <div className="d-flex gap-2 flex-wrap">
                          {captures.length ? (
                            captures.map((capture, index) => (
                              <div key={capture.id} className="border rounded p-1 bg-white" style={{ width: '82px' }}>
                                <img
                                  src={capture.url}
                                  alt={`Capture ${index + 1}`}
                                  className="rounded w-100"
                                  style={{ aspectRatio: '1 / 1', objectFit: 'cover' }}
                                />
                                <div className="small text-center text-muted mt-1">{capture.quality}</div>
                              </div>
                            ))
                          ) : (
                            <div className="small text-muted">No samples captured yet.</div>
                          )}
                        </div>
                      </div>
                    </div>

                    <div className="col-lg-6">
                      <div className="card-body p-4 p-xl-5">
                        <div className="pt-2 pb-3">
                          <h5 className="card-title pb-0 fs-4 mb-1">Student Information</h5>
                          <p className="text-muted small mb-0">
                            Complete the form after enough samples have been captured.
                          </p>
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
                              Saved as {result.profile.name} ({result.profile.sr_code}) - {result.profile.course}.
                            </div>
                            <div className="small">Redirecting to the website record view...</div>
                          </div>
                        ) : null}

                        {!readyToSubmit ? (
                          <div className="alert alert-info mb-3" role="alert">
                            <i className="bi bi-info-circle me-2"></i>
                            Capture {Math.max(info.max_captures - info.capture_count, 0)} more sample(s) to unlock registration.
                          </div>
                        ) : (
                          <div className="alert alert-primary mb-3" role="alert">
                            <i className="bi bi-check-circle me-2"></i>
                            Required samples captured. You can now save this profile to the database.
                          </div>
                        )}

                        <form className="row g-3" onSubmit={handleSubmit}>
                          <div className="col-12">
                            <label htmlFor="name" className="form-label">Full Name</label>
                            <input
                              type="text"
                              id="name"
                              name="name"
                              className="form-control"
                              value={form.name}
                              onChange={(ev) => updateForm('name', ev.target.value)}
                              required
                            />
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
                            <label htmlFor="course" className="form-label">Course</label>
                            <input
                              type="text"
                              id="course"
                              name="course"
                              className="form-control"
                              value={form.course}
                              onChange={(ev) => updateForm('course', ev.target.value)}
                              required
                            />
                          </div>
                          <div className="col-12">
                            <div className="border rounded p-3 bg-light small text-muted">
                              This page captures face samples, extracts embeddings on the server, and saves the finished record into the same database used by Registered Profiles.
                            </div>
                          </div>
                          <div className="col-12 pt-2 d-grid gap-2 d-sm-flex">
                            <button className="btn btn-primary px-4" type="submit" disabled={submitting || !readyToSubmit}>
                              {submitting ? 'Saving Registration...' : 'Complete Registration'}
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
            </div>
          </div>
          <canvas ref={canvasRef} className="d-none"></canvas>
        </section>
      </div>
    </main>
  );
}
