import { test, expect } from '@playwright/test'
import { projectActions } from '../src/events'

test('parallel actions pair by id; ambiguous historical results are not guessed', () => {
  const actions = projectActions([
    { id: '1', action_id: 'a', event: 'tool_started', name: 'read_file', phase: 'implement', time: '2026-10-30T00:00:00Z', args: { path: 'a.py' } },
    { id: '2', action_id: 'b', event: 'tool_started', name: 'read_file', phase: 'implement', time: '2026-10-30T00:00:00Z', args: { path: 'b.py' } },
    { id: '3', action_id: 'b', event: 'tool_finished', name: 'read_file', time: '2026-10-30T00:00:01Z', result: { ok: true, content: 'B' } },
    { id: '4', action_id: 'a', event: 'tool_finished', name: 'read_file', time: '2026-10-30T00:00:02Z', result: { ok: true, content: 'A' } },
  ])
  expect(actions.map(a => a.output)).toEqual(['A', 'B'])
  const legacy = projectActions([{ id: '1', event: 'tool_started', name: 'read_file', time: '' }, { id: '2', event: 'tool_started', name: 'read_file', time: '' }, { id: '3', event: 'tool_finished', name: 'read_file', time: '', result: { content: 'ambiguous' } }])
  expect(legacy.every(a => a.status === 'running' && a.output === '')).toBe(true)
})

for (const width of [1440, 1280]) for (const mode of ['running', 'waiting', 'change']) {
  test(`${mode} design and asset geometry at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 960 })
    const errors: string[] = []; page.on('pageerror', e => errors.push(e.message))
    await page.goto('/?demo=' + mode); await page.getByRole('heading', { level: 1 }).waitFor(); await page.evaluate(() => document.fonts.ready)
    await expect(page.getByText('设计预览 · 示例数据', { exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    const broken = await page.locator('img').evaluateAll(images => images.filter(img => !(img as HTMLImageElement).complete || !(img as HTMLImageElement).naturalWidth).map(img => img.getAttribute('src')))
    expect(broken).toEqual([])
    const assets = await page.locator('img.icon').evaluateAll(images => images.map(img => ({ natural: [(img as HTMLImageElement).naturalWidth, (img as HTMLImageElement).naturalHeight], width: img.getBoundingClientRect().width, height: img.getBoundingClientRect().height })))
    expect(assets.every(a => a.width > 0 && a.height > 0 && a.width <= 20 && a.height <= 20)).toBe(true)
    expect(await page.locator('h1').evaluate(n => getComputedStyle(n).fontFamily)).toContain('Noto Sans SC')
    expect(errors).toEqual([])
    await page.screenshot({ path: `test-results/${mode}-${width}.png` })
  })
}

test('logs expand, search, failure filtering and page tabs work', async ({ page }) => {
  await page.goto('/?demo=running')
  const input = page.getByRole('textbox', { name: '搜索此 Agent 的日志' })
  await input.fill('AssertionError')
  await expect(page.locator('.action')).toHaveCount(1)
  await expect(page.getByText('E   AssertionError: expected [21, 22], got [1, 2]', { exact: true })).toBeVisible()
  await input.fill(''); await page.getByLabel('筛选动作').selectOption('failed'); await expect(page.locator('.action')).toHaveCount(1)
  await page.getByRole('button', { name: /^改动/ }).click(); await expect(page.getByText('候选改动', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /^检查与审核/ }).click(); await expect(page.getByRole('heading', { name: '独立检查与审核' })).toBeVisible()
})

test('approval requires a concrete reason and preview has no runtime side effects', async ({ page }) => {
  await page.goto('/?demo=waiting')
  await page.getByRole('button', { name: '批准此候选', exact: true }).click()
  const confirm = page.getByRole('button', { name: '确认批准', exact: true })
  await expect(confirm).toBeDisabled(); await page.getByLabel('核对说明').fill('核对当前固定提交与证据')
  await expect(confirm).toBeEnabled(); await confirm.click()
  await expect(page.getByRole('status')).toContainText('未批准、发布或修改任何真实任务')
  await expect(page.getByText('需要你核对并批准发布', { exact: true })).toBeVisible()
})

test('live mode starts empty and does not silently substitute sample data', async ({ page }) => {
  await page.route('**/api/runs', route => route.fulfill({ json: { runs: [], queue: [] } }))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '还没有执行记录' })).toBeVisible()
  await expect(page.getByText('为本地批次列表添加分页', { exact: true })).toHaveCount(0)
})
