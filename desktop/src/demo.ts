import type { RunDetail, RunSummary, TraceEvent } from './types'
// Explicit design preview, never mixed into live runtime responses.
const started = '2026-10-30T06:32:00Z'
export const demoRuns: RunSummary[] = [
  { id: 'issue-12-28', title: '为本地批次列表添加分页', repository: 'jobs', number: 12, url: 'https://github.com/example/jobs/issues/12', status: 'running', phase: 'implement', revision: 28, created_at: started, updated_at: started, requirements_revision: 3, branch: 'codex/issue-12-pagination' },
  { id: 'issue-11-27', title: '修复日期过滤边界', repository: 'jobs', number: 11, status: 'completed', phase: 'review', revision: 27, requirements_revision: 1, branch: 'codex/issue-11' },
  { id: 'issue-10-26', title: '补充配置说明', repository: 'repopilot', number: 10, status: 'completed', phase: 'review', revision: 26, requirements_revision: 1, branch: 'codex/issue-10' },
]
export function demoDetail(mode: string): RunDetail {
  const waiting = mode === 'waiting'
  return { ...demoRuns[0], status: waiting ? 'awaiting_approval' : 'running', phase: waiting ? 'publish' : 'implement', candidate: waiting ? '8f3a2c1d0b45e678901234567890123456789abc' : undefined,
    in_flight: waiting ? undefined : 'implement', pause_requested: false,
    verified: waiting ? [{ name: '分页与筛选', exit_code: 0, output_tail: '12 passed in 24.01s' }] : [],
    review: waiting ? { verdict: 'approved', findings: [], perspectives: { correctness: { verdict: 'approved' }, context: { verdict: 'approved' } } } : undefined,
    plan: { summary: '实现批次分页并保留筛选条件，补齐分页边界测试。' },
    requests: waiting ? [{ id: 'preview-approval', kind: 'approval', version: 'v3', summary: '需要你核对并批准发布', questions: ['是否批准此候选？'], context: {} }] : [],
    changes: { changed_files: ['src/jobs/commands/batches/pagination.py', 'tests/test_batch_pagination.py', 'tests/integration/test_batch_listing_with_filters.py'], additions: 84, deletions: 12,
      diff: 'diff --git a/src/jobs/commands/batches/pagination.py b/src/jobs/commands/batches/pagination.py\n@@ -57,1 +57,2 @@\n- rows = repository.list_batches()\n+ offset = (page - 1) * page_size\n+ rows = repository.list_batches(limit=page_size, offset=offset)' },
    capabilities: { pause: !waiting, decide: waiting, answer: false, publish: false, requirements_change: false, github_comments: false } }
}
const path = '.repopilot/workspaces/jobs/issue-12/src/jobs/commands/batches/pagination.py'
const events: TraceEvent[] = []
let seq = 0
function action(action_id: string, name: string, args: Record<string, unknown>, output?: Record<string, unknown>, seconds = 0.2) {
  events.push({ id: String(++seq), event: 'tool_started', action_id, name, args, phase: 'implement', time: started })
  if (output) events.push({ id: String(++seq), event: 'tool_finished', action_id, name, result: output, phase: 'implement', time: started, duration_seconds: seconds })
}
action('read', 'read_file', { path, start_line: 40, end_line: 92 }, { ok: true, content: '40: def list_batches(page=1, page_size=20):\n41:     """按页列出本地批次。"""\n42:     ...', truncated: false })
action('edit', 'replace_in_file', { path, old: 'rows = repository.list_batches()', new: 'offset = (page - 1) * page_size\nrows = repository.list_batches(limit=page_size, offset=offset)' }, { ok: true, replaced: 1 }, 0.4)
action('first', 'run_registered_check', { name: 'pytest · 第一次执行' }, { ok: true, exit_code: 1, output: '$ python -m pytest tests/test_batch_pagination.py -q\nFAILED test_page_two_preserves_filters\nE   AssertionError: expected [21, 22], got [1, 2]\n1 failed, 7 passed in 2.41s · exit 1' }, 2.4)
action('fix', 'replace_in_file', { path, old: 'offset = 0', new: 'offset = (page - 1) * page_size' }, { ok: true, replaced: 1 }, 0.3)
action('second', 'run_registered_check', { name: 'pytest · 第二次执行' })
events.push({ id: String(++seq), event: 'command_output', action_id: 'second', phase: 'implement', time: started,
  output: '$ python -m pytest tests/test_batch_pagination.py\n  tests/integration/test_batch_listing_with_filters.py -q\ncollected 12 items\ntests/test_batch_pagination.py ........  [ 66%]\ntests/integration/test_batch_listing_with_filters.py ..' })
export const demoEvents = events
