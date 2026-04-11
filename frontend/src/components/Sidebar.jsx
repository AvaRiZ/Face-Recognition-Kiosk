import React from 'react';
import { NavLink } from 'react-router-dom';
import { useSession } from '../App.jsx';

function NavItem({ to, icon, label }) {
  return (
    <li className="nav-item">
      <NavLink className={({ isActive }) => `nav-link ${isActive ? '' : 'collapsed'}`} to={to}>
        <i className={icon}></i>
        <span>{label}</span>
      </NavLink>
    </li>
  );
}

export default function Sidebar() {
  const { session } = useSession();
  const role = session?.role || '';
  const isAdmin = role === 'super_admin' || role === 'library_admin';
  const isStaff = role === 'super_admin' || role === 'library_admin' || role === 'library_staff';

  return (
    <aside id="sidebar" className="sidebar">
      <ul className="sidebar-nav" id="sidebar-nav">
        <NavItem to="/dashboard" icon="bi bi-grid" label="Dashboard" />

        {isAdmin ? (
          <>
            <NavItem to="/registered-profiles" icon="bi bi-people-fill" label="Registered Profiles" />
            <NavItem to="/analytics-reports" icon="bi bi-graph-up" label="Analytics & Reports" />
          </>
        ) : null}

        {isStaff ? <NavItem to="/entry-exit-logs" icon="bi bi-people-fill" label="Entry/Exit Logs" /> : null}
        {role ? <NavItem to="/program-monthly-visits" icon="bi bi-table" label="Program Monthly Visits" /> : null}

        {role === 'super_admin' ? <NavItem to="/settings" icon="bi bi-gear" label="Settings" /> : null}
      </ul>
    </aside>
  );
}
