import React from 'react';

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

export function useSessionState() {
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

export function useThemeState() {
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

export function AppProviders({ children }) {
  const sessionState = useSessionState();
  const themeState = useThemeState();

  return (
    <ThemeContext.Provider value={themeState}>
      <SessionContext.Provider value={sessionState}>
        {children}
      </SessionContext.Provider>
    </ThemeContext.Provider>
  );
}
