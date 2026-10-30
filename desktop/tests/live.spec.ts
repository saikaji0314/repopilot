import { test, expect } from '@playwright/test'
import { spawn, spawnSync, type ChildProcess } from 'node:child_process'
import { mkdtempSync, mkdirSync, appendFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { randomBytes } from 'node:crypto'

test('real loopback service replays persisted events and exposes disconnection', async ({ page }) => {
  const root = path.resolve('..'); const python = path.join(root, '.venv/Scripts/python.exe')
  mkdirSync(path.join(root, '.local/desktop-tests'), { recursive: true })
  const output = mkdtempSync(path.join(root, '.local/desktop-tests/live-'))
  const state = { run_id: 'live-fixture', task: { id: 'live-fixture', repository: 'sample' }, status: 'running', phase: 'implement', control: {}, human_requests: [], requirements_revision: 3 }
  const seeded = spawnSync(python, ['-c', "import json,sys;from pathlib import Path;from repopilot.storage.jobs import JobStore;JobStore(Path(sys.argv[1])).create(json.load(sys.stdin))", output], { cwd: root, input: JSON.stringify(state), encoding: 'utf8', windowsHide: true })
  expect(seeded.status).toBe(0)
  const directory = path.join(output, 'runs/live-fixture'); mkdirSync(directory, { recursive: true })
  const events = path.join(directory, 'events.jsonl')
  writeFileSync(events, JSON.stringify({ event: 'tool_started', action_id: 'check-1', name: 'run_registered_check', args: { name: 'unit' }, phase: 'implement', time: new Date().toISOString() }) + '\n')
  const token = randomBytes(32).toString('hex')
  let backend: ChildProcess | undefined
  try {
    const origin = await new Promise<string>((resolve, reject) => {
      backend = spawn(python, ['-m', 'repopilot.desktop', '--output', output, '--ui', path.resolve('out')], { cwd: root, windowsHide: true, env: { ...process.env, REPOPILOT_DESKTOP_TOKEN: token }, stdio: ['ignore', 'pipe', 'pipe'] })
      let pending = ''; const timer = setTimeout(() => reject(new Error('API fixture startup timeout')), 10000)
      backend.stdout!.on('data', chunk => { pending += chunk; if (!pending.includes('\n')) return; try { const ready = JSON.parse(pending.split('\n')[0]); if (ready.desktop_ready) { clearTimeout(timer); resolve('http://127.0.0.1:' + ready.port) } } catch {} })
      backend.on('error', reject); backend.on('exit', code => { clearTimeout(timer); if (code) reject(new Error('API fixture exited: ' + code)) })
    })
    await page.addInitScript(({ token }) => { window.repopilot = { bootstrap: async () => ({ token, runtime: 'isolated-test' }), window: () => {}, openExternal: async () => {} } }, { token })
    await page.goto(origin)
    await expect(page.getByRole('heading', { level: 1, name: 'live-fixture' })).toBeVisible()
    await expect(page.locator('.action')).toHaveCount(1)
    await expect(page.getByText('设计预览 · 示例数据', { exact: true })).toHaveCount(0)
    appendFileSync(events, JSON.stringify({ event: 'command_output', phase: 'implement', action_id: 'check-1', output: 'first live output\n', time: new Date().toISOString() }) + '\n')
    await expect(page.getByText('first live output', { exact: true })).toBeVisible({ timeout: 5000 })
    appendFileSync(events, JSON.stringify({ event: 'tool_finished', phase: 'implement', name: 'run_registered_check', action_id: 'check-1', result: { ok: true, exit_code: 0, output: 'first live output\nfinished' }, time: new Date().toISOString() }) + '\n')
    await expect(page.getByText('exit 0', { exact: true })).toBeVisible({ timeout: 5000 })
    await page.reload(); await expect(page.locator('.action')).toHaveCount(1)
    backend!.kill(); await expect(page.getByRole('alert')).toContainText('连接中断', { timeout: 15000 })
    await expect(page.getByRole('heading', { level: 1, name: 'live-fixture' })).toBeVisible()
    await expect(page.getByRole('button', { name: '请求暂停', exact: true })).toBeDisabled()
  } finally { backend?.kill() }
})
