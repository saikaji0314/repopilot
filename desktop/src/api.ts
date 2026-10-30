import { useEffect, useState } from 'react'
import type { Operation, QueueItem, RunDetail, RunSummary, TraceEvent } from './types'
import { demoDetail, demoEvents, demoRuns } from './demo'

export const demoMode = new URLSearchParams(location.search).get('demo')
let token = ''
export async function connect() { if (window.repopilot) token = (await window.repopilot.bootstrap()).token }
export async function request<T>(path: string, body?: unknown): Promise<T> {
  if (!token && window.repopilot) await connect()
  const r = await fetch('/api/' + path, { headers: { Authorization: 'Bearer ' + token, ...(body ? { 'Content-Type': 'application/json' } : {}) }, ...(body ? { method: 'POST', body: JSON.stringify(body) } : {}) })
  if (!r.ok) { const b = await r.json().catch(() => ({})); throw new Error(b.detail || `请求失败 (${r.status})`) }
  return r.json()
}
export function useRuntime() {
  const [runs, setRuns] = useState<RunSummary[]>([]); const [queue, setQueue] = useState<QueueItem[]>([])
  const [id, setId] = useState(''); const [detail, setDetail] = useState<RunDetail | null>(null)
  const [events, setEvents] = useState<TraceEvent[]>([]); const [connection, setConnection] = useState('connecting')
  const [error, setError] = useState('')
  useEffect(() => {
    if (demoMode) { setRuns([demoDetail(demoMode), ...demoRuns.slice(1)]); setId(demoRuns[0].id); setConnection('demo'); return }
    let disposed = false
    async function list() {
      try { await connect(); const data = await request<{ runs: RunSummary[]; queue: QueueItem[] }>('runs'); if (disposed) return; setRuns(data.runs); setQueue(data.queue); setId(current => current || data.runs[0]?.id || ''); if (!data.runs.length) setConnection('connected'); setError('') }
      catch (e) { if (!disposed) { setConnection('disconnected'); setError(String(e instanceof Error ? e.message : e)) } }
    }
    void list(); const timer = setInterval(list, 5000)
    return () => { disposed = true; clearInterval(timer) }
  }, [])
  useEffect(() => {
    if (!id) return
    if (demoMode) { setDetail(demoDetail(demoMode)); setEvents(demoEvents); return }
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>
    setDetail(null); setEvents([])
    async function stream() {
      const attempt = new AbortController(); const cancel = () => attempt.abort()
      controller.signal.addEventListener('abort', cancel, { once: true })
      try {
        await connect()
        // Replay on reconnect and replace by durable byte id. The API never returns
        // internal model events; an interrupted final line remains unconsumed.
        const r = await fetch(`/api/runs/${encodeURIComponent(id)}/events`, { headers: { Authorization: 'Bearer ' + token }, signal: attempt.signal })
        if (!r.ok || !r.body) throw new Error(`连接失败 (${r.status})`)
        const reader = r.body.pipeThrough(new TextDecoderStream()).getReader(); let pending = ''; let fresh = true
        while (!controller.signal.aborted) {
          let watchdog: ReturnType<typeof setTimeout> | undefined
          const read = reader.read()
          const { done, value } = await Promise.race([read, new Promise<never>((_, reject) => { watchdog = setTimeout(() => { reject(new Error('超过 10 秒未收到服务更新')); attempt.abort() }, 10000) })]).finally(() => clearTimeout(watchdog))
          if (done) throw new Error('服务连接已中断')
          pending += value
          let end: number
          while ((end = pending.indexOf('\n\n')) >= 0) {
            const block = pending.slice(0, end); pending = pending.slice(end + 2)
            const json = block.split('\n').filter(l => l.startsWith('data: ')).map(l => l.slice(6)).join('\n'); if (!json) continue
            const data = JSON.parse(json) as { events: TraceEvent[]; reset: boolean; snapshot: RunDetail }
            const replace = fresh || data.reset; fresh = false
            setEvents(current => [...new Map((replace ? data.events : [...current, ...data.events]).map(e => [e.id, e])).values()])
            setDetail(data.snapshot); setConnection('connected'); setError('')
          }
        }
      } catch (e) { attempt.abort(); if (!controller.signal.aborted) { setConnection('disconnected'); setError(e instanceof Error ? e.message : String(e)); timer = setTimeout(stream, 2000) } }
      finally { controller.signal.removeEventListener('abort', cancel) }
    }
    void stream(); return () => { controller.abort(); clearTimeout(timer) }
  }, [id])
  async function operate(action: string, body: Operation) {
    if (demoMode) throw new Error('设计预览不会修改真实运行')
    const data = await request(`runs/${encodeURIComponent(id)}/${action}`, body)
    setDetail(await request<RunDetail>(`runs/${encodeURIComponent(id)}`)); return data
  }
  return { runs, queue, id, setId, detail, events, connection, error, operate }
}
