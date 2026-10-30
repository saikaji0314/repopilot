import { useEffect, useState, type CSSProperties, type PointerEvent } from 'react'
import type { PanelPreferences } from './types'

const defaults: PanelPreferences = { sidebarWidth: 216, agentWidth: 224, sidebarHidden: false, agentHidden: false }
export function usePanels(agentVisible: boolean) {
  const [viewport, setViewport] = useState(window.innerWidth)
  const [panels, setPanels] = useState<PanelPreferences>(() => {
    try { return { ...defaults, ...JSON.parse(localStorage.getItem('repopilot.panels') || '{}') } } catch { return defaults }
  })
  const [loaded, setLoaded] = useState(!window.repopilot?.getPreferences)
  useEffect(() => { const resize = () => setViewport(window.innerWidth); window.addEventListener('resize', resize); return () => window.removeEventListener('resize', resize) }, [])
  useEffect(() => { let disposed = false; window.repopilot?.getPreferences?.().then(value => { if (!disposed) { setPanels({ ...defaults, ...value }); setLoaded(true) } }).catch(() => setLoaded(true)); return () => { disposed = true } }, [])
  useEffect(() => {
    if (!loaded) return
    const timer = setTimeout(() => { localStorage.setItem('repopilot.panels', JSON.stringify(panels)); void window.repopilot?.savePreferences?.(panels).catch(() => {}) }, 180)
    return () => clearTimeout(timer)
  }, [panels, loaded])
  useEffect(() => { const fn = (event: KeyboardEvent) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'b' && !event.altKey) { event.preventDefault(); setPanels(p => ({ ...p, sidebarHidden: !p.sidebarHidden })) } }; window.addEventListener('keydown', fn); return () => window.removeEventListener('keydown', fn) }, [])
  const sidebarMax = Math.max(180, Math.min(420, viewport - (agentVisible && !panels.agentHidden ? 180 : 0) - 540))
  const sidebarWidth = Math.max(180, Math.min(sidebarMax, Number(panels.sidebarWidth) || defaults.sidebarWidth))
  const agentMax = Math.max(180, Math.min(400, viewport - (panels.sidebarHidden ? 0 : sidebarWidth) - 540))
  const agentWidth = Math.max(180, Math.min(agentMax, Number(panels.agentWidth) || defaults.agentWidth))
  return { panels, setPanels, sidebarWidth, agentWidth, sidebarMax, agentMax, style: { '--sidebar-width': `${sidebarWidth}px`, '--agent-width': `${agentWidth}px` } as CSSProperties }
}

export function ResizeHandle({ label, width, minimum, maximum, reset, onChange }: { label: string; width: number; minimum: number; maximum: number; reset: number; onChange: (value: number) => void }) {
  function clamp(value: number) { return Math.round(Math.max(minimum, Math.min(maximum, value))) }
  function pointer(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return
    const handle = event.currentTarget; const start = event.clientX; const initial = width
    handle.setPointerCapture(event.pointerId); document.documentElement.classList.add('resizing-panel')
    const move = (e: globalThis.PointerEvent) => onChange(clamp(initial + e.clientX - start))
    const end = () => { handle.removeEventListener('pointermove', move); handle.removeEventListener('pointerup', end); handle.removeEventListener('lostpointercapture', end); document.documentElement.classList.remove('resizing-panel') }
    handle.addEventListener('pointermove', move); handle.addEventListener('pointerup', end); handle.addEventListener('lostpointercapture', end)
    event.preventDefault()
  }
  return <div className="resize-handle" role="separator" aria-label={label} aria-orientation="vertical" aria-valuemin={minimum} aria-valuemax={maximum} aria-valuenow={width} tabIndex={0} onPointerDown={pointer} onDoubleClick={() => onChange(reset)} onKeyDown={e => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return
    e.preventDefault(); onChange(e.key === 'Home' ? minimum : e.key === 'End' ? maximum : clamp(width + (e.key === 'ArrowLeft' ? -1 : 1) * (e.shiftKey ? 32 : 8)))
  }} title="拖动调整宽度；双击恢复默认；方向键微调" />
}
