import { Button as PrimerButton } from '@primer/react'
import type { ComponentProps, ReactNode } from 'react'

const assets = import.meta.glob('./assets/*.svg', { eager: true, query: '?url', import: 'default' }) as Record<string, string>
export function Icon({ name, className = '' }: { name: string; className?: string }) {
  const key = './assets/imgOcticon' + name + '.svg'
  return <img className={'icon ' + className} src={assets[key]} alt="" aria-hidden="true" />
}
export function Button({ children, ...props }: ComponentProps<typeof PrimerButton>) {
  return <PrimerButton {...props} className={'rp-button ' + (props.className || '')}>{children}</PrimerButton>
}
export function StatusIcon({ status }: { status: string }) {
  return <Icon name={status === 'success' ? 'CheckCircleFill' : status === 'failed' ? 'XCircleFill' : status === 'running' ? 'Sync' : status === 'waiting' ? 'Clock' : 'Circle'} />
}
export const isWaiting = (s: string) => ['awaiting_approval', 'needs_input', 'recovery_required', 'rework_limit_reached'].includes(s)
export const statusText = (s: string) => ({ running: '运行中', awaiting_approval: '等待人工', needs_input: '等待答复', recovery_required: '需要核对现场', completed: '已完成', published: '已发布草稿', merged: '已合并', approved: '已批准发布', paused: '已暂停', ready: '就绪', rejected: '已要求修改', limit_reached: '达到运行上限', failed: '执行失败' }[s] || s)
export const statusKind = (s: string) => s === 'running' ? 'running' : isWaiting(s) || s === 'paused' ? 'waiting' : ['completed', 'published', 'merged', 'approved'].includes(s) ? 'success' : ['ready', 'unknown'].includes(s) ? 'idle' : 'failed'
export function StateLabel({ status }: { status: string }) { return <span className={'state-label ' + statusKind(status)}><StatusIcon status={statusKind(status)} />{statusText(status)}</span> }
export function Empty({ title, children }: { title: string; children?: ReactNode }) { return <div className="empty"><Icon name="Repo1" /><h2>{title}</h2><p>{children}</p></div> }
export function Output({ value, diff = false }: { value: string; diff?: boolean }) {
  return <div className={'output ' + (diff ? 'diff' : '')}>{value.split('\n').map((line, i) => <div className={'output-line ' + (diff && line.startsWith('+') ? 'added' : diff && line.startsWith('-') ? 'removed' : /FAILED|AssertionError|exit 1|^E\s/.test(line) ? 'error-line' : '')} key={i}><span className="line-number">{i + 1}</span><code>{line || ' '}</code></div>)}</div>
}
export async function openExternal(url: string) {
  if (!/^https:\/\//.test(url)) return
  if (window.repopilot) await window.repopilot.openExternal(url)
  else window.open(url, '_blank', 'noopener,noreferrer')
}
export function download(name: string, value: string) {
  const url = URL.createObjectURL(new Blob([value], { type: 'text/plain;charset=utf-8' }))
  const a = document.createElement('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
}
