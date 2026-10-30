import { test, expect, type Page } from '@playwright/test'
import { demoDetail } from '../src/demo'

const run = demoDetail('running')
const settings = { revision: 0, runtime: 'D:/isolated/runtime', secure_storage: 'windows-current-user-dpapi',
  github: { source: 'environment', app_id: '', installation_id: '', owner: '', repository: '', private_key_configured: false, user_token_configured: false },
  model: { source: 'environment', provider: 'deepseek', base_url: 'https://api.deepseek.com', model: 'deepseek-flash', max_run_cost_cny: 2, budget_cny: 200, input_cny_per_million: 3, output_cny_per_million: 12, api_key_configured: false } }
async function fixture(page: Page) {
  await page.route('**/api/runs', route => route.fulfill({ json: { runs: [run], queue: [] } }))
  await page.route('**/api/runs/*/events', route => route.fulfill({ contentType: 'text/event-stream', body: `event: update\ndata: ${JSON.stringify({ snapshot: run, events: [], cursor: 0 })}\n\n` }))
}

test('both split panes resize by pointer and keyboard and remember collapse', async ({ page }) => {
  await page.goto('/?demo=running')
  const handle = page.getByRole('separator', { name: '调整导航栏宽度' })
  await handle.focus(); await page.keyboard.press('ArrowRight')
  await expect(page.locator('.sidebar')).toHaveCSS('width', '224px')
  const box = (await handle.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + 100); await page.mouse.down(); await page.mouse.move(box.x + box.width / 2 + 64, box.y + 100, { steps: 8 }); await page.mouse.up()
  await expect(page.locator('.sidebar')).toHaveCSS('width', '288px')
  const agent = page.getByRole('separator', { name: '调整 Agent 列表宽度' })
  await agent.focus(); await page.keyboard.press('Shift+ArrowRight')
  await expect(page.locator('.agents')).toHaveCSS('width', '256px')
  await page.getByRole('button', { name: '隐藏 Agent 列表' }).click()
  await page.keyboard.press('Control+b')
  await expect(page.getByRole('complementary', { name: '应用导航' })).toHaveCount(0)
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('repopilot.panels') || '{}').sidebarHidden)).toBe(true)
  await page.reload()
  await expect(page.getByRole('button', { name: '显示导航栏' })).toBeVisible()
  await page.getByRole('button', { name: '显示导航栏' }).click(); await page.getByRole('button', { name: '显示 Agent 列表' }).click()
  await expect(page.locator('.sidebar')).toHaveCSS('width', '288px'); await expect(page.locator('.agents')).toHaveCSS('width', '256px')
  await page.getByRole('separator', { name: '调整导航栏宽度' }).dblclick()
  await expect(page.locator('.sidebar')).toHaveCSS('width', '216px')
  await page.keyboard.press('Control+b'); await page.keyboard.press('Control+k')
  await expect(page.getByLabel('搜索任务或仓库')).toBeFocused()
})

