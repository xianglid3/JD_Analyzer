import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite' 

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    environment: 'jsdom',
    setupFiles: './tests/helpers/setup.js',
    include: ['tests/**/*.test.{js,jsx}'],
  },
  server: {
    proxy: {
      '/api': 'http://localhost:5001',
    },
  },
})
