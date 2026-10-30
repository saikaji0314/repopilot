import { useEffect, useState } from 'react'
import { Textarea } from '@primer/react'
import { demoMode, request } from './api'
import { Button, openExternal } from './components'
import { message } from './Settings'
import type { RunDetail } from './types'

interface Comment { id: number; author: string; author_type?: string; body: string; created_at: string; url: string }
interface DiscussionData { comments: Comment[]; login: string; owner: string; default_sender: 'bot' | 'user'; user_token_configured: boolean }
const draftKey = (id: string) => `repopilot.comment.${id}`
export function Discussion({ run, answer, settings }: { run: RunDetail; answer: () => void; settings: () => void }) {
  const [data, setData] = useState<DiscussionData | null>(null); const [error, setError] = useState(''); const [notice, setNotice] = useState(''); const [loading, setLoading] = useState(false); const [busy, setBusy] = useState(false)
  const [body, setBody] = useState(''); const [sender, setSender] = useState<'bot' | 'user'>('bot'); const [clientId, setClientId] = useState(crypto.randomUUID())
  const [locked, setLocked] = useState(false); const [confirmed, setConfirmed] = useState(false)
  const supported = run.url?.startsWith('https://github.com/')
  async function refresh() {
    if (!supported) return; setLoading(true); setError('')
    try {
      const result = demoMode ? { comments: [], login: 'repopilot[bot]', owner: '', default_sender: 'bot' as const, user_token_configured: false } : await request<DiscussionData>(`runs/${encodeURIComponent(run.id)}/comments`)
      setData(result)
    } catch (e) { setError(message(e instanceof Error ? e.message : String(e))) }
    finally { setLoading(false) }
  }
  useEffect(() => {
    // Persist the UUID with the draft before sending, so a reload can reconcile
    // an interrupted response using exactly the same delivery identity.
    try { const draft = JSON.parse(localStorage.getItem(draftKey(run.id)) || 'null'); if (draft) { setBody(draft.body); setSender(draft.sender); setClientId(draft.clientId); setLocked(Boolean(draft.locked)) } } catch { /* invalid draft is ignored */ }
    void refresh()
  }, [run.id])
  function store(text: string, identity: 'bot' | 'user', id: string, pending: boolean) {
    try { localStorage.setItem(draftKey(run.id), JSON.stringify({ body: text, sender: identity, clientId: id, locked: pending })); return true }
    catch { setError('本机无法保留草稿，暂不能安全发送。请检查本机存储后重试。'); return false }
  }
  function edit(text: string, identity = sender) { const id = crypto.randomUUID(); setBody(text); setSender(identity); setClientId(id); store(text, identity, id, false) }
  async function send() {
    setError(''); setNotice('')
    if (!store(body, sender, clientId, true)) return
    setBusy(true); setLocked(true)
    try {
      if (demoMode) { setNotice('设计预览：未发送真实评论。'); setLocked(false); store(body, sender, clientId, false); return }
      const result = await request<{ status: string; comment: Comment; reused: boolean }>(`runs/${encodeURIComponent(run.id)}/comments`, { body, sender, client_id: clientId })
      setData(d => d && ({ ...d, comments: [...d.comments.filter(c => c.id !== result.comment.id), result.comment] }))
      setNotice(result.reused ? '已核对远端记录，未重复发送。' : '评论已发送。'); edit(''); setLocked(false); setConfirmed(false)
    } catch (e) {
      const code = e instanceof Error ? e.message : String(e)
      const definite = /(?:configuration|identity_required|must_match|token_required|invalid|github_http_40[134]|github_app_http_40[134])/.test(code)
      setLocked(!definite); store(body, sender, clientId, !definite); setError(message(code))
    } finally { setBusy(false) }
  }
  return <div className="panel discussion-panel"><div className="panel-heading"><h2>Issue 讨论</h2><div className="row"><Button disabled={!supported || loading || busy} onClick={refresh}>{loading ? '正在读取…' : '刷新评论'}</Button>{run.url && <Button onClick={() => openExternal(run.url!)}>在 GitHub 打开</Button>}</div></div>
    <p className="muted small">这里发送普通讨论评论。回答 Agent 的问题请使用下方的正式答复入口。</p>
    {!supported ? <p>此本地运行没有关联 GitHub Issue。</p> : <>
      {data && <><div className="comment-list">{data.comments.length ? data.comments.map(comment => <article className="issue-comment" key={comment.id}><div className="comment-meta"><strong>{comment.author}</strong>{comment.author_type === 'Bot' && <span className="comment-bot">bot</span>}<time>{new Date(comment.created_at).toLocaleString('zh-CN')}</time><button className="text-button push" onClick={() => openExternal(comment.url)}>查看原文</button></div><pre>{comment.body.replace(/^<!-- repopilot-desktop-comment:[a-zA-Z0-9-]+ -->\s*/, '')}</pre></article>) : <p className="muted">当前没有评论。</p>}</div>
      <section className="comment-composer"><h3>{locked ? '核对上次发送结果' : '添加普通评论'}</h3><label htmlFor="comment-sender">发送身份</label><select id="comment-sender" disabled={busy || locked} value={sender} onChange={e => edit(body, e.target.value as 'bot' | 'user')}><option value="bot">机器人 · {data.login.endsWith('[bot]') ? data.login : '需配置 GitHub App'}</option><option value="user" disabled={!data.user_token_configured}>本人 · {data.owner || '需登记负责人'}{!data.user_token_configured ? '（需个人令牌）' : ''}</option></select><p className="small muted">发送后 GitHub 会显示以上所选身份。正式答复与本地批准另行记录。</p><label className="sr-only" htmlFor="comment-body">普通评论内容</label><Textarea id="comment-body" block rows={5} placeholder="写下对这个 Issue 的补充或讨论…" value={body} disabled={busy || locked} onChange={e => edit(e.target.value)} /><div className="row comment-actions"><span className="small muted">{body.length} / 8000</span><Button variant="primary" disabled={busy || !body.trim() || body.length > 8000 || sender === 'bot' && !data.login.endsWith('[bot]')} onClick={send}>{busy ? '正在发送与核对…' : locked ? '核对同一条评论' : sender === 'bot' ? '以机器人身份发送' : '以本人身份发送'}</Button></div>
      {locked && <div className="uncertain-comment"><p>草稿与投递编号已保留。可再次核对同一条评论；结果不确定时不会自动重新发布。</p><label className="checkbox-row"><input type="checkbox" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)} />我已在 GitHub 确认这条评论没有发送</label><Button disabled={!confirmed || busy} onClick={() => { setLocked(false); edit(body); setConfirmed(false); setError('') }}>启用新草稿</Button></div>}</section></>}
      {!data && !loading && <Button onClick={settings}>配置 GitHub 连接</Button>}
    </>}
    {error && <p role="alert" className="danger-text">{error}</p>}{notice && <p role="status">{notice}</p>}
    {run.requests.map(r => <section className="request" key={r.id}><strong>{r.summary}</strong>{r.questions.map(q => <p key={q}>{q}</p>)}<Button disabled={r.kind !== 'clarification'} onClick={answer}>提交正式答复</Button></section>)}
  </div>
}