test('settings guides app setup and preserves blank credentials in a safe payload', async ({ page }) => {
  await fixture(page)
  let saved: Record<string, any> | undefined
  await page.route('**/api/settings', async route => {
    if (route.request().method() === 'POST') { saved = route.request().postDataJSON(); await route.fulfill({ json: { ...settings, ...saved, revision: 1, github: { ...saved!.github, private_key_configured: true, user_token_configured: false }, model: { ...saved!.model, api_key_configured: false } } }) }
    else await route.fulfill({ json: settings })
  })
  await page.route('**/api/settings/check-github', route => route.fulfill({ json: { login: 'test-bot[bot]', repository: 'owner/repo' } }))
  await page.goto('/'); await page.getByRole('button', { name: '桌面设置与连接', exact: true }).click()
  await expect(page.getByRole('button', { name: '关闭设置', exact: true })).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  await expect(page.getByRole('button', { name: '保存设置', exact: true })).toBeFocused()
  await page.getByRole('button', { name: 'GitHub 机器人', exact: true }).click()
  await page.getByLabel('配置来源').selectOption('desktop')
  await expect(page.getByText('App 设置页的数字 ID，不是 Client ID。')).toBeVisible()
  await page.getByLabel('App ID', { exact: true }).fill('1'); await page.getByLabel('Installation ID', { exact: true }).fill('2')
  await page.getByLabel('负责人 GitHub 用户名').fill('owner'); await page.getByLabel('连接检查仓库').fill('owner/repo')
  await page.locator('input[type=file]').setInputFiles({ name: 'synthetic.pem', mimeType: 'text/plain', buffer: Buffer.from('synthetic-ui-fixture-key') })
  await page.screenshot({ path: 'test-results/settings-github.png' })
  await page.getByRole('button', { name: '保存并检查 GitHub 连接' }).click()
  await expect(page.getByRole('status')).toContainText('连接成功：test-bot[bot]')
  expect(saved?.private_key).toBe('synthetic-ui-fixture-key'); expect(saved?.user_token).toBeNull(); expect(saved?.clear_private_key).toBe(false)
  expect(saved?.github.private_key_configured).toBeUndefined()
  await page.getByRole('button', { name: '模型与预算', exact: true }).click()
  await expect(page.getByLabel('模型 API Key', { exact: true })).toHaveAttribute('type', 'password')
  await page.screenshot({ path: 'test-results/settings-model.png' })
})

test('ordinary comment send keeps identity and reconciles an interrupted draft after reload', async ({ page }) => {
  await fixture(page)
  let firstId = ''; let posts = 0
  await page.route('**/api/runs/*/comments', async route => {
    if (route.request().method() === 'GET') { await route.fulfill({ json: { comments: [{ id: 1, author: 'owner', body: '<img src=x onerror=alert(1)>', created_at: '2026-10-30T00:00:00Z', url: run.url + '#issuecomment-1' }], login: 'repo-app[bot]', owner: 'owner', default_sender: 'bot', user_token_configured: true } }); return }
    const body = route.request().postDataJSON(); posts++
    if (posts === 1) { firstId = body.client_id; await route.fulfill({ status: 409, json: { detail: 'comment_delivery_uncertain_retry_same_draft' } }); return }
    expect(body.client_id).toBe(firstId); expect(body.sender).toBe('bot'); expect(body.body).toBe('这是一条普通讨论')
    await route.fulfill({ json: { status: 'sent', reused: true, comment: { id: 2, author: 'repo-app[bot]', body: body.body, created_at: '2026-10-30T00:00:00Z', url: run.url + '#issuecomment-2' } } })
  })
  await page.goto('/'); await page.getByRole('button', { name: 'Issue 讨论', exact: true }).click()
  await expect(page.getByText('<img src=x onerror=alert(1)>', { exact: true })).toBeVisible()
  await expect(page.locator('.issue-comment img')).toHaveCount(0)
  await page.getByLabel('普通评论内容').fill('这是一条普通讨论'); await page.getByRole('button', { name: '以机器人身份发送' }).click()
  await expect(page.locator('.discussion-panel').getByRole('alert')).toContainText('发送结果尚不确定')
  await expect(page.getByLabel('普通评论内容')).toBeDisabled()
  await page.reload(); await page.getByRole('button', { name: 'Issue 讨论', exact: true }).click()
  await expect(page.getByLabel('普通评论内容')).toHaveValue('这是一条普通讨论')
  await page.getByRole('button', { name: '核对同一条评论' }).click()
  await expect(page.getByRole('status')).toContainText('未重复发送')
  await expect(page.getByLabel('普通评论内容')).toHaveValue('')
  expect(posts).toBe(2) // second request is reconciliation, not a second remote POST
  await page.screenshot({ path: 'test-results/issue-discussion.png' })
})
