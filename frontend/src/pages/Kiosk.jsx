import React from 'react';

export default function KioskPage() {
  const [statusText, setStatusText] = React.useState('Initializing...');
  const [recognizedUser, setRecognizedUser] = React.useState(null);
  const [pendingRegistration, setPendingRegistration] = React.useState(false);

  React.useEffect(() => {
    let intervalId = null;

    const checkStatus = () => {
      fetch('/check_status')
        .then((response) => response.json())
        .then((payload) => {
          if (payload.recognized_user) {
            setRecognizedUser(payload.recognized_user);
            setStatusText(`Recognized: ${payload.recognized_user.name}`);
          } else if (payload.pending_registration) {
            setPendingRegistration(true);
            setStatusText('New face detected - Please register');
          } else {
            setStatusText('Camera active - Detecting faces...');
          }
        })
        .catch((error) => {
          console.error('Error checking status:', error);
        });
    };

    checkStatus();
    intervalId = window.setInterval(checkStatus, 1000);

    return () => {
      if (intervalId) window.clearInterval(intervalId);
      if (navigator.sendBeacon) {
        navigator.sendBeacon('/stop_feed');
      }
    };
  }, []);

  function closeRecognizedPopup() {
    setRecognizedUser(null);
  }

  function closeRegisterPopup() {
    setPendingRegistration(false);
    fetch('/api/reset_registration', { method: 'POST' }).catch(() => undefined);
  }

  function goToRegister() {
    window.location.href = '/register';
  }

  return (
    <div>
      <style>{`
        .popup {
          display: none;
          position: fixed;
          z-index: 1000;
          left: 50%;
          top: 50%;
          transform: translate(-50%, -50%);
          background-color: white;
          padding: 20px;
          border: 3px solid #4caf50;
          border-radius: 10px;
          box-shadow: 0 4px 8px rgba(0, 0, 0, 0.2);
          min-width: 300px;
        }
        .popup h3 {
          margin-top: 0;
          color: #4caf50;
        }
        .popup button {
          background-color: #4caf50;
          color: white;
          border: none;
          padding: 10px 20px;
          border-radius: 5px;
          cursor: pointer;
          margin-top: 10px;
        }
        .popup button:hover {
          background-color: #45a049;
        }
        .status-box {
          background-color: #f0f0f0;
          padding: 10px;
          border-radius: 5px;
          margin: 10px 0;
          border-left: 4px solid #4caf50;
        }
      `}</style>

      <h1>Library Access Kiosk</h1>

      <div className="status-box">
        <strong>Status:</strong> <span>{statusText}</span>
      </div>

      <div className="video-container">
        <img src="/video_feed" width="720" />
      </div>

      {recognizedUser ? (
        <div id="recognized-popup" className="popup" style={{ display: 'block' }}>
          <h3>Welcome!</h3>
          <p>
            <strong>Name:</strong> {recognizedUser.name}
            <br />
            <strong>SR Code:</strong> {recognizedUser.sr_code}
            <br />
            <strong>Course:</strong> {recognizedUser.course}
          </p>
          <button onClick={closeRecognizedPopup}>Close</button>
        </div>
      ) : null}

      {pendingRegistration ? (
        <div id="register-popup" className="popup" style={{ display: 'block' }}>
          <h3>New Face Detected</h3>
          <p>Please register this new face.</p>
          <button onClick={goToRegister}>Register Now</button>
          <button onClick={closeRegisterPopup} style={{ backgroundColor: '#f44336' }}>
            Cancel
          </button>
        </div>
      ) : null}

      <footer>
        (c) 2026 Library Facial Recognition System | Using DeepFace
      </footer>
    </div>
  );
}
