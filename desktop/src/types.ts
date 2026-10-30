export type RunStatus = string
export interface RunSummary {
  id: string; title: string; repository: string; number?: number; url?: string;
  status: RunStatus; phase: string; revision: number; updated_at?: string; created_at?: string;
  candidate?: string; requirements_revision: number; branch: string
}
export interface HumanRequest { id: string; kind: string; summary: string; questions: string[]; version: string; context: Record<string, unknown> }
export interface Check { name: string; exit_code?: number; output_tail?: string; error?: string }
export interface RunDetail extends RunSummary {
  in_flight?: string; stop_reason?: string; pause_requested: boolean;
  stage_status?: Record<string, string>;
  verified: Check[]; review?: { verdict?: string; findings?: Record<string, unknown>[]; perspectives?: Record<string, { verdict?: string }> };
  plan?: { summary?: string; subtasks?: { id: string; title: string }[] };
  requests: HumanRequest[]; approval?: { approved: boolean; commit: string; reason: string };
  changes: { changed_files?: string[]; additions?: number; deletions?: number; diff?: string; truncated?: boolean };
  capabilities: { pause: boolean; decide: boolean; answer: boolean; publish: boolean; requirements_change: boolean; github_comments: boolean }
}
export interface TraceEvent {
  id: string; event: string; time: string; phase?: string; name?: string; action_id?: string;
  agent_id?: string; subtask_id?: string; args?: Record<string, unknown>; result?: unknown;
  error_type?: string; output?: string; duration_seconds?: number; exit_code?: number
}
export interface Action {
  id: string; phase: string; agent: string; name: string; object: string;
  status: 'running' | 'success' | 'failed' | 'unknown'; args: Record<string, unknown>;
  output: string; diff?: string; path?: string; started: string; finished?: string; duration?: number;
  truncated?: boolean; exit_code?: number; legacy?: boolean
}
export interface QueueItem { id: string; repository: string; status: string; run_id?: string; error?: string }
export type Operation = { revision: number; commit?: string; approved?: boolean; reason?: string; request_id?: string; version?: string }
export interface PanelPreferences { sidebarWidth: number; agentWidth: number; sidebarHidden: boolean; agentHidden: boolean }
export interface DesktopBridge {
  bootstrap(): Promise<{ token: string; runtime: string }>;
  window(action: 'minimize' | 'maximize' | 'close'): void;
  openExternal(url: string): Promise<void>;
  getPreferences?(): Promise<PanelPreferences>;
  savePreferences?(preferences: PanelPreferences): Promise<void>;
  chooseRuntime?(): Promise<void>;
}
declare global { interface Window { repopilot?: DesktopBridge } }
