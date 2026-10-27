import { readFileSync } from 'node:fs'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

/** In mock mode, serve / emit MSW's service worker without committing it to public/. */
function mswWorker(enabled: boolean): Plugin {
  const path = fileURLToPath(new URL('./node_modules/msw/lib/mockServiceWorker.js', import.meta.url))
  return {
    name: 'voti-msw-worker',
    apply: () => enabled,
    configureServer(server) {
      server.middlewares.use('/mockServiceWorker.js', (_req, res) => {
        res.setHeader('Content-Type', 'text/javascript')
        res.end(readFileSync(path))
      })
    },
    configurePreviewServer(server) {
      server.middlewares.use('/mockServiceWorker.js', (_req, res) => {
        res.setHeader('Content-Type', 'text/javascript')
        res.end(readFileSync(path))
      })
    },
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: 'mockServiceWorker.js', source: readFileSync(path, 'utf8') })
    },
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const mock = env.VITE_MOCK === '1'
  const apiTarget = env.VOTI_API ?? 'http://127.0.0.1:8000'
  return {
    plugins: [react(), tailwindcss(), mswWorker(mock)],
    resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
    server: {
      host: '127.0.0.1',
      port: 5173,
      proxy: mock ? undefined : { '/api': { target: apiTarget, changeOrigin: false, xfwd: true } },
    },
    preview: { host: '127.0.0.1' },
    build: {
      target: 'es2022',
      sourcemap: false,
      chunkSizeWarningLimit: 900,
      rollupOptions: {
        output: {
          manualChunks: {
            react: ['react', 'react-dom', 'react-router'],
            ui: ['radix-ui', 'sonner', '@tanstack/react-query'],
          },
        },
      },
    },
  }
})
