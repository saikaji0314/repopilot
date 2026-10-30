import { useEffect, useState, useRef } from 'react'
import { TextInput } from '@primer/react'
import { Button, openExternal } from './components'
import { demoMode, request } from './api'

export interface DesktopSettings {
  revision: number; runtime: string; secure_storage: string;
  github: { source: 'environment' | 'desktop'; app_id: string; installation_id: string; owner: string; repository: string; private_key_configured: boolean; user_token_configured: boolean };
  model: { source: 'environment' | 'desktop'; provider: string; base_url: string; model: string; max_run_cost_cny: number; budget_cny: number; input_cny_per_million: number; output_cny_per_million: number; api_key_configured: boolean }
}
const preview: DesktopSettings = { revision: 0, runtime: '设计预览', secure_storage: 'windows-current-user-dpapi', github: { source: 'desktop', app_id: '', installation_id: '', owner: '', repository: '', private_key_configured: false, user_token_configured: false }, model: { source: 'desktop', provider: 'deepseek', base_url: 'https://api.deepseek.com', model: 'deepseek-flash', max_run_cost_cny: 2, budget_cny: 200, input_cny_per_million: 3, output_cny_per_million: 12, api_key_configured: false } }
const guide = {
  app: 'https://github.com/settings/apps/new', installations: 'https://github.com/settings/installations',
  permissions: 'https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app',
  token: 'https://github.com/settings/personal-access-tokens/new', key: 'https://platform.deepseek.com/api_keys',
}
export function Settings({ close, repository = '' }: { close: () => void; repository?: string }) {
  const dialog = useRef<HTMLElement>(null)
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    dialog.current?.querySelector<HTMLButtonElement>('button')?.focus()
    const trap = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return
      const items = Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex="0"]') || []).filter(el => el.offsetParent !== null)
      if (!items.length) return
      if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1)?.focus() }
      else if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0].focus() }
    }
    document.addEventListener('keydown', trap)
    return () => { document.removeEventListener('keydown', trap); previous?.focus() }
  }, [])
  const [value, setValue] = useState<DesktopSettings | null>(null)
  const [tab, setTab] = useState('连接与本机'); const [error, setError] = useState(''); const [notice, setNotice] = useState(''); const [busy, setBusy] = useState(false)
  const [pem, setPem] = useState(''); const [pemName, setPemName] = useState(''); const [userToken, setUserToken] = useState(''); const [modelKey, setModelKey] = useState('')
  const [clearPem, setClearPem] = useState(false); const [clearUser, setClearUser] = useState(false); const [clearModel, setClearModel] = useState(false)
  useEffect(() => { let disposed = false; (demoMode ? Promise.resolve(preview) : request<DesktopSettings>('settings')).then(data => { if (!disposed) setValue({ ...data, github: { ...data.github, repository: data.github.repository || (repository.includes('/') ? repository : '') } }) }).catch(e => setError(e.message)); return () => { disposed = true } }, [repository])
  function github(key: string, input: string) { setValue(v => v && ({ ...v, github: { ...v.github, [key]: input } })) }
  function model(key: string, input: string | number) { setValue(v => v && ({ ...v, model: { ...v.model, [key]: input } })) }
  async function save(check?: 'github' | 'model') {
    if (!value) return; setError(''); setNotice(''); setBusy(true)
    try {
      const { private_key_configured, user_token_configured, ...gh } = value.github
      const { api_key_configured, ...md } = value.model
      const saved = demoMode ? value : await request<DesktopSettings>('settings', { revision: value.revision, github: gh, model: md,
        private_key: pem || null, user_token: userToken || null, model_api_key: modelKey || null,
        clear_private_key: clearPem, clear_user_token: clearUser, clear_model_api_key: clearModel })
      setValue(saved); setPem(''); setPemName(''); setUserToken(''); setModelKey(''); setClearPem(false); setClearUser(false); setClearModel(false)
      if (demoMode) { setNotice('设计预览：未保存真实配置或发起网络请求。'); return }
      if (check === 'github') { const result = await request<{ login: string; repository: string }>('settings/check-github', { repository: saved.github.repository }); setNotice(`连接成功：${result.login} · ${result.repository}。已完成只读核对，未发布评论或 PR。`) }
      else if (check === 'model') { await request('settings/check-model', {}); setNotice('模型配置格式有效。未发起推理请求，API 可用性与余额尚未验证。') }
      else setNotice('配置已加密保存在本机，现有 .env 和历史运行未被修改。')
    } catch (e) { setError(message(e instanceof Error ? e.message : String(e))) }
    finally { setBusy(false) }
  }
  const ghDisabled = value?.github.source === 'environment'; const modelDisabled = value?.model.source === 'environment'
  return <div className="dialog-backdrop" onMouseDown={e => { if (e.target === e.currentTarget && !busy) close() }}><section ref={dialog} className="dialog settings-dialog" role="dialog" aria-modal="true" aria-labelledby="settings-heading">
    <div className="panel-heading"><h2 id="settings-heading">桌面设置与连接</h2><button className="text-button" aria-label="关闭设置" onClick={close} disabled={busy}>×</button></div>
    <nav className="settings-tabs" aria-label="设置分类">{['连接与本机', 'GitHub 机器人', '模型与预算'].map(name => <button key={name} aria-current={tab === name ? 'page' : undefined} onClick={() => setTab(name)}>{name}</button>)}</nav>
    {!value ? <p role="status">{error || '正在读取本机设置…'}</p> : <div className="settings-content">
      {tab === '连接与本机' ? <><h3>本机执行服务</h3><div className="connection-card"><strong>已连接当前桌面服务</strong><p>运行目录</p><code className="runtime-path">{value.runtime}</code><Button disabled={!window.repopilot?.chooseRuntime || busy} onClick={async () => { setError(''); try { await window.repopilot?.chooseRuntime?.() } catch { setError('更换运行目录失败，原连接已保留。') } }}>选择其他运行目录</Button></div><p className="muted small">每个运行目录独立保存连接设置。切换时不搬移数据、不停止已有 CLI 任务；窗口会重新载入。</p><h3>凭证存储</h3><p>{value.secure_storage === 'windows-current-user-dpapi' ? '私钥和 API Key 使用 Windows 当前用户加密，保存后不回显原文。' : '当前系统无法使用 Windows 加密存储，不能保存凭证。'}</p><h3>窗口与分栏</h3><p>拖动导航栏和 Agent 列表边界可调宽度；双击边界恢复默认。Ctrl+B 显示或隐藏导航栏，宽度与开关会在本机记忆。</p><p>关闭主窗口后保留托盘与本机服务；从托盘可重新打开同一次执行。</p></> : tab === 'GitHub 机器人' ? <>
      <label className="field-label" htmlFor="github-source">配置来源</label><select id="github-source" value={value.github.source} onChange={e => github('source', e.target.value)}><option value="environment">使用现有环境配置</option><option value="desktop">在桌面配置自己的机器人</option></select>
      <div className="setup-step"><span className="step-number">1</span><div><h3>创建 GitHub App</h3><p>在你的 GitHub 账号或组织下创建。名称需唯一；Homepage 可填写自己的仓库地址。不需要 Webhook、回调地址或用户 OAuth。</p><div className="row"><Button onClick={() => openExternal(guide.app)}>打开创建页面</Button><button className="text-button" onClick={() => openExternal(guide.permissions)}>查看官方权限说明</button></div><p className="muted small">仓库权限：Contents、Pull requests、Issues 为读写；Checks、Commit statuses、Administration 为只读；Metadata 自动提供。只安装到需要处理的仓库。</p></div></div>
      <div className="settings-grid"><Field label="App ID" hint="App 设置页的数字 ID，不是 Client ID。" id="app-id"><TextInput id="app-id" block value={value.github.app_id} onChange={e => github('app_id', e.target.value)} disabled={ghDisabled} placeholder="例如 123456" /></Field><Field label="Installation ID" hint="安装设置网址 /settings/installations/ 后面的数字。" id="installation-id"><TextInput id="installation-id" block value={value.github.installation_id} onChange={e => github('installation_id', e.target.value)} disabled={ghDisabled} placeholder="例如 987654" /></Field></div>
      <div className="setup-step"><span className="step-number">2</span><div><h3>安装 App 并导入私钥</h3><p>在 App 页生成并下载未加密的 RSA 私钥（.pem），然后 Install App → Only select repositories。</p><div className="row"><Button onClick={() => openExternal(guide.installations)}>打开安装列表</Button><label className={'file-button ' + (ghDisabled ? 'disabled' : '')}>导入 PEM 私钥<input type="file" accept=".pem" disabled={ghDisabled} onChange={async e => { const file = e.target.files?.[0]; if (!file) return; if (file.size > 20000) { setError('私钥文件过大，请选择 GitHub 下载的 PEM 文件。'); return } setPem(await file.text()); setPemName(file.name); setClearPem(false) }} /></label></div><p className="small muted">{pemName || (value.github.private_key_configured ? '已配置私钥；原文不会回显。' : '尚未配置私钥。')}</p><label className="checkbox-row"><input type="checkbox" checked={clearPem} disabled={ghDisabled} onChange={e => setClearPem(e.target.checked)} />清除已保存的私钥</label></div></div>
      <div className="settings-grid"><Field id="github-owner" label="负责人 GitHub 用户名" hint="用于核对本人评论身份；不填写邮箱。"><TextInput id="github-owner" block value={value.github.owner} disabled={ghDisabled} onChange={e => github('owner', e.target.value)} /></Field><Field id="github-repository" label="连接检查仓库" hint="填写 owner/repository，机器人须已安装到此仓库。"><TextInput id="github-repository" block value={value.github.repository} onChange={e => github('repository', e.target.value)} placeholder="yourname/your-repository" /></Field></div>
      <details className="optional-settings"><summary>可选：以本人身份发表评论</summary><p>机器人发送的评论显示为 App[bot]。如需显示为你本人，可创建只限目标仓库的细粒度个人令牌，授予 Issues 读写权限。不会把此令牌用于机器人执行身份。</p><button className="text-button" onClick={() => openExternal(guide.token)}>打开个人令牌创建页面</button><Field id="github-user-token" label="本人评论令牌" hint={value.github.user_token_configured ? '已配置；留空保留原值。' : '可留空，继续使用机器人评论。'}><TextInput id="github-user-token" block type="password" autoComplete="new-password" value={userToken} disabled={ghDisabled} onChange={e => setUserToken(e.target.value)} /></Field><label className="checkbox-row"><input type="checkbox" disabled={ghDisabled} checked={clearUser} onChange={e => setClearUser(e.target.checked)} />清除已保存的本人令牌</label></details>
      <Button onClick={() => save('github')} disabled={busy || !value.github.repository}>保存并检查 GitHub 连接</Button>
      </> : <><label className="field-label" htmlFor="model-source">配置来源</label><select id="model-source" value={value.model.source} onChange={e => model('source', e.target.value)}><option value="environment">使用现有环境配置</option><option value="desktop">在桌面配置模型</option></select>
      <p>从模型供应商控制台获取 API Key，填写其兼容接口地址和模型 ID。保存不会发起模型请求或改变已有运行的模型。</p><p className="small muted">当前桌面尚未启动 Agent 执行进程；这里先保存配置，现有 CLI 任务仍读取原环境配置。</p><div className="settings-grid"><Field label="模型供应商" id="provider"><select id="provider" disabled={modelDisabled} value={value.model.provider} onChange={e => model('provider', e.target.value)}><option value="deepseek">DeepSeek</option><option value="qwen">Qwen</option><option value="anthropic">Anthropic</option><option value="openai-compatible">OpenAI 兼容接口</option></select></Field><Field label="模型 ID" id="model-id"><TextInput id="model-id" block disabled={modelDisabled} value={value.model.model} onChange={e => model('model', e.target.value)} /></Field></div>
      <Field label="API Base URL" id="base-url" hint="仅接受不含凭证的 HTTPS 地址。"><TextInput id="base-url" block disabled={modelDisabled} value={value.model.base_url} onChange={e => model('base_url', e.target.value)} /></Field><Field label="模型 API Key" id="model-api-key" hint={value.model.api_key_configured ? '已配置；留空保留原值。' : '从你的供应商控制台获取。'}><TextInput id="model-api-key" block type="password" autoComplete="new-password" disabled={modelDisabled} value={modelKey} onChange={e => setModelKey(e.target.value)} /></Field>{value.model.provider === 'deepseek' && <button className="text-button" onClick={() => openExternal(guide.key)}>打开 DeepSeek API Key 页面</button>}<label className="checkbox-row"><input type="checkbox" disabled={modelDisabled} checked={clearModel} onChange={e => setClearModel(e.target.checked)} />清除已保存的模型 Key</label>
      <h3>新任务默认预算</h3><p className="small muted">这里保存默认值，不清零历史用量，不修改现有运行账本。价格填写你的保守估算。</p><div className="settings-grid">{([['max_run_cost_cny', '单次任务上限（元）'], ['budget_cny', '累计预算上限（元）'], ['input_cny_per_million', '每百万输入 token 估价（元）'], ['output_cny_per_million', '每百万输出 token 估价（元）']] as const).map(([key, title]) => <Field label={title} id={key} key={key}><TextInput id={key} block type="number" min="0.01" step="0.01" disabled={modelDisabled} value={value.model[key]} onChange={e => model(key, Number(e.target.value))} /></Field>)}</div><Button onClick={() => save('model')} disabled={busy}>保存并校验配置格式</Button>
      </>}
    </div>}
    {error && <p role="alert" className="danger-text settings-message">{error}</p>}{notice && <p role="status" className="settings-message">{notice}</p>}
    <div className="row dialog-actions"><span className="small muted">保存到本机，不上传密钥到项目仓库。</span><Button onClick={close} disabled={busy}>关闭</Button><Button variant="primary" onClick={() => save()} disabled={!value || busy}>{busy ? '正在保存与核对…' : '保存设置'}</Button></div>
  </section></div>
}
function Field({ label, hint, id, children }: { label: string; hint?: string; id: string; children: React.ReactNode }) { return <div className="settings-field"><label className="field-label" htmlFor={id}>{label}</label>{children}{hint && <small className="muted">{hint}</small>}</div> }
export function message(code: string) {
  const values: Record<string, string> = { model_or_connection_configuration_invalid: '配置尚不完整，请检查模型 Key、供应商字段和现有环境配置。', github_bot_identity_required_use_personal_sender: '当前连接是个人身份。请配置 GitHub App，或改为以本人身份发送。', github_test_repository_required: '请填写连接检查仓库（owner/repository）。', job_is_running: '相同操作正在处理中，请稍后核对。', invalid_fields: '部分字段格式不正确，请检查数字 ID、仓库名称、HTTPS 地址和预算。', github_app_configuration_incomplete: '请先填写 App ID、Installation ID 并导入私钥。', github_private_key_must_be_unencrypted_rsa_pem: '请选择 GitHub 下载的未加密 RSA PEM 私钥。', desktop_settings_revision_conflict: '设置已被更新，请关闭后重新打开再修改。', github_app_http_401: 'GitHub App 验证失败，请核对 App ID 与私钥是否属于同一个 App。', github_app_http_404: '没有找到该安装，请核对 Installation ID 与 App 是否匹配。', github_http_404: '无法读取仓库或 Issue，请核对名称及 App 安装范围。', github_http_403: 'GitHub 拒绝访问，请检查安装权限或 API 限流。', github_personal_comment_token_required: '请先在设置中添加本人评论令牌。', github_personal_identity_must_match_registered_owner: '个人令牌身份与登记负责人不一致，评论未发送。', comment_delivery_uncertain_retry_same_draft: '发送结果尚不确定。请保留此草稿再次核对，不要新建重复评论。', comment_delivery_uncertain_check_github_before_new_draft: '尚未找到已发送记录，结果仍不确定。请在 GitHub 核对后再决定是否新建草稿。' }
  return values[code] || code
}
