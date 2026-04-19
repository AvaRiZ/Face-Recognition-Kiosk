import fs from 'fs';
import path from 'path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

function serveFlaskStatic() {
  const staticRoot = path.resolve(__dirname, '..', 'static');

  return {
    name: 'serve-flask-static',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const reqUrl = req.url ? new URL(req.url, 'http://localhost') : null;
        const pathname = reqUrl?.pathname ?? '';

        if (!pathname.startsWith('/static/')) {
          next();
          return;
        }

        const relativePath = pathname.slice('/static/'.length);
        const filePath = path.resolve(staticRoot, relativePath);

        if (!filePath.startsWith(staticRoot + path.sep) && filePath !== staticRoot) {
          res.statusCode = 403;
          res.end('Forbidden');
          return;
        }

        if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
          next();
          return;
        }

        const ext = path.extname(filePath).toLowerCase();
        const contentType =
          {
            '.css': 'text/css; charset=utf-8',
            '.js': 'application/javascript; charset=utf-8',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.svg': 'image/svg+xml',
            '.ico': 'image/x-icon',
            '.woff': 'font/woff',
            '.woff2': 'font/woff2',
            '.ttf': 'font/ttf',
            '.eot': 'application/vnd.ms-fontobject',
            '.map': 'application/json; charset=utf-8'
          }[ext] || 'application/octet-stream';

        res.setHeader('Content-Type', contentType);
        fs.createReadStream(filePath).pipe(res);
      });
    }
  };
}

export default defineConfig(({ command }) => ({
  plugins: [react(), serveFlaskStatic()],
  root: path.resolve(__dirname),
  base: command === 'serve' ? '/' : '/static/react/',
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:5000',
      '/socket.io': {
        target: 'http://127.0.0.1:5000',
        ws: true
      },
      '/register': {
        target: 'http://127.0.0.1:5000',
        bypass(req) {
          if (req.method === 'GET' || req.method === 'HEAD') {
            return '/index.html';
          }
        }
      }
    }
  },
  build: {
    outDir: path.resolve(__dirname, '..', 'static', 'react'),
    emptyOutDir: true,
    rollupOptions: {
      input: path.resolve(__dirname, 'index.html')
    }
  }
}));
