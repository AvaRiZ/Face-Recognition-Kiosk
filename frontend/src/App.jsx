import React from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import Layout from './components/Layout.jsx';
import { AppProviders, useSession } from './contexts.jsx';
import LoginPage from './pages/Login.jsx';
import DashboardPage from './pages/Dashboard.jsx';
import RegisteredProfilesPage from './pages/RegisteredProfiles.jsx';
import ArchiveProfilesPage from './pages/ArchiveProfiles.jsx';
import ArchivedProfilesPage from './pages/ArchivedProfiles.jsx';
import EntryExitLogsPage from './pages/EntryExitLogs.jsx';
import ProgramMonthlyVisitsPage from './pages/ProgramMonthlyVisits.jsx';
import AnalyticsReportsPage from './pages/AnalyticsReports.jsx';
import RouteListPage from './pages/RouteList.jsx';
import ManageUsersPage from './pages/ManageUsers.jsx';
import SettingsPage from './pages/Settings.jsx';
import PolicyPage from './pages/Policy.jsx';
import ProfileSettingsPage from './pages/ProfileSettings.jsx';
import RegisterPage from './pages/Register.jsx';
import UnauthorizedPage from './pages/Unauthorized.jsx';

export { useSession, useTheme } from './contexts.jsx';

function ProtectedRoute({ children }) {
  const { session, loading } = useSession();
  const location = useLocation();

  if (loading) {
    return (
      <div className="d-flex justify-content-center align-items-center" style={{ minHeight: '50vh' }}>
        <div className="spinner-border text-primary" role="status">
          <span className="visually-hidden">Loading...</span>
        </div>
      </div>
    );
  }

  if (!session) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return children;
}

function RoleProtectedRoute({ roles, children }) {
  const { session, loading } = useSession();

  if (loading) {
    return (
      <div className="d-flex justify-content-center align-items-center" style={{ minHeight: '50vh' }}>
        <div className="spinner-border text-primary" role="status">
          <span className="visually-hidden">Loading...</span>
        </div>
      </div>
    );
  }

  if (!session) {
    return <Navigate to="/login" replace />;
  }

  if (!roles.includes(session.role)) {
    return <Navigate to="/unauthorized" replace />;
  }

  return children;
}

export default function App() {
  return (
    <AppProviders>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/register"
          element={(
            <ProtectedRoute>
              <RoleProtectedRoute roles={['super_admin', 'library_admin', 'library_staff']}>
                <RegisterPage />
              </RoleProtectedRoute>
            </ProtectedRoute>
          )}
        />
        <Route path="/unauthorized" element={<UnauthorizedPage />} />
        <Route
          path="/"
          element={(
            <ProtectedRoute>
              <Layout />
            </ProtectedRoute>
          )}
        >
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard" element={<DashboardPage />} />
          <Route path="registered-profiles" element={<RegisteredProfilesPage />} />
          <Route path="archive-profiles" element={<ArchiveProfilesPage />} />
          <Route path="archived-profiles" element={<ArchivedProfilesPage />} />
          <Route path="entry-logs" element={<EntryExitLogsPage />} />
          <Route path="program-monthly-visits" element={<ProgramMonthlyVisitsPage />} />
          <Route path="analytics-reports" element={<AnalyticsReportsPage />} />
          <Route path="route-list" element={<RouteListPage />} />
          <Route path="routes" element={<Navigate to="/route-list" replace />} />
          <Route path="manage-users" element={<ManageUsersPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="policy" element={<PolicyPage />} />
          <Route path="profile" element={<ProfileSettingsPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </AppProviders>
  );
}
