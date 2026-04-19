import React from 'react';
import { NavLink } from 'react-router-dom';
import { useSession } from '../App.jsx';
import { confirmAction } from '../alerts.js';

function getInitials(name) {
  if (!name) return 'SA';
  const parts = name.split(' ').filter(Boolean);
  const first = parts[0]?.[0] || 'S';
  const second = parts[1]?.[0] || '';
  return (first + second).toUpperCase();
}

function formatRoleLabel(role) {
  if (role === 'super_admin') return 'Super Admin';
  if (role === 'library_admin') return 'Administrator';
  return 'Library Staff';
}

function NavItem({ to, icon, label }) {
  return (
    <li className="nav-item">
      <NavLink
        className={({ isActive }) => `nav-link ${isActive ? '' : 'collapsed'}`}
        to={to}
        title={label}
        aria-label={label}
      >
        <i className={icon}></i>
        <span>{label}</span>
      </NavLink>
    </li>
  );
}

export default function Sidebar() {
  const { session, refresh } = useSession();
  const role = session?.role || '';
  const isAdmin = role === 'super_admin' || role === 'library_admin';
  const isStaff = role === 'super_admin' || role === 'library_admin' || role === 'library_staff';
  const canSeeManagement = isAdmin || role === 'super_admin';
  const displayName = session?.full_name || session?.username || 'System User';
  const roleLabel = formatRoleLabel(role);
  const initials = getInitials(displayName);

  async function handleLogout(ev) {
    ev.preventDefault();
    const confirmed = await confirmAction({
      title: 'Sign Out?',
      text: 'Are you sure you want to log out of your account?',
      confirmButtonText: 'Sign Out',
      confirmButtonColor: '#dc3545'
    });
    if (!confirmed) return;
    await fetch('/api/logout', { method: 'POST', credentials: 'include' });
    refresh();
    window.location.href = '/login';
  }

  return (
    <aside id="sidebar" className="sidebar">
      <div className="sidebar-shell">
        <div className="sidebar-top">
          <div className="sidebar-brand">
            <div className="sidebar-brand-title">{displayName.toUpperCase()}</div>
            <div className="sidebar-brand-subtitle">{roleLabel.toUpperCase()} PORTAL</div>
          </div>
        </div>

        <div className="sidebar-menu">
          <ul className="sidebar-nav" id="sidebar-nav">
            <li className="nav-heading">Library System</li>
            <NavItem to="/dashboard" icon="bi bi-house-door" label="Dashboard" />
            {isAdmin ? <NavItem to="/registered-profiles" icon="bi bi-people" label="Registered Profiles" /> : null}
            {isStaff ? <NavItem to="/entry-exit-logs" icon="bi bi-clipboard-check" label="Entry / Exit Logs" /> : null}
            {role ? <NavItem to="/program-monthly-visits" icon="bi bi-bar-chart" label="Program Monthly Visits" /> : null}

            {canSeeManagement ? <li className="nav-heading">Management</li> : null}
            {isAdmin ? <NavItem to="/analytics-reports" icon="bi bi-graph-up-arrow" label="Reports & Analytics" /> : null}
            {isAdmin ? <NavItem to="/route-list" icon="bi bi-diagram-3" label="Route List" /> : null}
            {role === 'super_admin' ? <NavItem to="/settings" icon="bi bi-gear" label="Settings" /> : null}
          </ul>
        </div>

        <div className="sidebar-footer">
          <a href="/login" className="sidebar-profile-card" onClick={handleLogout}>
            {session?.profile_image ? (
              <img src={session.profile_image} alt="Profile" className="sidebar-profile-avatar-img" />
            ) : (
              <span className="sidebar-profile-avatar">{initials}</span>
            )}
            <div className="sidebar-profile-meta">
              <div className="sidebar-profile-name">{displayName}</div>
              <div className="sidebar-profile-role">{roleLabel}</div>
            </div>
            <i className="bi bi-box-arrow-right sidebar-profile-action"></i>
          </a>
        </div>
      </div>
    </aside>
  );
}
