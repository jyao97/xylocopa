import fs from 'node:fs'
import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

// Load self-signed certs for HTTPS (needed for microphone access over LAN)
const certsDir = path.resolve(__dirname, '../certs')
const httpsConfig = fs.existsSync(path.join(certsDir, 'selfsigned.key'))
  ? { key: fs.readFileSync(path.join(certsDir, 'selfsigned.key')), cert: fs.readFileSync(path.join(certsDir, 'selfsigned.crt')) }
  : undefined

// PWA manifest icons must be reachable from Google's WebAPK minting server
// (public internet) — self-hosted Tailscale/LAN URLs aren't, so Android
// falls back to "host first-letter" placeholder. jsDelivr mirrors GitHub
// tagged releases on a public CDN; pin to the current version so each
// release immutably points at its own icons.
const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, 'package.json'), 'utf8'))
const ICON_BASE = `https://cdn.jsdelivr.net/gh/jyao97/xylocopa@v${pkg.version}/frontend/public`

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    // Inject __APP_VERSION__ placeholder in index.html with the current
    // package.json version (used by the apple-touch-icon CDN URL).
    {
      name: 'inject-app-version',
      transformIndexHtml(html) {
        return html.replace(/__APP_VERSION__/g, pkg.version)
      },
    },
    react(),
    tailwindcss(),
    VitePWA({
      // 'prompt' (vs 'autoUpdate'): new SW installs in background but does
      // NOT auto-reload the page. Combined with clientsClaim removed below,
      // existing tabs keep using the old SW until manually refreshed —
      // avoiding the iOS controllerchange→reload loop.
      registerType: 'prompt',
      devOptions: { enabled: true },
      workbox: {
        // skipWaiting kept: new SW activates without waiting for old SW to
        // be released. clientsClaim removed: new SW will only control NEW
        // navigations, not steal currently-loaded pages.
        skipWaiting: true,
        globPatterns: ['**/*.{js,css,html,ico,png,svg,woff2}'],
        // Import existing push notification handler into generated SW
        importScripts: ['/push-handler.js'],
        runtimeCaching: [
          // Fluent UI emoji SVGs from jsdelivr — immutable assets, cache forever
          {
            urlPattern: /^https:\/\/cdn\.jsdelivr\.net\/gh\/microsoft\/fluentui-emoji.*\.svg$/,
            handler: 'CacheFirst',
            options: {
              cacheName: 'fluent-emoji-cache',
              expiration: { maxEntries: 300, maxAgeSeconds: 365 * 24 * 60 * 60 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
          // Thumbnails — cached aggressively (small files, rarely change)
          {
            urlPattern: /\.thumb\.jpg$/,
            handler: 'CacheFirst',
            options: {
              cacheName: 'thumbnail-cache',
              expiration: { maxEntries: 200, maxAgeSeconds: 7 * 24 * 60 * 60 },
            },
          },
          // Image thumbnails — cached aggressively (small JPEG, rarely change)
          {
            urlPattern: /\/api\/thumbs\//,
            handler: 'CacheFirst',
            options: {
              cacheName: 'thumbnail-cache',
              expiration: { maxEntries: 500, maxAgeSeconds: 7 * 24 * 60 * 60 },
            },
          },
          // Files/uploads must bypass SW cache — Safari requires intact
          // HTTP Range responses for <video> playback and Workbox caching
          // strategies strip/corrupt the 206 + Content-Range semantics.
          {
            urlPattern: /\/api\/(?:files|uploads)\//,
            handler: 'NetworkOnly',
          },
          // Polling endpoints refresh every 3-10s — caching adds SW
          // overhead with zero benefit.  NetworkOnly bypasses cache entirely.
          {
            urlPattern: /^.*\/api\/.*/,
            handler: 'NetworkOnly',
          },
        ],
      },
      manifest: {
        name: 'Xylocopa',
        short_name: 'Xylocopa',
        description: 'Multi-agent Claude Code dashboard',
        theme_color: '#06b6d4',
        background_color: '#0a0a0a',
        display: 'standalone',
        orientation: 'portrait',
        start_url: '/',
        icons: [
          { src: `${ICON_BASE}/icon-192.png`, sizes: '192x192', type: 'image/png' },
          { src: `${ICON_BASE}/icon-512.png`, sizes: '512x512', type: 'image/png' },
          { src: `${ICON_BASE}/icon-mask.png`, sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
    }),
  ],
  server: {
    https: httpsConfig,
    host: '0.0.0.0',
    port: 3000,
    hmr: {
      // Explicit HMR config stabilises the WebSocket on mobile with self-signed certs
      protocol: httpsConfig ? 'wss' : 'ws',
      port: 3000,
    },
    proxy: {
      '/api': 'http://localhost:8080',
      '/ws': { target: 'ws://localhost:8080', ws: true },
    },
  },
  preview: {
    // Prod-build serving mode — no HMR, no ws ping reload loop.
    // Mirrors `server` but without the HMR block.
    https: httpsConfig,
    host: '0.0.0.0',
    port: 3000,
    proxy: {
      '/api': 'http://localhost:8080',
      '/ws': { target: 'ws://localhost:8080', ws: true },
    },
  },
  optimizeDeps: {
    // Work around TailwindCSS v4 HMR cache invalidation bug
    exclude: ['@tailwindcss/vite'],
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test-setup.js',
  },
})
