# Agent 专用 Prompt，不复用基线 Prompt
from repopilot.task_loader import get_agent_prompt


def build_initial_messages(task: dict, system_prompt: str):
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": get_agent_prompt(task)},
    ]
