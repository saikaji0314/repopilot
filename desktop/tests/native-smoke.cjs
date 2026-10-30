// Run after `pnpm build`, with REPOPILOT_RUNTIME_DIR pointing to an isolated fixture.
const { _electron, expect } = require('@playwright/test')
const path = require('node:path')
const fs = require('node:fs')
async function main() {
  if (!process.env.REPOPILOT_RUNTIME_DIR) throw new Error('An isolated runtime fixture is required')
  const env = { ...process.env, REPOPILOT_PYTHON: path.resolve(__dirname, '../../.venv/Scripts/python.exe') }
  const profile = path.join(process.env.REPOPILOT_RUNTIME_DIR, 'native-desktop-profile'); fs.mkdirSync(profile, { recursive: true })
  env.REPOPILOT_DESKTOP_USER_DATA = profile; env.REPOPILOT_DESKTOP_HOST_CONFIG = path.join(profile, 'host.json')
  delete env.ELECTRON_RUN_AS_NODE
  const packaged = process.env.REPOPILOT_PACKAGED_EXE
  if (packaged) { env.PATH = path.join(process.env.SystemRoot || 'C:/Windows', 'System32') + path.delimiter + (process.env.REPOPILOT_TEST_GIT_DIR || path.dirname(require('node:child_process').execFileSync('where.exe', ['git'], { encoding: 'utf8' }).split(/\r?\n/)[0])); for (const name of ['REPOPILOT_PYTHON', 'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'CONDA_PREFIX']) delete env[name] }
  const app = await _electron.launch({ executablePath: packaged || require('electron'), args: packaged ? [] : [path.resolve(__dirname, '..')], cwd: profile, env })
  try {
    const page = await app.firstWindow()
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 20000 })
    await expect(page.getByText('需要你核对并批准发布', { exact: true })).toBeVisible()
    const boundary = await page.evaluate(async () => ({ noNode: typeof window.require === 'undefined',
      unauthenticatedStatus: (await fetch('/api/runs')).status,
      runtimeReady: !!window.repopilot && !!(await window.repopilot.bootstrap()).token }))
    if (!boundary.noNode || boundary.unauthenticatedStatus !== 401 || !boundary.runtimeReady) throw new Error('Electron security boundary failed')
    await page.getByRole('button', { name: '关闭到托盘' }).click()
    const hidden = await app.evaluate(({ BrowserWindow }) => !BrowserWindow.getAllWindows()[0].isVisible())
    if (!hidden) throw new Error('Close did not hide to tray')
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].show())
    await expect(page.getByText('需要你核对并批准发布', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: /^改动/ }).click()
    await expect(page.getByText('候选改动', { exact: true })).toBeVisible()
    await expect(page.locator('.file-list')).toContainText('app.py')
    await page.getByRole('button', { name: '执行', exact: true }).click()
    await page.getByRole('button', { name: 'Reviewers', exact: false }).click()
    await page.getByRole('button', { name: '批准此候选', exact: true }).click()
    await page.getByLabel('核对说明').fill('Deterministic native smoke: fixed SHA and evidence checked')
    await page.getByRole('button', { name: '确认批准', exact: true }).click()
    await expect(page.getByText('已批准发布', { exact: true })).toBeVisible({ timeout: 10000 })
    await page.screenshot({ path: path.resolve(__dirname, '../../.local/desktop-tests/native-approved.png') })
    console.log(JSON.stringify({ native: 'passed', closeToTray: hidden, reopenSameRun: true, fixedCandidateDecision: true, ...boundary }))
  } finally { await app.close() }
}
main().catch(e => { console.error(e); process.exitCode = 1 })
