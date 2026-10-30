import { useEffect, useMemo, useRef, useState } from 'react'
import { Textarea } from '@primer/react'
import { demoMode, useRuntime } from './api'
import { projectActions, role } from './events'
import { Button, Empty, Icon, Output, StateLabel, StatusIcon, download, isWaiting, openExternal, statusKind, statusText } from './components'
import type { Action, RunDetail, RunSummary } from './types'
import { ResizeHandle, usePanels } from './panels'
import { Settings } from './Settings'
import { Discussion } from './Discussion'

type View = 'execution' | 'overview' | 'inbox' | 'queue' | 'executors'
type Tab = '执行' | '改动' | '检查与审核' | 'Issue 讨论'
function duration(seconds?: number) { return seconds == null ? '运行中' : seconds < 60 ? `${seconds.toFixed(1)}s` : `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s` }
function when(value?: string) { if (!value) return '时间未记录'; return new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) }
function short(value?: string) { return value?.slice(0, 7) || '尚未生成' }

function ActionRow({ action, query, initialOpen, onCopy }: { action: Action; query: string; initialOpen: boolean; onCopy: (s: string) => void }) {
  const [open, setOpen] = useState(initialOpen); const [all, setAll] = useState(false); const [parameters, setParameters] = useState(false)
  const lines = action.output.split('\n'); const truncated = !all && lines.length > 12
  const output = truncated ? lines.slice(-8).join('\n') : action.output
  useEffect(() => { if (query) setOpen(true) }, [query])
  return <section className="action" data-action-id={action.id}>
    <button className="action-header" onClick={() => setOpen(!open)} aria-expanded={open}>
      <Icon name={open ? 'ChevronDown' : 'ChevronRight'} /><StatusIcon status={action.status} />
      <code>{action.name === 'run_registered_check' ? 'run_command' : action.name}</code><span className="action-object" title={action.object}>{action.object}</span><span className="action-duration">{action.status === 'unknown' ? '结果未关联' : duration(action.duration)}</span>
    </button>
    {open && <div className="action-body">
      {action.path && action.name === 'read_file' && <div className="path-row"><Icon name="FileCode" /><code title={action.path}>{action.path}</code><button className="text-button" onClick={() => setAll(!all)}>{all ? '收起内容' : '查看内容'}</button><button className="text-button" onClick={() => onCopy(action.path!)}>复制路径</button></div>}
      {action.diff ? <Output value={action.diff} diff /> : output && (action.name !== 'read_file' || all) && <Output value={output} />}
      {action.exit_code != null && <span className={'exit-label ' + (action.exit_code === 0 ? 'success-text' : 'danger-text')}>exit {action.exit_code}</span>}
      {(truncated || action.truncated || action.status === 'running' && lines.length > 1) && <div className="output-disclosure"><span>{action.truncated ? '运行时已截断，当前保留首尾输出' : `已显示${truncated ? '最近 8' : lines.length}行`}</span><button className="text-button" onClick={() => setAll(!all)}>{all ? '收起输出' : '展开完整输出'}</button></div>}
      <details className="parameters" open={parameters} onToggle={e => setParameters(e.currentTarget.open)}><summary>参数与时间</summary><pre>{JSON.stringify(action.args, null, 2)}</pre><span>开始：{when(action.started)}{action.finished ? ` · 结束：${when(action.finished)}` : ''}{action.legacy ? ' · 历史事件缺少动作关联 ID' : ''}</span></details>
    </div>}
  </section>
}

