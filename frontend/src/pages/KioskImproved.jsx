import React from 'react';

async function showPopup(icon, title, text) {
  if (window.Swal) {
    await window.Swal.fire({ icon, title, text });
    return;
  }
  window.alert(`${title}: ${text}`);
}

export default function KioskImprovedPage() {
  const [statusText, setStatusText] = React.useState('Initializing...');
  const [captureProgress, setCaptureProgress] = React.useState({ current: 0, total: 3, percentage: 0 });
  const [recognizedUser, setRecognizedUser] = React.useState(null);
  const [pendingRegistration, setPendingRegistration] = React.useState(false);
  const [userCount, setUserCount] = React.useState(0);

  React.useEffect(() => {
    fetch('/api/kiosk-metrics')
      .then((res) => res.json())
      .then((resp) => setUserCount(resp.user_count || 0))
      .catch(() => undefined);

    let intervalId = null;

    const updateStatus = (payload) => {
      if (payload.recognized_user) {
        setStatusText(`Recognized: ${payload.recognized_user.name}`);
        setRecognizedUser(payload.recognized_user);
      } else if (payload.pending_registration) {
        setStatusText('New face detected - Ready for registration');
        setPendingRegistration(true);
      } else {
        setStatusText('Detecting faces...');
      }

      if (payload.capture_progress) {
        setCaptureProgress(payload.capture_progress);
      }
    };

    const checkStatus = () => {
      fetch('/check_status')
        .then((response) => response.json())
        .then(updateStatus)
        .catch(console.error);
    };

    checkStatus();
    intervalId = window.setInterval(checkStatus, 1000);

    return () => {
      if (intervalId) window.clearInterval(intervalId);
    };
  }, []);

  function closePopup(popupId) {
    if (popupId === 'recognized-popup') {
      setRecognizedUser(null);
    }
    if (popupId === 'register-popup') {
      setPendingRegistration(false);
    }
  }

  function goToRegister() {
    window.location.href = '/register';
  }

  async function resetRegistration() {
    fetch('/api/reset_registration', { method: 'POST' })
      .then((response) => response.json())
      .then(async (resp) => {
        if (resp.success !== false) {
          await showPopup('success', 'Completed', resp.message || 'Registration reset successfully.');
          window.location.reload();
        } else {
          await showPopup('error', 'Request Failed', resp.message || 'Unable to reset registration.');
        }
      })
      .catch(async (error) => {
        await showPopup('error', 'Request Failed', error.message || 'Unexpected error occurred.');
      });
  }

  return (
    <div>
      <style>{`
        body {
          font-family: Arial, sans-serif;
          max-width: 800px;
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
        .status-panel {
          background-color: white;
          padding: 15px;
          margin: 10px 0;
          border-radius: 5px;
          box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .video-container {
          text-align: center;
          margin: 20px 0;
        }
        .video-container img {
          border: 3px solid #4CAF50;
          border-radius: 10px;
          max-width: 100%;
        }
        .progress-container {
          background-color: #e8f5e9;
          padding: 10px;
          border-radius: 5px;
          margin: 10px 0;
        }
        .progress-bar {
          height: 20px;
          background-color: #4CAF50;
          border-radius: 10px;
          transition: width 0.3s;
        }
        .popup {
          display: none;
          position: fixed;
          top: 50%;
          left: 50%;
          transform: translate(-50%, -50%);
          background: white;
          padding: 30px;
          border-radius: 10px;
          box-shadow: 0 4px 20px rgba(0,0,0,0.2);
          z-index: 1000;
          text-align: center;
        }
        .stats-panel {
          background-color: white;
          padding: 15px;
          margin: 10px 0;
          border-radius: 5px;
        }
        .button {
          background-color: #4CAF50;
          color: white;
          padding: 10px 20px;
          border: none;
          border-radius: 5px;
          cursor: pointer;
          margin: 5px;
        }
        .button:hover {
          background-color: #45a049;
        }
      `}</style>

      <div className="header">
        <h1>Improved Face Recognition Kiosk</h1>
        <p>Advanced face recognition with multiple samples</p>
      </div>

      <div className="status-panel">
        <h3>System Status</h3>
        <p>
          <strong>Status:</strong> <span>{statusText}</span>
        </p>
        <p>
          <strong>Users in database:</strong> {userCount}
        </p>
        <p>
          <strong>Face capture progress:</strong> <span>{captureProgress.current}/{captureProgress.total}</span>
          <div className="progress-container">
            <div className="progress-bar" style={{ width: `${captureProgress.percentage}%` }}></div>
          </div>
        </p>
      </div>

      <div className="video-container">
        <img src="/video_feed" width="640" />
      </div>

      <div className="stats-panel">
        <h3>Quick Actions</h3>
        <button className="button" onClick={() => (window.location.href = '/settings')}>
          Settings
        </button>
        <button className="button" onClick={() => (window.location.href = '/api/stats')}>
          View Stats
        </button>
        <button className="button" onClick={resetRegistration}>
          Reset Registration
        </button>
      </div>

      {recognizedUser ? (
        <div id="recognized-popup" className="popup" style={{ display: 'block' }}>
          <h2>Welcome!</h2>
          <div>
            <p>
              <strong>Name:</strong> {recognizedUser.name}
            </p>
            <p>
              <strong>SR Code:</strong> {recognizedUser.sr_code}
            </p>
          </div>
          <p>Confidence: {recognizedUser.confidence}</p>
          <button className="button" onClick={() => closePopup('recognized-popup')}>
            OK
          </button>
        </div>
      ) : null}

      {pendingRegistration ? (
        <div id="register-popup" className="popup" style={{ display: 'block' }}>
          <h2>New Face Detected</h2>
          <p>
            We've captured <span>{captureProgress.total}</span> samples of this new face.
          </p>
          <p>Please register this new user:</p>
          <button className="button" onClick={goToRegister}>
            Register Now
          </button>
          <button
            className="button"
            onClick={() => closePopup('register-popup')}
            style={{ backgroundColor: '#f44336' }}
          >
            Cancel
          </button>
        </div>
      ) : null}
    </div>
  );
}
