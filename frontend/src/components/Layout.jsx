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
        <div className="copyright">
          <strong>Batangas State University The National Engineering University</strong>
        </div>
      </footer>
    </div>
  );
}
