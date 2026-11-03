import path from 'node:path'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const publicUrl = loadEnv(mode, process.cwd(), 'VITE_').VITE_PUBLIC_URL?.trim()
  const origin = publicUrl ? new URL(publicUrl) : null
  if (origin && (!['https:', 'http:'].includes(origin.protocol) || origin.username || origin.password
    || origin.pathname !== '/' || origin.search || origin.hash)) {
    throw new Error('VITE_PUBLIC_URL must be a public HTTP(S) origin without credentials, path, query or hash')
  }
  return {
  plugins: [react(), tailwindcss(), {
    name: 'telos-public-metadata',
    transformIndexHtml(html) {
      if (!origin) return html
      return {
        html: html.replaceAll('content="/brand/share-cover.png"', `content="${origin.origin}/brand/share-cover.png"`),
        tags: [
          { tag: 'link', attrs: { rel: 'canonical', href: `${origin.origin}/` }, injectTo: 'head' },
          { tag: 'meta', attrs: { property: 'og:url', content: `${origin.origin}/` }, injectTo: 'head' },
        ],
      }
    },
  }],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  }
})
