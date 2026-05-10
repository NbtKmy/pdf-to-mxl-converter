import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Flask serves the built SPA from /edit/<job_id>, so assets must be resolved
// against the document path rather than the site root. base: './' keeps every
// generated <script src=...> / <link href=...> relative.
export default defineConfig({
  base: './',
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8888',
    },
  },
})
