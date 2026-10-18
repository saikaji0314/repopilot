# Agent Loop 与停止条件
from copy import deepcopy
from pathlib import Path
import time
from collections import deque
from repopilot.legacy.agent.schemas import *
from repopilot.legacy.schemas import BaselineAnswer
from repopilot.legacy.agent.trace import *

# 只有这三类工具错误可以直接作为 Agent 的停止原因。
# file_not_found、test_timeout 等具体错误统一归为 TOOL_ERROR。
TOOL_ERROR_STOP_REASONS = {
    StopReason.UNKNOWN_TOOL.value: StopReason.UNKNOWN_TOOL,
    StopReason.INVALID_TOOL_ARGUMENTS.value: StopReason.INVALID_TOOL_ARGUMENTS,
    StopReason.TOOL_ERROR.value: StopReason.TOOL_ERROR,
}


class FakeChatModel:
    # search_code → read_file → run_test → final_answer
    '''
    [
        ModelTurn(
            content="我先搜索目标函数。",
            tool_calls=[
                ToolCall(
                    id="call-search-1",
                    name="search_code",
                    arguments_json=(
                        '{"query":"get_post",'
                        '"path":".",'
                        '"file_pattern":"*.py"}'
                    ),
                )
            ],
            finish_reason="tool_calls",
            usage=Usage(
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
            ),
        ),
        ModelTurn(
            content="我需要阅读搜索到的文件。",
            tool_calls=[
                ToolCall(
                    id="call-read-1",
                    name="read_file",
                    arguments_json=(
                        '{"path":"flaskr/blog.py",'
                        '"start_line":1,'
                        '"end_line":160}'
                    ),
                )
            ],
            finish_reason="tool_calls",
            usage=Usage(
                prompt_tokens=200,
                completion_tokens=20,
                total_tokens=220,
            ),
        ),
        ModelTurn(
            content="我需要运行相关测试。",
            tool_calls=[
                ToolCall(
                    id="call-test-1",
                    name="run_test",
                    arguments_json=(
                        '{"test_target":'
                        '"tests/test_blog.py::test_update"}'
                    ),
                )
            ],
            finish_reason="tool_calls",
            usage=Usage(
                prompt_tokens=300,
                completion_tokens=20,
                total_tokens=320,
            ),
        ),
        ModelTurn(
            content="""{
                "status": "diagnosed",
                "summary": "已经定位问题",
                "root_cause": "更新逻辑不符合预期",
                "evidence": [
                    {
                        "source": "flaskr/blog.py:80",
                        "observation": "目标逻辑位于 update 函数"
                    }
                ],
                "proposed_fix": [
                    "修改 update 函数中的处理逻辑"
                ],
                "verification": [
                    "重新运行 test_update"
                ],
                "risks": [
                    "可能影响已有更新行为"
                ]
            }""",
            tool_calls=[],
            finish_reason="stop",
            usage=Usage(
                prompt_tokens=400,
                completion_tokens=100,
                total_tokens=500,
            ),
        ),
    ]
    '''
    def __init__(self, turns: list[ModelTurn]):
        self._turns = deque(turns)
        self.received_messages = []
        self.received_tools = []
        self.received_tool_choices = []

    def complete(self, messages: list[dict], tools: list[dict], *, tool_choice: str | None = None) -> ModelTurn:
        self.received_messages.append(deepcopy(messages))
        self.received_tools.append(deepcopy(tools))
        self.received_tool_choices.append(tool_choice)
        if not self._turns:
            raise RuntimeError("FakeChatModel turns are empty")
        turn = ModelTurn.model_validate(self._turns.popleft())
        return turn


