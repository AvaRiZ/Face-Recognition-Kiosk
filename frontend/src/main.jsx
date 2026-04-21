import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App.jsx';

const CSS_ASSETS = [
  '/static/assets/css/bootstrap.min.css',
  '/static/assets/css/bootstrap-overrides.css',
  '/static/assets/css/bootstrap-icons.css',
  '/static/assets/css/style.css'
];

const SCRIPT_ASSETS = [
  '/static/assets/js/bootstrap.bundle.min.js',
  '/static/assets/js/main.js',
  '/static/assets/js/chart.min.js',
  '/static/assets/js/sweetalert2@11.js'
];

function ensureStylesheet(href) {
  const existing = document.querySelector(`link[rel="stylesheet"][href="${href}"]`);
  if (existing) {
    return Promise.resolve();
  }

  return new Promise((resolve, reject) => {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = href;
    link.onload = () => resolve();
    link.onerror = () => reject(new Error(`Failed to load stylesheet: ${href}`));
    document.head.appendChild(link);
  });
}

function ensureScript(src) {
  const existing = document.querySelector(`script[src="${src}"]`);
  if (existing) {
    return existing.dataset.loaded === 'true'
      ? Promise.resolve()
      : new Promise((resolve, reject) => {
          existing.addEventListener('load', () => resolve(), { once: true });
          existing.addEventListener(
            'error',
            () => reject(new Error(`Failed to load script: ${src}`)),
            { once: true }
          );
        });
  }

  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = src;
    script.async = false;
    script.onload = () => {
      script.dataset.loaded = 'true';
      resolve();
    };
    script.onerror = () => reject(new Error(`Failed to load script: ${src}`));
    document.body.appendChild(script);
  });
}

async function bootstrapAssets() {
  await Promise.all(CSS_ASSETS.map((href) => ensureStylesheet(href)));

  for (const src of SCRIPT_ASSETS) {
    await ensureScript(src);
  }
}

function renderApp() {
  const rootEl = document.getElementById('root');
  if (!rootEl) {
    return;
  }

  createRoot(rootEl).render(
    <React.StrictMode>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </React.StrictMode>
  );
}

bootstrapAssets()
  .catch((error) => {
    console.error('Static asset bootstrap failed:', error);
  })
  .finally(() => {
    renderApp();
  });
