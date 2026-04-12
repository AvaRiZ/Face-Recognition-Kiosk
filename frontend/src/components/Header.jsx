import React from 'react';
import { useSession } from '../App.jsx';

function getInitials(name) {
  if (!name) return 'a';
  const parts = name.split(' ').filter(Boolean);
  const first = parts[0]?.[0] || 'a';
  const second = parts[1]?.[0] || '';
  return (first + second).toLowerCase();
}

export default function Header() {
  const { session, refresh } = useSession();
  const displayName = session?.full_name || session?.username || 'Admin';
  const initials = getInitials(displayName);
  const isAdmin = session?.role === 'super_admin' || session?.role === 'library_admin';

  async function handleLogout(ev) {
    ev.preventDefault();
    await fetch('/api/logout', { method: 'POST', credentials: 'include' });
    refresh();
    window.location.href = '/login';
  }

  return (
    <header id="header" className="header fixed-top d-flex align-items-center">
      <div className="d-flex align-items-center justify-content-between">
        <a href="/dashboard" className="logo d-flex align-items-center">
          <span className="d-none d-lg-block">Library Management</span>
        </a>
        <i className="bi bi-list toggle-sidebar-btn"></i>
      </div>

      <nav className="header-nav ms-auto">
        <ul className="d-flex align-items-center">
          <li className="nav-item dropdown pe-3">
            <a className="nav-link nav-profile d-flex align-items-center pe-0" href="#" data-bs-toggle="dropdown">
              {session?.profile_image ? (
                <img src={session.profile_image} alt="Profile" className="profile-avatar-img" />
              ) : (
                <span className="profile-avatar-fallback">{initials}</span>
              )}
              <span className="d-none d-md-block ps-1">{displayName}</span>
              <i className="bi bi-caret-down-fill profile-caret ms-1"></i>
            </a>
            <ul className="dropdown-menu dropdown-menu-end dropdown-menu-arrow profile">
              <li className="dropdown-header">
                <h6>{displayName}</h6>
                <span>{session?.role || 'staff'}</span>
              </li>
              <li>
                <hr className="dropdown-divider" />
              </li>
              <li>
                <button id="profileThemeToggle" className="dropdown-item d-flex align-items-center" type="button">
                  <i className="bi bi-moon me-2"></i>
                  <span>Dark Mode</span>
                </button>
              </li>
              <li>
                <hr className="dropdown-divider" />
              </li>
              <li>
                <a className="dropdown-item d-flex align-items-center" href="/profile">
                  <i className="bi bi-person-circle"></i>
                  <span>Profile Settings</span>
                </a>
              </li>
              <li>
                <hr className="dropdown-divider" />
              </li>
              {isAdmin ? (
                <>
                  <li>
                    <a className="dropdown-item d-flex align-items-center" href="/route-list">
                      <i className="bi bi-diagram-3"></i>
                      <span>Route List</span>
                    </a>
                  </li>
                  <li>
                    <hr className="dropdown-divider" />
                  </li>
                </>
              ) : null}
              {session?.role === 'super_admin' ? (
                <>
                  <li>
                    <a className="dropdown-item d-flex align-items-center" href="/manage-users">
                      <i className="bi bi-people"></i>
                      <span>Manage User</span>
                    </a>
                  </li>
                  <li>
                    <hr className="dropdown-divider" />
                  </li>
                </>
              ) : null}
              <li>
                <a className="dropdown-item d-flex align-items-center" href="/policy">
                  <i className="bi bi-shield-check"></i>
                  <span>Policy</span>
                </a>
              </li>
              <li>
                <hr className="dropdown-divider" />
              </li>
              <li>
                <button className="dropdown-item d-flex align-items-center" type="button" onClick={handleLogout}>
                  <i className="bi bi-box-arrow-right"></i>
                  <span>Sign Out</span>
                </button>
              </li>
            </ul>
          </li>
        </ul>
      </nav>
    </header>
  );
}
