// Capture actual desktop preview UI; no model, platform, or runtime operations.
const path = require('node:path')
const fs = require('node:fs')
const { chromium } = require('@playwright/test')
async function main() {
  const desktop = path.resolve(__dirname, '..')
  const output = path.resolve(process.argv[2] || path.join(desktop, '../.local/desktop-preview'))
  fs.mkdirSync(output, { recursive: true })
  const { createServer } = await import('vite')
  const server = await createServer({ root: desktop, configFile: path.join(desktop, 'vite.config.ts'), server: { host: '127.0.0.1', port: 0, strictPort: false } })
  await server.listen()
  const origin = 'http://127.0.0.1:' + server.httpServer.address().port
  let browser
  try {
    browser = await chromium.launch({ executablePath: process.env.REPOPILOT_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' })
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 }, deviceScaleFactor: 1 })
    const errors = []; page.on('pageerror', error => errors.push(error.message))
    const frames = []
    for (const [mode, file, duration] of [['running', 'execution.png', 6000], ['waiting', 'approval.png', 5000]]) {
      await page.goto(origin + '/?demo=' + mode)
      await page.getByRole('heading', { level: 1 }).waitFor()
      await page.evaluate(() => document.fonts.ready)
      await page.screenshot({ path: path.join(output, file) })
      frames.push({ file, duration_ms: duration })
    }
    await page.goto(origin + '/?demo=running')
    await page.getByRole('button', { name: '桌面设置与连接', exact: true }).click()
    await page.getByRole('button', { name: 'GitHub 机器人', exact: true }).click()
    await page.getByText('App 设置页的数字 ID，不是 Client ID。', { exact: true }).waitFor()
    await page.evaluate(() => document.fonts.ready)
    await page.screenshot({ path: path.join(output, 'connections.png') })
    frames.push({ file: 'connections.png', duration_ms: 6000 })
    if (errors.length) throw new Error(errors.join('\n'))
    fs.writeFileSync(path.join(output, 'frames.json'), JSON.stringify({ crop: [0, 0, 1440, 960], trim: false, frames }, null, 2))
    console.log(JSON.stringify({ output, frames: frames.length, mode: 'explicit demo data' }))
  } finally { if (browser) await browser.close(); await server.close() }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
