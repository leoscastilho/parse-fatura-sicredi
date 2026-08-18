import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    // Em dev o Vite fala com o FastAPI direto; em produção o nginx faz o proxy,
    // então o front sempre chama caminhos relativos (/api/...) e nunca precisa
    // saber o host do backend.
    proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true, rewrite: p => p.replace(/^\/api/, '') } },
  },
  build: { outDir: 'dist', sourcemap: false },
})
