import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base: './' 讓打包後的檔案用相對路徑，後端直接當靜態檔提供
export default defineConfig({
  base: './',
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8765' } },
  build: { outDir: 'dist', emptyOutDir: true, chunkSizeWarningLimit: 1000 },
})
