import React from 'react';
import { Outlet } from 'react-router-dom';
import Header from './Header.jsx';
import Sidebar from './Sidebar.jsx';

export default function Layout() {
  const [sidebarCollapsed, setSidebarCollapsed] = React.useState(false);

  React.useEffect(() => {
    document.body.classList.toggle('toggle-sidebar', sidebarCollapsed);
    return () => {
      document.body.classList.remove('toggle-sidebar');
    };
  }, [sidebarCollapsed]);

  function handleToggleSidebar() {
    setSidebarCollapsed((current) => !current);
  }

  return (
    <div>
      <Header onToggleSidebar={handleToggleSidebar} sidebarCollapsed={sidebarCollapsed} />
      <Sidebar />
      <main id="main" className="main">
        <Outlet />
      </main>
      <footer id="footer" className="footer">
        <div className="footer-accent" />
        <div className="footer-inner">
          <section className="footer-panel">
            <h3 className="footer-title">ATTENDANCE LOGGING SYSTEM</h3>
            <p className="footer-field"><span className="footer-label">Telephone:</span></p>
            <p className="footer-field">(043) 980 0385 local 2150</p>
            <p className="footer-field"><span className="footer-label">Email:</span></p>
            <p className="footer-field">library.alangilan@g.batstate-u.edu.ph</p>
          </section>
          <section className="footer-panel">
            <h3 className="footer-title">CAMPUS DIRECTORY</h3>
            <p className="footer-field"><span className="footer-label">Trunklines:</span></p>
            <p className="footer-field">(043) 425-0139</p>
            <p className="footer-field">(043) 425-0143</p>
          </section>
          <div className="footer-meta">
            <div className="footer-logo-group">
              <div className="footer-logo-box">
                <img src="/static/assets/img/bsu-neu-logo.png" alt="BatState Logo" />
              </div>
            </div>
            <div className="copyright">
              <strong>© 2026 Batangas State University The National Engineering University</strong>
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
