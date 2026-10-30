import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: './tests', fullyParallel: false, workers: 1, timeout: 30000,
  use: { baseURL: 'http://127.0.0.1:5173', viewport: { width: 1440, height: 960 },
    launchOptions: { executablePath: process.env.REPOPILOT_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' } },
  webServer: { command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1', url: 'http://127.0.0.1:5173', reuseExistingServer: !process.env.CI },
})
