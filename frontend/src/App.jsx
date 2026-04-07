import React from 'react';
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import Layout from './components/Layout.jsx';
import LoginPage from './pages/Login.jsx';
import DashboardPage from './pages/Dashboard.jsx';
import RegisteredProfilesPage from './pages/RegisteredProfiles.jsx';
import ArchiveProfilesPage from './pages/ArchiveProfiles.jsx';
import ArchivedProfilesPage from './pages/ArchivedProfiles.jsx';
import EntryExitLogsPage from './pages/EntryExitLogs.jsx';
import AnalyticsReportsPage from './pages/AnalyticsReports.jsx';
import RouteListPage from './pages/RouteList.jsx';
import ManageUsersPage from './pages/ManageUsers.jsx';
import SettingsPage from './pages/Settings.jsx';
import PolicyPage from './pages/Policy.jsx';
import ProfileSettingsPage from './pages/ProfileSettings.jsx';
import RegisterPage from './pages/Register.jsx';

const SessionContext = React.createContext({
  session: null,
  loading: true,
  refresh: () => undefined
});

export function useSession() {
  return React.useContext(SessionContext);
}

function useSessionState() {
  const [session, setSession] = React.useState(null);
  const [loading, setLoading] = React.useState(true);

  const refresh = React.useCallback(() => {
    setLoading(true);
    fetch('/api/session', { credentials: 'include' })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        setSession(data && data.authenticated ? data : null);
        setLoading(false);
      })
      .catch(() => {
        setSession(null);
        setLoading(false);
      });
  }, []);

  React.useEffect(() => {
    refresh();
  }, [refresh]);

  return { session, loading, refresh };
}

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

export default function App() {
  const sessionState = useSessionState();
  const location = useLocation();
  const navigate = useNavigate();

  React.useEffect(() => {
    let cancelled = false;

    async function syncPendingRegistration() {
      try {
        const resp = await fetch('/api/register-info', { credentials: 'include' });
        if (!resp.ok) {
          return;
        }
        const payload = await resp.json();
        if (cancelled) {
          return;
        }
        if (payload?.has_pending_registration && location.pathname !== '/register') {
          navigate('/register', { replace: true });
        }
      } catch {
        // Ignore polling failures and keep the current route.
      }
    }

    syncPendingRegistration();
    const timer = window.setInterval(syncPendingRegistration, 1500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [location.pathname, navigate]);

  return (
    <SessionContext.Provider value={sessionState}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <Layout />
            </ProtectedRoute>
          }
        >
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard" element={<DashboardPage />} />
          <Route path="registered-profiles" element={<RegisteredProfilesPage />} />
          <Route path="archive-profiles" element={<ArchiveProfilesPage />} />
          <Route path="archived-profiles" element={<ArchivedProfilesPage />} />
          <Route path="entry-exit-logs" element={<EntryExitLogsPage />} />
          <Route path="analytics-reports" element={<AnalyticsReportsPage />} />
          <Route path="routes" element={<RouteListPage />} />
          <Route path="route-list" element={<RouteListPage />} />
          <Route path="manage-users" element={<ManageUsersPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="policy" element={<PolicyPage />} />
          <Route path="profile" element={<ProfileSettingsPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </SessionContext.Provider>
  );
}
