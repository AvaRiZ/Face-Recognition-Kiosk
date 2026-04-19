import React from 'react';
import { fetchJson } from '../api.js';
import { getErrorMessage, showError, showSuccess } from '../alerts.js';

export default function ProfileSettingsPage() {
  const [staff, setStaff] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [fullName, setFullName] = React.useState('');
  const [username, setUsername] = React.useState('');

  React.useEffect(() => {
    fetchJson('/api/profile')
      .then((resp) => {
        setStaff(resp.staff);
        setFullName(resp.staff?.full_name || '');
        setUsername(resp.staff?.username || '');
      })
      .catch(() => setStaff(null))
      .finally(() => setLoading(false));
  }, []);

  async function handleProfileSubmit(ev) {
    ev.preventDefault();
    const formData = new FormData();
    formData.append('full_name', fullName);
    formData.append('username', username);
    const fileInput = ev.currentTarget.querySelector('#profile_image');
    if (fileInput && fileInput.files && fileInput.files[0]) {
      formData.append('profile_image', fileInput.files[0]);
    }

    try {
      const res = await fetch('/api/profile', {
        method: 'POST',
        body: formData,
        credentials: 'include'
      });
      const resp = await res.json();
      if (!res.ok) {
        throw new Error(resp?.message || 'Unable to save profile.');
      }
      setStaff(resp.staff);
      await showSuccess('Profile Saved', 'Your profile was updated successfully.');
    } catch (error) {
      await showError('Save Failed', getErrorMessage(error));
    }
  }

  async function handlePasswordSubmit(ev) {
    ev.preventDefault();
    const form = ev.currentTarget;
    const payload = {
      current_password: form.current_password.value,
      new_password: form.new_password.value,
      confirm_password: form.confirm_password.value
    };

    try {
      await fetchJson('/api/profile/password', {
        method: 'POST',
        body: JSON.stringify(payload)
      });
      form.reset();
      await showSuccess('Password Updated', 'Your password was changed successfully.');
    } catch (error) {
      await showError('Update Failed', getErrorMessage(error));
    }
  }

  if (loading) {
    return (
      <div className="d-flex justify-content-center align-items-center" style={{ minHeight: '30vh' }}>
        <div className="spinner-border text-primary" role="status"></div>
      </div>
    );
  }

  if (!staff) {
    return <div className="text-muted">Unable to load profile.</div>;
  }

  return (
    <section className="section">
      <div className="pagetitle">
        <h1>Profile Settings</h1>
      </div>
      <div className="row g-3">
        <div className="col-lg-5">
          <div className="card">
            <div className="card-body">
              <h5 className="card-title">Profile Information</h5>

              <div className="mb-3">
                {staff.profile_image ? (
                  <img
                    src={staff.profile_image}
                    alt="Profile"
                    className="rounded-circle"
                    width="84"
                    height="84"
                    style={{ objectFit: 'cover' }}
                  />
                ) : (
                  <div
                    className="rounded-circle d-inline-flex align-items-center justify-content-center text-white fw-bold"
                    style={{
                      width: '84px',
                      height: '84px',
                      background: 'linear-gradient(135deg, #6f2bff, #b100e8)'
                    }}
                  >
                    {(staff.full_name?.[0] || 'a').toLowerCase()}
                  </div>
                )}
              </div>

              <form className="row g-3" onSubmit={handleProfileSubmit}>
                <div className="col-12">
                  <label className="form-label" htmlFor="full_name">Full Name</label>
                  <input
                    id="full_name"
                    name="full_name"
                    className="form-control"
                    value={fullName}
                    onChange={(ev) => setFullName(ev.target.value)}
                    required
                  />
                </div>

                <div className="col-12">
                  <label className="form-label" htmlFor="username">Username</label>
                  <input
                    id="username"
                    name="username"
                    className="form-control"
                    value={username}
                    onChange={(ev) => setUsername(ev.target.value)}
                    required
                  />
                </div>

                <div className="col-12">
                  <label className="form-label" htmlFor="profile_image">Profile Picture</label>
                  <input
                    id="profile_image"
                    name="profile_image"
                    type="file"
                    className="form-control"
                    accept=".jpg,.jpeg,.png,.webp"
                  />
                  <div className="form-text">JPG, JPEG, PNG, WEBP.</div>
                </div>

                <div className="col-12">
                  <button type="submit" className="btn btn-primary">Save Profile</button>
                </div>
              </form>
            </div>
          </div>
        </div>

        <div className="col-lg-7">
          <div className="card">
            <div className="card-body">
              <h5 className="card-title">Change Password</h5>

              <form className="row g-3" onSubmit={handlePasswordSubmit}>
                <div className="col-12">
                  <label className="form-label" htmlFor="current_password">Current Password</label>
                  <input id="current_password" name="current_password" type="password" className="form-control" required />
                </div>

                <div className="col-12">
                  <label className="form-label" htmlFor="new_password">New Password</label>
                  <input id="new_password" name="new_password" type="password" className="form-control" minLength={8} required />
                </div>

                <div className="col-12">
                  <label className="form-label" htmlFor="confirm_password">Confirm New Password</label>
                  <input id="confirm_password" name="confirm_password" type="password" className="form-control" minLength={8} required />
                </div>

                <div className="col-12">
                  <button type="submit" className="btn btn-outline-primary">Update Password</button>
                </div>
              </form>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
