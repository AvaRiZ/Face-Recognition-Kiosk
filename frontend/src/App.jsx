import React from 'react';
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import Layout from './components/Layout.jsx';
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

const THEME_STORAGE_KEY = 'theme';

const SessionContext = React.createContext({
  session: null,
  loading: true,
  refresh: () => undefined
});

const ThemeContext = React.createContext({
  theme: 'light',
  toggleTheme: () => undefined
});

export function useSession() {
  return React.useContext(SessionContext);
}

export function useTheme() {
  return React.useContext(ThemeContext);
}

function useSessionState() {
  const [session, setSession] = React.useState(null);
  const [loading, setLoading] = React.useState(true);

  const refresh = React.useCallback(() => {
    setLoading(true);
    fetch('/api/auth/session', { credentials: 'include' })
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

function getInitialTheme() {
  if (typeof window === 'undefined') return 'light';
  const savedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
  return savedTheme === 'dark' ? 'dark' : 'light';
}

function useThemeState() {
  const [theme, setTheme] = React.useState(getInitialTheme);

  React.useEffect(() => {
    const isDark = theme === 'dark';
    document.body.classList.toggle('dark', isDark);
    document.documentElement.setAttribute('data-bs-theme', theme);
    document.body.setAttribute('data-bs-theme', theme);
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);

    if (window.Chart?.defaults) {
      window.Chart.defaults.color = isDark ? '#cbd5e1' : '#6c757d';
      window.Chart.defaults.borderColor = isDark ? 'rgba(148, 163, 184, 0.18)' : 'rgba(0, 0, 0, 0.1)';
      if (window.Chart.defaults.plugins?.legend?.labels) {
        window.Chart.defaults.plugins.legend.labels.color = isDark ? '#e2e8f0' : '#495057';
      }
      if (window.Chart.defaults.plugins?.tooltip) {
        window.Chart.defaults.plugins.tooltip.backgroundColor = isDark ? 'rgba(15, 23, 42, 0.96)' : 'rgba(33, 37, 41, 0.92)';
        window.Chart.defaults.plugins.tooltip.titleColor = '#f8fafc';
        window.Chart.defaults.plugins.tooltip.bodyColor = '#e2e8f0';
        window.Chart.defaults.plugins.tooltip.borderColor = isDark ? 'rgba(148, 163, 184, 0.18)' : 'rgba(255, 255, 255, 0.08)';
        window.Chart.defaults.plugins.tooltip.borderWidth = 1;
      }
    }
  }, [theme]);

  const toggleTheme = React.useCallback(() => {
    setTheme((current) => (current === 'dark' ? 'light' : 'dark'));
  }, []);

  return { theme, toggleTheme };
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
  const themeState = useThemeState();
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
        const registrationReady = Boolean(
          payload?.ready_to_submit
            || payload?.has_pending_registration
            || payload?.is_in_progress
            || payload?.web_session_active
        );
        if (registrationReady && location.pathname !== '/register') {
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
    <ThemeContext.Provider value={themeState}>
      <SessionContext.Provider value={sessionState}>
        <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/unauthorized" element={<UnauthorizedPage />} />
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
            <Route path="entry-logs" element={<EntryExitLogsPage />} />
            <Route path="program-monthly-visits" element={<ProgramMonthlyVisitsPage />} />
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
    </ThemeContext.Provider>
  );
}