function AgentList({ run, selected, onSelect, actions }: { run: RunDetail; selected: string; onSelect: (s: string) => void; actions: Action[] }) {
  const roles = ['Planner', 'Implementer', 'Verifier', 'Reviewers']
  return <aside className="agents" aria-label="Agent 与子任务"><div className="agent-heading"><strong>Agent / 子任务</strong><span>4</span></div>
    {roles.map(name => {
      const mine = actions.filter(a => role(a.phase) === name); let state = run.status === 'running' && (role(run.phase) === name || mine.some(a => a.status === 'running')) ? 'running' : run.stage_status?.[name] || (mine.length ? 'success' : 'idle')
      if (demoMode && (name === 'Planner' || isWaiting(run.status))) state = 'success'
      if (name === 'Verifier' && run.verified.length) state = run.verified.some(c => c.exit_code !== 0) ? 'failed' : 'success'
      if (name === 'Reviewers' && run.review?.verdict) state = run.review.verdict === 'approved' ? 'success' : 'waiting'
      if (mine.length && mine.at(-1)?.status === 'failed' && state !== 'running') state = 'failed'
      const caption = state === 'running' ? run.pause_requested ? '等待阶段安全检查点' : demoMode === 'change' ? '等待当前操作结束' : '正在执行 · 实时输出' : state === 'success' ? name === 'Planner' ? '计划已完成' : name === 'Reviewers' ? '双审已完成' : name === 'Verifier' ? '独立检查通过' : '实现已完成' : state === 'failed' ? '存在失败动作' : '尚无执行记录'
      return <button className={'agent-row ' + (selected === name ? 'selected' : '')} key={name} onClick={() => onSelect(name)}><StatusIcon status={state} /><span><strong>{name}</strong><small>{caption}</small></span></button>
    })}
    <div className="run-facts"><small>本次执行</small><p>运行 <code title={run.id}>{run.id}</code></p><p>仓库 {run.repository}</p><p>需求版本 v{run.requirements_revision}{demoMode === 'change' ? ' → v4 待应用' : ''}</p><p>阶段 {run.phase}</p>{run.plan?.summary && <p className="plan-summary">{run.plan.summary}</p>}</div>
  </aside>
}

function Approval({ run, onDecision }: { run: RunDetail; onDecision: (approved: boolean) => void }) {
  return <div className="console approval" id="approval"><div className="console-heading"><StatusIcon status="waiting" /><strong>核对候选并批准发布</strong><span className="push muted small">负责人操作</span></div>
    <div className="approval-body"><div className="approval-summary"><strong>候选实现已完成，请核对检查与审核证据。</strong><p>批准绑定当前候选。创建草稿 PR 与后续原生 Approve 是两个独立操作。</p></div>
      <div className="candidate-row"><small>候选</small><code className="accent" title={run.candidate}>{short(run.candidate)}</code><button className="icon-button" aria-label="复制候选 SHA" onClick={() => navigator.clipboard.writeText(run.candidate || '')}><Icon name="Copy" /></button><small>· 需求 v{run.requirements_revision}</small></div>
      <div className="evidence-row"><StatusIcon status={run.verified.length && run.verified.every(c => c.exit_code === 0 && !c.error) ? 'success' : 'waiting'} /><div><strong>独立验证</strong><small>{run.verified.length ? run.verified.map(c => `${c.name} · exit ${c.exit_code ?? '未知'}`).join('，') : '尚无验证证据'}</small></div></div>
      {['correctness', 'context'].map((key, i) => <div className="evidence-row" key={key}><StatusIcon status={run.review?.perspectives?.[key]?.verdict === 'approved' ? 'success' : 'waiting'} /><div><strong>{i === 0 ? '正确性审核' : '需求上下文审核'}</strong><small>{run.review?.perspectives?.[key]?.verdict || '查看汇总审核依据'} · 固定候选 {short(run.candidate)}</small></div></div>)}
      <div className="approval-check"><strong>发布前核对</strong><p><code>{run.changes.changed_files?.length || 0} 个文件 · +{run.changes.additions ?? 0} / −{run.changes.deletions ?? 0} · {run.branch}</code></p><p className="muted small">批准只对当前候选与需求版本有效；候选变化后需重新批准。</p><div className="row"><Button variant="primary" onClick={() => onDecision(true)}>批准此候选</Button><Button onClick={() => onDecision(false)}>要求修改</Button></div><p className="small muted">{demoMode ? '设计预览不会发布 PR 或修改真实运行。' : '本地批准后，通过 CLI publish 创建草稿 PR；桌面发布与 GitHub 原生审核尚未接通。'}</p></div>
    </div><div className="console-footer"><StatusIcon status="waiting" />等待负责人 · 执行现场已保存<span className="push">{when(run.updated_at)}</span></div>
  </div>
}

