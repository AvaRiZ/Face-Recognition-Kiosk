import React from 'react';

export default function RegisterPage() {
  const [captureCount, setCaptureCount] = React.useState(0);
  const [loading, setLoading] = React.useState(true);
  const [form, setForm] = React.useState({ name: '', sr_code: '', course: '' });

  React.useEffect(() => {
    fetch('/api/register-info', { credentials: 'include' })
      .then((res) => res.json())
      .then((resp) => {
        setCaptureCount(resp.capture_count || 0);
      })
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, []);

  function updateForm(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function handleSubmit(ev) {
    ev.preventDefault();
    const formData = new FormData();
    formData.append('name', form.name);
    formData.append('sr_code', form.sr_code);
    formData.append('course', form.course);

    fetch('/register', {
      method: 'POST',
      credentials: 'include',
      body: formData
    })
      .then((res) => res.json())
      .then((resp) => {
        if (resp.success) {
          window.location.href = '/kiosk';
        }
      })
      .catch(() => undefined);
  }

  if (loading) {
    return (
      <div className="d-flex justify-content-center align-items-center" style={{ minHeight: '30vh' }}>
        <div className="spinner-border text-primary" role="status"></div>
      </div>
    );
  }

  return (
    <div>
      <style>{`
        body {
          font-family: Arial, sans-serif;
          max-width: 600px;
          margin: 0 auto;
          padding: 20px;
          background-color: #f5f5f5;
        }
        .header {
          background-color: #4CAF50;
          color: white;
          padding: 20px;
          border-radius: 10px 10px 0 0;
          text-align: center;
        }
        .form-container {
          background-color: white;
          padding: 30px;
          border-radius: 0 0 10px 10px;
          box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        .info-box {
          background-color: #e8f5e9;
          padding: 15px;
          border-radius: 5px;
          margin-bottom: 20px;
          border-left: 4px solid #4CAF50;
        }
        .form-group {
          margin-bottom: 20px;
        }
        label {
          display: block;
          margin-bottom: 5px;
          font-weight: bold;
          color: #333;
        }
        input[type="text"] {
          width: 100%;
          padding: 10px;
          border: 1px solid #ddd;
          border-radius: 5px;
          font-size: 16px;
          box-sizing: border-box;
        }
        input[type="text"]:focus {
          outline: none;
          border-color: #4CAF50;
          box-shadow: 0 0 5px rgba(76, 175, 80, 0.3);
        }
        .submit-btn {
          background-color: #4CAF50;
          color: white;
          padding: 12px 30px;
          border: none;
          border-radius: 5px;
          font-size: 16px;
          cursor: pointer;
          width: 100%;
          transition: background-color 0.3s;
        }
        .submit-btn:hover {
          background-color: #45a049;
        }
        .requirements {
          background-color: #fff3cd;
          border: 1px solid #ffeaa7;
          color: #856404;
          padding: 15px;
          border-radius: 5px;
          margin-bottom: 20px;
          font-size: 14px;
        }
        .requirements ul {
          margin: 10px 0 0 20px;
          padding: 0;
        }
        .requirements li {
          margin-bottom: 5px;
        }
        .capture-summary {
          background-color: #e3f2fd;
          padding: 15px;
          border-radius: 5px;
          margin-bottom: 20px;
        }
        .sample-images {
          display: flex;
          justify-content: space-around;
          margin: 20px 0;
          flex-wrap: wrap;
        }
        .sample-box {
          text-align: center;
          margin: 10px;
        }
        .sample-number {
          width: 60px;
          height: 60px;
          background-color: #4CAF50;
          color: white;
          border-radius: 50%;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 20px;
          font-weight: bold;
          margin: 0 auto 10px;
        }
        .quality-indicator {
          font-size: 12px;
          color: #666;
        }
      `}</style>

      <div className="header">
        <h1>Register New User</h1>
        <p>Advanced Registration with Multiple Face Samples</p>
      </div>

      <div className="form-container">
        <div className="info-box">
          <h3>Face Capture Successful!</h3>
          <p>
            We've captured <strong>{captureCount} face samples</strong> for better recognition accuracy.
          </p>
          <p>Each sample represents a slightly different angle or expression.</p>
        </div>

        <div className="requirements">
          <h4>Registration Requirements:</h4>
          <ul>
            <li>All fields are required</li>
            <li>SR Code must be unique</li>
            <li>Use proper capitalization for names</li>
            <li>Ensure information is accurate</li>
          </ul>
        </div>

        <div className="capture-summary">
          <h4>Captured Face Samples:</h4>
          <div className="sample-images">
            <div className="sample-box">
              <div className="sample-number">1</div>
              <div className="quality-indicator">Front view</div>
            </div>
            <div className="sample-box">
              <div className="sample-number">2</div>
              <div className="quality-indicator">Slight left</div>
            </div>
            <div className="sample-box">
              <div className="sample-number">3</div>
              <div className="quality-indicator">Slight right</div>
            </div>
            {captureCount > 3 ? (
              <div className="sample-box">
                <div className="sample-number">+{captureCount - 3}</div>
                <div className="quality-indicator">Extra samples</div>
              </div>
            ) : null}
          </div>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label htmlFor="name">Full Name</label>
            <input
              type="text"
              id="name"
              name="name"
              value={form.name}
              onChange={(ev) => updateForm('name', ev.target.value)}
              required
            />
          </div>
          <div className="form-group">
            <label htmlFor="sr_code">SR Code</label>
            <input
              type="text"
              id="sr_code"
              name="sr_code"
              value={form.sr_code}
              onChange={(ev) => updateForm('sr_code', ev.target.value)}
              required
            />
          </div>
          <div className="form-group">
            <label htmlFor="course">Course</label>
            <input
              type="text"
              id="course"
              name="course"
              value={form.course}
              onChange={(ev) => updateForm('course', ev.target.value)}
              required
            />
          </div>
          <button className="submit-btn" type="submit">Complete Registration</button>
        </form>
      </div>
    </div>
  );
}
