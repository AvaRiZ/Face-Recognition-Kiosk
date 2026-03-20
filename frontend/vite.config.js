import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig(({ command }) => ({
  plugins: [react()],
  root: path.resolve(__dirname),
  base: command === 'serve' ? '/' : '/static/react/',
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:5000',
      '/static': 'http://127.0.0.1:5000',
      '/video_feed': 'http://127.0.0.1:5000',
      '/check_status': 'http://127.0.0.1:5000',
      '/stop_feed': 'http://127.0.0.1:5000',
      '/register': 'http://127.0.0.1:5000',
      '/login': 'http://127.0.0.1:5000',
      '/logout': 'http://127.0.0.1:5000',
      '/profile': 'http://127.0.0.1:5000'
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