export default function App() {
  const runtime = useRuntime(); const { runs, queue, id, setId, detail: run, connection, error, operate } = runtime
  const [view, setView] = useState<View>('execution'); const [tab, setTab] = useState<Tab>('执行')
  const [selected, setSelected] = useState(demoMode === 'waiting' ? 'Reviewers' : 'Implementer')
  const [search, setSearch] = useState(''); const [query, setQuery] = useState(''); const [filter, setFilter] = useState('all')
  const [toast, setToast] = useState(''); const [dialog, setDialog] = useState<'approve' | 'reject' | 'answer' | 'settings' | null>(null)
  const [reason, setReason] = useState(''); const [busy, setBusy] = useState(false); const [operationError, setOperationError] = useState('')
  const [autoScroll, setAutoScroll] = useState(true); const scroll = useRef<HTMLDivElement>(null); const globalSearch = useRef<HTMLInputElement>(null)
  const actions = useMemo(() => projectActions(runtime.events).map(a => run && run.status !== 'running' && a.status === 'running' ? { ...a, status: 'unknown' as const } : a), [runtime.events, run?.status])
  const currentActions = actions.filter(a => role(a.phase) === selected && (filter !== 'failed' || a.status === 'failed') && (!query || `${a.name}\n${a.object}\n${a.output}\n${a.diff || ''}\n${JSON.stringify(a.args)}`.toLowerCase().includes(query.toLowerCase())))
  const repositories = [...new Set(runs.map(r => r.repository))]
  const waiting = runs.filter(r => isWaiting(r.status))
  const connected = connection === 'connected' || connection === 'demo'
  const { panels, setPanels, style: panelStyle, sidebarWidth, agentWidth, sidebarMax, agentMax } = usePanels(view === 'execution' && tab === '执行' && !!run)
  useEffect(() => { const fn = (e: KeyboardEvent) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setPanels(p => ({ ...p, sidebarHidden: false })); requestAnimationFrame(() => globalSearch.current?.focus()) } if (e.key === 'Escape') setDialog(null) }; window.addEventListener('keydown', fn); return () => window.removeEventListener('keydown', fn) }, [])
  useEffect(() => { if (toast) { const t = setTimeout(() => setToast(''), 3500); return () => clearTimeout(t) } }, [toast])
  useEffect(() => { if (autoScroll && scroll.current && !query) scroll.current.scrollTop = scroll.current.scrollHeight }, [runtime.events, autoScroll, query])
  useEffect(() => { if (!run || demoMode) return; setSelected(run.status === 'awaiting_approval' ? 'Reviewers' : role(run.phase)) }, [id, run?.phase, run?.status])
  async function copy(s: string) { try { await navigator.clipboard.writeText(s); setToast('已复制') } catch { setToast('复制失败，请手动选择文本复制') } }
  function choose(r: RunSummary) { setId(r.id); setView('execution'); setTab('执行'); setQuery(''); setFilter('all') }
  function showDialog(value: typeof dialog) { setReason(''); setOperationError(''); setDialog(value) }
  async function pause() {
    if (!run) return
    if (demoMode) { setToast('设计预览：请求将在安全边界保存现场，不操作真实任务。'); return }
    try { await operate('pause', { revision: run.revision }); setToast('已请求暂停，将在本阶段安全检查点生效') } catch (e) { setToast(String(e instanceof Error ? e.message : e)) }
  }
  async function submit() {
    if (!run) return; if (demoMode) { setToast('设计预览：未批准、发布或修改任何真实任务'); setDialog(null); return }
    setBusy(true); setOperationError('')
    try {
      if (dialog === 'answer') { const req = run.requests.find(r => r.kind === 'clarification')!; await operate('answer', { revision: run.revision, request_id: req.id, version: req.version, reason }) }
      else await operate('decision', { revision: run.revision, commit: run.candidate, approved: dialog === 'approve', reason })
      setDialog(null); setToast(dialog === 'answer' ? '答复已记录；通过 CLI resume 显式继续当前运行。' : '决定已记录，候选与需求版本已核对。')
    } catch (e) { setOperationError((e instanceof Error ? e.message : String(e)) + '；请核对最新候选和状态后重试。') }
    finally { setBusy(false) }
  }
  function nav(label: string, icon: string, target: View, count?: number) { return <button className={'nav-row ' + (view === target ? 'selected' : '')} onClick={() => setView(target)}><Icon name={icon} /><span>{label}</span>{count ? <small>{count}</small> : null}</button> }
  const shownRuns = runs.filter(r => `${r.title} ${r.repository} ${r.id} ${r.number}`.toLowerCase().includes(search.toLowerCase()))
  const displayRuns = view === 'inbox' ? shownRuns.filter(r => isWaiting(r.status)) : shownRuns
  return <div className="app" style={panelStyle}>
    <header className="titlebar"><button className="titlebar-tool" aria-label={panels.sidebarHidden ? '显示导航栏' : '隐藏导航栏'} aria-expanded={!panels.sidebarHidden} aria-controls="application-sidebar" title="切换导航栏 (Ctrl+B)" onClick={() => setPanels(p => ({ ...p, sidebarHidden: !p.sidebarHidden }))}><svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><rect x="1.5" y="2.5" width="13" height="11" rx="2" stroke="currentColor" /><path d="M6 3v10" stroke="currentColor" /></svg></button><span>RepoPilot{demoMode ? ' · 设计预览' : ''}</span><button className="titlebar-tool" aria-label="桌面设置与连接" title="桌面设置与连接" onClick={() => showDialog('settings')}><Icon name="Gear" /></button><div className="window-controls">{(['minimize', 'maximize', 'close'] as const).map((action, i) => <button aria-label={['最小化', '最大化或还原', '关闭到托盘'][i]} key={action} onClick={() => window.repopilot?.window(action)} disabled={!window.repopilot}>{['—', '□', '×'][i]}</button>)}</div></header>
    <div className="workspace">{!panels.sidebarHidden && <><aside id="application-sidebar" className="sidebar" aria-label="应用导航"><div className="brand"><Icon name="Repo1" /><strong>RepoPilot</strong><Icon name="ChevronDown" /></div>
      <div className="global-search"><Icon name="Search" /><input ref={globalSearch} aria-label="搜索任务或仓库" placeholder="搜索任务或仓库" value={search} onChange={e => { setSearch(e.target.value); setView('overview') }} /><kbd>Ctrl K</kbd></div>
      <div className="nav-group">{nav('任务总览', 'Home', 'overview')}{nav('待我处理', 'Inbox', 'inbox', waiting.length)}</div>
      <div className="nav-group"><small className="nav-section-label">仓库</small>{repositories.map(repository => <div key={repository}><button className="nav-row" onClick={() => { const r = runs.find(r => r.repository === repository); if (r) choose(r) }}><Icon name="Repo" /><span>{repository}</span><small>{runs.filter(r => r.repository === repository).length}</small></button>{repository === run?.repository && <><button className={'nav-row indented ' + (view === 'execution' ? 'selected' : '')} onClick={() => setView('execution')}><Icon name="History" /><span>执行历史</span></button><button className="nav-row indented" onClick={() => run?.url ? openExternal(run.url) : setToast('此任务没有关联 Issue')}><Icon name="IssueOpened" /><span>Issue 列表</span></button><button className="nav-row indented" onClick={() => showDialog('settings')}><Icon name="Gear" /><span>仓库设置</span></button></>}</div>)}</div>
      <div className="recent"><small>最近执行</small>{runs.slice(0, 3).map((r, i) => <button className="recent-row" onClick={() => choose(r)} key={r.id}><StatusIcon status={statusKind(r.status)} /><span><strong>{demoMode ? `#${28 - i} ` : ''}{r.title.replace('为本地', '本地').replace('添加', '')}</strong><small>{r.number ? `Issue #${r.number} · ` : ''}{r.id === id ? '当前执行' : statusText(r.status)}</small></span></button>)}</div>
      <div className="sidebar-bottom">{nav('队列', 'Stack', 'queue', queue.filter(q => q.status === 'queued').length)}{nav('执行器', 'Cpu', 'executors', runs.filter(r => r.status === 'running').length)}<button className="nav-row settings" onClick={() => showDialog('settings')}><Icon name="Gear" /><span>设置</span></button></div>
    </aside><ResizeHandle label="调整导航栏宽度" width={sidebarWidth} minimum={180} maximum={sidebarMax} reset={216} onChange={width => setPanels(p => ({ ...p, sidebarWidth: width }))} /></>}
    <main>{view !== 'execution' ? <div className="listing"><h1>{view === 'overview' ? '任务总览' : view === 'inbox' ? '待我处理' : view === 'queue' ? '任务队列' : '执行器'}</h1><p className="muted">{view === 'executors' ? '当前持久化运行记录；运行状态不等同于进程健康探测。' : '选择一条记录，查看同一次执行的动作、改动和证据。'}</p>{view === 'queue' ? queue.length ? <table><thead><tr><th>仓库</th><th>状态</th><th>执行</th></tr></thead><tbody>{queue.map(q => <tr key={q.id}><td>{q.repository}</td><td>{q.status}</td><td>{q.run_id ? <button className="text-button" onClick={() => { setId(q.run_id!); setView('execution') }}>{q.run_id}</button> : q.error || '尚未分派'}</td></tr>)}</tbody></table> : <Empty title="队列为空">没有已登记的排队任务。</Empty> : displayRuns.length ? <table><thead><tr><th>任务</th><th>仓库</th><th>状态</th><th>最近更新</th></tr></thead><tbody>{displayRuns.filter(r => view !== 'executors' || r.status === 'running').map(r => <tr key={r.id}><td><button className="text-button" onClick={() => choose(r)}>{r.title}</button><small className="block">{r.id}</small></td><td>{r.repository}</td><td><StateLabel status={r.status} /></td><td>{when(r.updated_at)}</td></tr>)}</tbody></table> : <Empty title="暂无任务">{search ? '没有匹配的任务或仓库。' : '已有 CLI 任务会自动显示在这里。'}</Empty>}</div> : !run ? <Empty title={connection === 'disconnected' ? '本机服务未连接' : id ? '正在读取执行记录' : '还没有执行记录'}>{error || '启动已有的本机任务后，可在这里查看动作和检查结果。'}</Empty> : <>
      <div className="page-header"><div className="breadcrumbs"><button onClick={() => setView('overview')}>仓库</button><Icon name="ChevronRight" /><button onClick={() => setView('overview')}>{run.repository}</button><Icon name="ChevronRight" /><span title={run.id}>执行 {demoMode ? '#28' : run.id}</span><button className="push external" onClick={() => run.url && openExternal(run.url)} disabled={!run.url}>在 GitHub 查看 Issue <Icon name="LinkExternal" /></button></div>
        <div className="heading-row"><h1>{run.title} {run.number && <span>#{run.number}</span>}</h1>{run.status === 'running' ? <Button onClick={pause} disabled={run.pause_requested || !connected}>{run.pause_requested ? '等待检查点' : '请求暂停'}</Button> : <Button onClick={() => { setTab('检查与审核'); setView('execution') }}>查看交付报告</Button>}</div>
        <div className="metadata"><StateLabel status={connected ? run.status : 'unknown'} /><Icon name="GitBranch" /><code title={run.branch}>{run.branch}</code><small>·</small><small>候选 {short(run.candidate)}</small><small>· 需求 v{run.requirements_revision}</small><small className="push">{when(run.created_at)} 开始</small></div>
        {!connected && <div role="alert" className="banner attention"><StatusIcon status="waiting" /><div><strong>连接中断，当前状态待核对</strong><small>最后记录 {when(run.updated_at)} · 正在重连，请勿依据旧快照判断任务仍在运行。</small></div></div>}
        {run.status === 'awaiting_approval' && <div className="banner attention"><StatusIcon status="waiting" /><div><strong>需要你核对并批准发布</strong><small>候选 {short(run.candidate)} · 需求 v{run.requirements_revision} · 检查与双审证据见下方</small></div><Button variant="primary" className="push" onClick={() => { setTab('执行'); setSelected('Reviewers'); document.getElementById('approval')?.scrollIntoView({ block: 'nearest' }) }}>核对候选</Button></div>}
        {demoMode === 'change' && <div className="banner info"><Icon name="Sync" /><div><strong>已收到 @Agent 的需求变更</strong><small>等待当前 pytest 结束 → 保存现场 → 基于需求 v4 重新规划；普通评论不改变任务</small></div></div>}
        {run.pause_requested && <div className="banner info"><Icon name="Sync" /><div><strong>暂停请求已收到</strong><small>本阶段结束并保存检查点后暂停，在途命令继续完成。</small></div></div>}
        {run.status === 'needs_input' && <div className="banner attention"><StatusIcon status="waiting" /><div><strong>{run.requests[0]?.summary || '需要补充信息'}</strong><small>答复关联请求版本；普通评论不会被此入口采纳。</small></div><Button className="push" onClick={() => showDialog('answer')} disabled={!run.capabilities.answer || !connected}>回答问题</Button></div>}
        <nav className="tabs" aria-label="执行页导航">{(['执行', '改动', '检查与审核', 'Issue 讨论'] as Tab[]).map(t => <button key={t} onClick={() => setTab(t)} aria-current={tab === t ? 'page' : undefined}>{t}{t === '改动' && <small>{run.changes.changed_files?.length || ''}</small>}{t === '检查与审核' && <small>{run.verified.length || ''}</small>}</button>)}{tab === '执行' && <button className="push agent-toggle" aria-label={panels.agentHidden ? '显示 Agent 列表' : '隐藏 Agent 列表'} aria-expanded={!panels.agentHidden} onClick={() => setPanels(p => ({ ...p, agentHidden: !p.agentHidden }))}><Icon name="Cpu" />{panels.agentHidden ? '显示 Agent' : '收起 Agent'}</button>}</nav>
      </div>
      <div className={'detail-content ' + (tab === '执行' ? 'split-content' : '')}>{tab === '执行' ? <>{!panels.agentHidden && <><AgentList run={run} selected={selected} onSelect={setSelected} actions={actions} /><ResizeHandle label="调整 Agent 列表宽度" width={agentWidth} minimum={180} maximum={agentMax} reset={224} onChange={width => setPanels(p => ({ ...p, agentWidth: width }))} /></>}{run.status === 'awaiting_approval' && selected === 'Reviewers' ? <Approval run={run} onDecision={approved => showDialog(approved ? 'approve' : 'reject')} /> : <div className="console"><div className="console-heading"><StatusIcon status={connected && run.status === 'running' ? 'running' : 'idle'} /><strong>{selected}</strong><span className="muted small">{selected === 'Implementer' ? '实现与自测' : selected === 'Planner' ? '任务规划' : selected === 'Verifier' ? '独立检查' : '双视角审核'}</span><span className="push muted small">{run.status === 'running' ? '当前阶段 ' : ''}{run.phase}</span></div>
          <div className="log-toolbar"><Icon name="Search" /><input aria-label="搜索此 Agent 的日志" placeholder="搜索此 Agent 的日志" value={query} onChange={e => setQuery(e.target.value)} /><select aria-label="筛选动作" value={filter} onChange={e => setFilter(e.target.value)}><option value="all">全部动作</option><option value="failed">仅失败动作</option></select><button className="icon-button" aria-label="下载当前 Agent 日志" onClick={() => download(run.id + '-' + selected + '.txt', actions.filter(a => role(a.phase) === selected).map(a => `${a.started} ${a.name}\n${JSON.stringify(a.args)}\n${a.output}`).join('\n\n'))}><Icon name="Download" /></button></div>
          <div className="actions-scroll" ref={scroll} onWheel={() => setAutoScroll(false)}>{currentActions.length ? currentActions.map((a, i) => <ActionRow key={a.id} action={a} query={query} initialOpen={a.status === 'failed' || a.status === 'running' || Boolean(demoMode && i !== 3 && !(demoMode === 'change' && i === 1))} onCopy={copy} />) : <Empty title={query ? '没有匹配的日志' : '尚无工具动作'}>{selected === 'Planner' && run.plan?.summary || '该 Agent 尚未执行，或旧运行没有可关联的动作记录。'}</Empty>}</div>
          <div className="console-footer"><Icon name={connected ? 'DotFill' : 'Clock'} /><button className="text-button muted" onClick={() => setAutoScroll(!autoScroll)}>{connected ? '实时输出' : '连接中断'} · {autoScroll ? '自动滚动' : '已停止跟随，点击恢复'}</button><span className="push">{when(runtime.events.at(-1)?.time || run.updated_at)}</span></div>
        </div>}</> : tab === '改动' ? <div className="panel"><div className="panel-heading"><strong>候选改动</strong><span className="muted">{short(run.candidate)} · +{run.changes.additions || 0} / −{run.changes.deletions || 0}</span><Button onClick={() => download(run.id + '.diff', run.changes.diff || '')} disabled={!run.changes.diff}>下载差异</Button></div><div className="file-list">{run.changes.changed_files?.map(p => <span key={p}><Icon name="FileCode" /><code>{p}</code></span>)}</div>{run.changes.diff ? <Output value={run.changes.diff} diff /> : <Empty title="尚无已保存的差异">运行时生成候选产物后显示，不依据界面猜测改动。</Empty>}{run.changes.truncated && <p>差异已被运行时截断，完整内容请在工作区核对。</p>}</div> : tab === '检查与审核' ? <div className="panel"><h2>独立检查与审核</h2>{run.verified.length ? run.verified.map((c, i) => <details key={i} open={c.exit_code !== 0}><summary><StatusIcon status={c.exit_code === 0 ? 'success' : 'failed'} />{c.name}<code>exit {c.exit_code ?? '未知'}</code></summary><Output value={c.output_tail || c.error || '无输出摘要'} /></details>) : <p className="muted">尚无独立检查记录。</p>}<h3>审核结论</h3><p>{run.review?.verdict || '审核尚未开始或没有汇总结论'}</p>{run.review?.findings?.map((f, i) => <pre key={i}>{JSON.stringify(f, null, 2)}</pre>)}{run.approval && <><h3>发布决定</h3><p>{run.approval.approved ? '已批准' : '已要求修改'} · {short(run.approval.commit)}</p><p>{run.approval.reason}</p></>}</div> : <Discussion key={run.id} run={run} answer={() => showDialog('answer')} settings={() => showDialog('settings')} />}</div>
    </>}</main></div>
    <footer className="statusbar"><span className={'connection-dot ' + (connected ? 'connected' : '')} /><span>{connection === 'demo' ? '设计预览 · 示例数据' : connected ? '本机服务已连接' : connection === 'connecting' ? '正在连接本机服务' : '连接中断 · 状态待核对'}</span><span className="push">{demoMode ? '执行器 2 / 2 · 队列 1' : `运行记录 ${runs.filter(r => r.status === 'running').length} · 排队 ${queue.filter(q => q.status === 'queued').length}`}</span></footer>
    {toast && <div className="toast" role="status">{toast}</div>}
    {dialog === 'settings' ? <Settings close={() => setDialog(null)} repository={run?.repository} /> : dialog && <div className="dialog-backdrop" onMouseDown={e => { if (e.target === e.currentTarget && !busy) setDialog(null) }}><section role="dialog" aria-modal="true" aria-labelledby="dialog-title" className="dialog"><div className="panel-heading"><h2 id="dialog-title">{dialog === 'answer' ? '回答 Agent 的问题' : dialog === 'approve' ? '批准当前候选' : '要求修改'}</h2><button className="text-button" onClick={() => setDialog(null)} disabled={busy} aria-label="关闭对话框">×</button></div><><p>{dialog === 'answer' ? run?.requests.find(r => r.kind === 'clarification')?.questions.join('\n') : `决定绑定候选 ${run?.candidate || '尚未生成'} 与需求 v${run?.requirements_revision}。`}</p><label htmlFor="reason">{dialog === 'answer' ? '正式答复' : '核对说明'}</label><Textarea id="reason" autoFocus rows={5} value={reason} onChange={e => setReason(e.target.value)} block placeholder={dialog === 'answer' ? '补充完成任务所需的信息' : '说明核对过的改动、检查与审核依据'} />{operationError && <p role="alert" className="danger-text">{operationError}</p>}<p className="muted small">{dialog === 'answer' ? '只记录答复；恢复执行仍使用原运行身份与累计预算。' : '此操作记录本地发布决定，不代替后续 GitHub 原生 Approve。'}</p><div className="row dialog-actions"><Button onClick={() => setDialog(null)} disabled={busy}>取消</Button><Button variant={dialog === 'reject' ? 'danger' : 'primary'} onClick={submit} disabled={!reason.trim() || busy || !connected}>{busy ? '正在核对…' : dialog === 'answer' ? '提交正式答复' : dialog === 'approve' ? '确认批准' : '确认要求修改'}</Button></div></></section></div>}
  </div>
}
