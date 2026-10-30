import type { Action, TraceEvent } from './types'

export const role = (phase: string) => phase.startsWith('review') ? 'Reviewers' : phase === 'plan' ? 'Planner' : phase === 'verify' ? 'Verifier' : 'Implementer'
function object(args: Record<string, unknown>, name: string) {
  if (args.path) return `${String(args.path).split(/[\\/]/).at(-1)}${args.start_line ? ` · ${args.start_line}–${args.end_line} 行` : ''}`
  return String(args.name || args.query || (name === 'run_registered_check' ? '登记检查' : ''))
}
function result(value: unknown): Record<string, unknown> {
  try { return typeof value === 'string' ? JSON.parse(value) : (value || {}) as Record<string, unknown> } catch { return { output: String(value) } }
}
export function projectActions(events: TraceEvent[]): Action[] {
  const actions: Action[] = []; const byId = new Map<string, Action>(); const legacy: Action[] = []
  for (const e of events) {
    if (e.event === 'tool_started' || e.event === 'verify_started') {
      const a: Action = { id: e.action_id || e.id, phase: e.phase || 'implement', agent: e.agent_id || 'legacy', name: e.name || 'run_registered_check', object: object(e.args || {}, e.name || ''), args: e.args || {}, status: 'running', output: '', started: e.time, path: e.args?.path as string | undefined, legacy: !e.action_id }
      if (a.args.old != null && a.args.new != null) a.diff = String(a.args.old).split('\n').map(l => '- ' + l).concat(String(a.args.new).split('\n').map(l => '+ ' + l)).join('\n')
      actions.push(a); byId.set(a.id, a); if (!e.action_id) legacy.push(a)
    } else {
      // Older parallel events have no correlation id. Only pair a unique open action;
      // never attach a result to whichever Agent happened to finish last.
      const candidates = legacy.filter(a => a.status === 'running' && a.name === e.name && (!e.phase || e.phase === a.phase))
      const a = e.action_id ? byId.get(e.action_id) : candidates.length === 1 ? candidates[0] : undefined
      if (!a) continue
      if (e.event === 'command_output') { a.output = (a.output + (e.output || '')).slice(-32000); continue }
      if (!['tool_finished', 'tool_failed', 'verify_finished'].includes(e.event)) continue
      const r = result(e.result)
      a.exit_code = typeof r.exit_code === 'number' ? r.exit_code : e.exit_code
      a.status = e.event === 'tool_failed' || r.ok === false || r.error || r.timed_out || (a.exit_code != null && a.exit_code !== 0) ? 'failed' : 'success'
      a.finished = e.time; a.duration = e.duration_seconds ?? Math.max(0, (Date.parse(e.time) - Date.parse(a.started)) / 1000)
      a.truncated = Boolean(r.truncated)
      a.output = String(r.output ?? r.content ?? r.error ?? e.error_type ?? (a.output || JSON.stringify(r, null, 2)))
    }
  }
  return actions
}