def agent_loop(chat_model, settings, initial_messages: list[dict], tool_registry, clock=time.monotonic, run_dir: Path | None = None, *, task_id: str | None = None) -> AgentResult:
    """run_dir 是完整输出路径，None 关闭保存；task_id 独立传入，不从路径推断。"""
    def _estimated_cost():
        return (
            usage.prompt_tokens * settings.input_price_per_million
            + usage.completion_tokens * settings.output_price_per_million
        ) / 1_000_000

    def _finish(agent_status: AgentStatus, stop_reason: StopReason, error: dict | None = None, answer: BaselineAnswer | None = None):
        agent_run_status.transition_to(agent_status)
        result = AgentResult(
            status=agent_run_status.status,
            stop_reason=stop_reason,
            model_steps=step_cnt,
            tool_calls=tool_cnt,
            usage=usage,
            messages=messages,
            answer=answer,
        )
        if run_dir:
            trace_data = result.model_dump()
            trace_data["estimated_cost_cny"] = _estimated_cost()
            trace_data["elapsed_seconds"] = clock() - start_time
            trace_data["error"] = error if result.status != AgentStatus.COMPLETED else None
            trace_data["final_answer_path"] = TraceFilename.FINAL_ANSWER if result.status == AgentStatus.COMPLETED else None
            save_trace(run_dir / TraceFilename.SUMMARY, trace_data)
            save_trace(run_dir / TraceFilename.MESSAGES, messages)
            save_trace_event(run_dir / TraceFilename.TRACE, TraceEvent.RUN_FINISHED, trace_data)
        return result

    def _is_run_timeout():
        return clock() - start_time >= settings.run_timeout_seconds

    agent_run_status = AgentRunStatus()
    start_time = clock()

    messages = deepcopy(initial_messages)
    usage = Usage()
    tool_cnt, step_cnt = 0, 0
    # 每种允许的“工具＋错误类型”各有一个纠错轮次，成功不重置。
    # 值记录首次出错的模型轮数，以便同轮多个相同错误一起回传。
    tool_argument_recovery_steps: dict[tuple[str, str], int] = {}
    final_answer_recovery_pending = False
    agent_run_status.transition_to(AgentStatus.RUNNING)
    if run_dir:
        save_trace_event(run_dir / TraceFilename.TRACE, TraceEvent.RUN_STARTED, {
            "task_id": task_id,
            "model": settings.model,
            "limits": {
                "max_steps": settings.max_steps,
                "max_tool_calls": settings.max_tool_calls,
                "max_total_tokens": settings.max_total_tokens,
                "max_cost": settings.max_cost,
                "run_timeout_seconds": settings.run_timeout_seconds
            }
        })

    while True:
        # run_timeout
        if _is_run_timeout():
            return _finish(AgentStatus.LIMIT_REACHED, StopReason.RUN_TIMEOUT, error={"stage": "run_timeout_before_step", "type": "TimeoutError", "message": "运行超时"})

        if step_cnt >= settings.max_steps:
            return _finish(AgentStatus.LIMIT_REACHED, StopReason.MAX_STEPS, error={"stage": "max_steps", "type": "LimitError", "message": "到达最大步数"})

        step_cnt += 1

        complete_time = clock()
        try:
            if final_answer_recovery_pending:
                # 与对照实验一致：沿用失败答案之前的证据历史，禁止再选工具。
                turn = chat_model.complete(messages[:-1], tool_registry.schema, tool_choice="none")
            else:
                turn = chat_model.complete(messages, tool_registry.schema)
        except Exception as e:
            return _finish(AgentStatus.FAILED, StopReason.MODEL_ERROR, error={
                "stage": "model_error", "type": "ModelError", 
                "message": redact_secrets(str(e), [settings.api_key])
            })
        
        if run_dir:
            save_trace_event(run_dir / TraceFilename.TRACE, TraceEvent.MODEL_TURN_COMPLETED, {
                "model_step": step_cnt,
                "final_answer_recovery": final_answer_recovery_pending,
                "elapsed_seconds": clock() - complete_time,
                "response": ModelTurn.model_dump(turn),
                "cumulative_usage": Usage.model_dump(usage + turn.usage),
            })

        # 追加模型自己的消息
        messages.append({"role": "assistant", 
                         "content": turn.content, 
                         "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.name,
                                    "arguments": call.arguments_json,
                                },
                            }
                            for call in turn.tool_calls
                        ]})

        usage += turn.usage

        # run_timeout
        if _is_run_timeout():
            return _finish(AgentStatus.LIMIT_REACHED, StopReason.RUN_TIMEOUT, error={"stage": "run_timeout_after_step", "type": "TimeoutError", "message": "运行超时"})

        # step
        if step_cnt >= settings.max_steps and turn.tool_calls:  # 到达最大步数且有工具调用
            return _finish(AgentStatus.LIMIT_REACHED, StopReason.MAX_STEPS, error={"stage": "max_steps", "type": "LimitError", "message": "到达最大步数"})
        
        # 是否到达token限制
        if usage.total_tokens >= settings.max_total_tokens:
            return _finish(AgentStatus.LIMIT_REACHED, StopReason.TOKEN_BUDGET, error={"stage": "max_total_tokens", "type": "LimitError", "message": "到达最大token数"})
        
        # cost限制
        if _estimated_cost() >= settings.max_cost:
            return _finish(AgentStatus.LIMIT_REACHED, StopReason.COST_BUDGET, error={"stage": "max_cost", "type": "LimitError", "message": "到达最大成本"})

        if final_answer_recovery_pending and turn.tool_calls:
            return _finish(AgentStatus.FAILED, StopReason.INVALID_FINAL_ANSWER, error={
                "stage": "final_answer_recovery_tool_calls", "type": "InvalidFinalAnswerError",
                "message": "最终答案纠错请求仍返回工具调用",
            })
        
        # tool call
        if turn.tool_calls:
            for tool_call in turn.tool_calls:
                if tool_cnt >= settings.max_tool_calls:
                    return _finish(AgentStatus.LIMIT_REACHED, StopReason.MAX_TOOL_CALLS, error={"stage": "max_tool_calls", "type": "LimitError", "message": "到达最大工具调用次数"})
                tool_cnt += 1

                if run_dir:
                    save_trace_event(run_dir / TraceFilename.TRACE, TraceEvent.TOOL_CALL_STARTED, {
                        "model_step": step_cnt,
                        "tool_call_id": tool_call.id,
                        "tool": tool_call.name,
                        "arguments_json": tool_call.arguments_json,
                    })

                tool_execute_time = clock()
                try:
                    tool_result = tool_registry.execute(name=tool_call.name, arguments=tool_call.arguments_json)
                except RuntimeError as e:
                    if run_dir:
                        tool_result = ToolResult(
                            ok=False,
                            tool=tool_call.name,
                            data=None,
                            error=ToolError(
                                code="tool_runtime_error",
                                message=redact_secrets(str(e), [settings.api_key]),
                            ),
                        )
                        messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": ToolResult.model_dump_json(tool_result)})
                        save_trace_event(run_dir / TraceFilename.TRACE, TraceEvent.TOOL_CALL_COMPLETED, {
                            "model_step": step_cnt,
                            "tool_call_id": tool_call.id,
                            "elapsed_seconds": clock() - tool_execute_time,
                            "result": ToolResult.model_dump(tool_result),
                        })
                    return _finish(AgentStatus.FAILED, StopReason.TOOL_ERROR, error={
                        "stage": "tool_runtime_error", "type": "ToolError", 
                        "message": redact_secrets(str(e), [settings.api_key])
                    })
                if run_dir:
                    save_trace_event(run_dir / TraceFilename.TRACE, TraceEvent.TOOL_CALL_COMPLETED, {
                        "model_step": step_cnt,
                        "tool_call_id": tool_call.id,
                        "elapsed_seconds": clock() - tool_execute_time,
                        "result": ToolResult.model_dump(tool_result),
                    })

                # 追加工具消息
                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": ToolResult.model_dump_json(tool_result)})
                # 处理可能的工具错误
                if not tool_result.ok:
                    recovery_key = (tool_call.name, tool_result.error.code)
                    if (
                        recovery_key in {
                            ("run_test", "unexpected_test_arguments"),
                            ("search_code", "not_a_directory"),
                        }
                        and tool_argument_recovery_steps.get(recovery_key) in (None, step_cnt)
                    ):
                        tool_argument_recovery_steps[recovery_key] = step_cnt
                        # 错误已写入 messages 和 Trace；先补齐本轮其余工具结果，
                        # 再由外层循环请求模型纠错，不额外重试工具或重置预算。
                        continue
                    return _finish(AgentStatus.FAILED, TOOL_ERROR_STOP_REASONS.get(
                            tool_result.error.code, StopReason.TOOL_ERROR
                        ), error={"stage": "tool_notok_error", "type": "ToolError", "message": str(tool_result.error)})

        else:
            # 校验answer
            try:
                answer = BaselineAnswer.model_validate_json(turn.content)
            except ValueError as e:
                if not final_answer_recovery_pending:
                    final_answer_recovery_pending = True
                    continue
                return _finish(AgentStatus.FAILED, StopReason.INVALID_FINAL_ANSWER, error={
                    "stage": "final_answer_error", "type": "InvalidFinalAnswerError", 
                    "message": redact_secrets(str(e), [settings.api_key])
                })
            # 保存答案、轨迹
            if run_dir:
                save_trace(run_dir / TraceFilename.FINAL_ANSWER, answer)
            return _finish(AgentStatus.COMPLETED, StopReason.FINAL_ANSWER, answer=answer)
