import React from 'react';
import { useLocation } from 'react-router-dom';
import { useSession, useTheme } from '../App.jsx';

function getInitials(name) {
  if (!name) return 'a';
  const parts = name.split(' ').filter(Boolean);
  const first = parts[0]?.[0] || 'a';
  const second = parts[1]?.[0] || '';
  return (first + second).toLowerCase();
}

export default function Header({ onToggleSidebar, sidebarCollapsed = false }) {
  const { session, refresh } = useSession();
  const { theme, toggleTheme } = useTheme();
  const location = useLocation();
  const displayName = session?.full_name || session?.username || 'Admin';
  const initials = getInitials(displayName);
  const isAdmin = session?.role === 'super_admin' || session?.role === 'library_admin';
  const isDark = theme === 'dark';
  const [registrationInfo, setRegistrationInfo] = React.useState(null);

  React.useEffect(() => {
    if (!session) {
      setRegistrationInfo(null);
      return undefined;
    }

    let cancelled = false;
    async function loadRegistrationInfo() {
      try {
        const response = await fetch('/api/register-info', { credentials: 'include' });
        if (!response.ok) {
          if (!cancelled && (response.status === 401 || response.status === 403)) {
            setRegistrationInfo(null);
          }
          return;
        }
        const payload = await response.json();
        if (!cancelled) {
          setRegistrationInfo(payload);
        }
      } catch {
        // Keep previous indicator state on transient fetch failures.
      }
    }

    loadRegistrationInfo();
    const timer = window.setInterval(loadRegistrationInfo, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [session]);

  const registrationActive = Boolean(
    registrationInfo
    && (
      registrationInfo.ready_to_submit
      || registrationInfo.has_pending_registration
      || registrationInfo.is_in_progress
      || registrationInfo.web_session_active
    )
  );
  const isRegistrationPage = location.pathname === '/register';
  const registrationCtaVisible = registrationActive && !isRegistrationPage;
  const registrationCtaLabel = registrationInfo?.ready_to_submit ? 'Open Registration (Ready)' : 'Open Registration';
  const registrationReason = (registrationInfo?.status_reason_message || '').trim();

  async function handleLogout(ev) {
    ev.preventDefault();
    await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' });
    refresh();
    window.location.href = '/login';
  }

  return (
    <header id="header" className="header fixed-top d-flex align-items-center">
      <div className={`header-brand d-flex align-items-center flex-nowrap${sidebarCollapsed ? ' is-collapsed' : ''}`}>
        {sidebarCollapsed ? (
          <>
            <a href="/dashboard" className="logo d-flex align-items-center" aria-label="Dashboard">
              <img src="/static/assets/img/bsu-neu-logo.png" alt="BatStateU Logo" className="header-collapsed-logo" />
            </a>
            <button
              type="button"
              className="btn btn-link p-0 border-0 text-decoration-none toggle-sidebar-btn"
              onClick={onToggleSidebar}
              aria-label="Toggle sidebar"
            >
              <i className="bi bi-list"></i>
            </button>
          </>
        ) : (
          <>
            <a href="/dashboard" className="logo d-flex align-items-center">
              <span className="d-none d-lg-block">Library Management</span>
            </a>
            <button
              type="button"
              className="btn btn-link p-0 border-0 text-decoration-none toggle-sidebar-btn"
              onClick={onToggleSidebar}
              aria-label="Toggle sidebar"
            >
              <i className="bi bi-list"></i>
            </button>
          </>
        )}
      </div>

      <nav className="header-nav ms-auto">
        <ul className="d-flex align-items-center">
          {registrationCtaVisible ? (
            <li className="nav-item me-2">
              <a
                href="/register"
                className="btn btn-sm btn-warning d-inline-flex align-items-center gap-2"
                title={registrationReason || 'Registration activity is in progress.'}
              >
                <i className="bi bi-person-plus-fill"></i>
                <span>{registrationCtaLabel}</span>
              </a>
            </li>
          ) : null}
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
                <button className="dropdown-item d-flex align-items-center" type="button" onClick={toggleTheme}>
                  <i className={`bi ${isDark ? 'bi-sun' : 'bi-moon'} me-2`}></i>
                  <span>{isDark ? 'Light Mode' : 'Dark Mode'}</span>
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
