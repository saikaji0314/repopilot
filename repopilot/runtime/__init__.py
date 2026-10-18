"""兼容历史 runtime 导入；新代码直接使用按职责划分的包。"""

from importlib import import_module
import sys

MODULES = {'agent': 'repopilot.agents.loop',
 'budget': 'repopilot.storage.budget',
 'config': 'repopilot.core.config',
 'execution': 'repopilot.orchestration.session',
 'github': 'repopilot.delivery.github',
 'health': 'repopilot.delivery.health',
 'memory': 'repopilot.memory.store',
 'memory_cli': 'repopilot.memory.cli',
 'memory_context': 'repopilot.memory.context',
 'memory_facts': 'repopilot.memory.facts',
 'phases': 'repopilot.orchestration.phases',
 'report': 'repopilot.presentation.report',
 'repository': 'repopilot.execution.repository',
 'run_state': 'repopilot.orchestration.state',
 'sandbox': 'repopilot.execution.sandbox',
 'sandbox_archive': 'repopilot.execution.archive',
 'sandbox_process': 'repopilot.execution.transport',
 'sandbox_worker': 'repopilot.execution.worker',
 'file_access': 'repopilot.execution.files',
 'store': 'repopilot.storage.jobs',
 'watcher': 'repopilot.delivery.watcher',
 'workflow': 'repopilot.orchestration.workflow',
 'workspace': 'repopilot.execution.workspace'}

for _old, _new in MODULES.items():
    _module = import_module(_new)
    sys.modules[f"{__name__}.{_old}"] = _module
    globals()[_old] = _module
