# 模型配置与兼容边界

同一次任务的实现与审核使用同一模型配置。模型工厂位于 `repopilot/agents/models.py`；业务编排、文件工具、预算、人工审批与 PR 规则不依赖供应商 SDK。没有自动切换模型或隐藏的 SDK 重试。

| provider | 适配器与参数 | 当前验证 |
| --- | --- | --- |
| `deepseek`（默认） | ChatDeepSeek；保留思考字段回传和工具选择兼容处理 | 既有真实交付与 5 题开发基线；本增量离线回归 |
| `qwen` | ChatOpenAI 的 Chat Completions 接口，显式 `enable_thinking=false` | 合成 HTTP 响应验证 SDK、工具往返、用量及完整审批前工作流；未调用真实 Qwen API |
| `openai-compatible` | Chat Completions，`max_tokens`；不注入供应商思考字段 | 同上；不保证任意兼容服务均支持工具选择和用量 |
| `anthropic` | 原生 ChatAnthropic，标准工具调用，不启用扩展思考 | 真实适配器请求构造、合成原生响应转换、工具往返及完整审批前工作流；未验证真实 HTTP/API |

“离线通过”证明接线与协议处理，不证明认证、服务端参数兼容性、模型效果或实际价格。Qwen 此入口面向支持关闭思考与工具调用的模型；不适用于强制思考的模型。Claude 的 Bedrock／Vertex 接口不在范围内。

## 配置

原有 `.env` 中 `DS_API_KEY / DS_BASE_URL / DS_MODEL / DS_THINKING / DS_REASONING_EFFORT` 保持有效，不必迁移。`REPOPILOT_PROVIDER` 缺省为 `deepseek`。以下通用变量在 DeepSeek 下覆盖对应 DS 值；其他供应商不读取 DS 密钥、模型名或端点。

```dotenv
REPOPILOT_PROVIDER=qwen
REPOPILOT_API_KEY=填写自己的密钥
REPOPILOT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
REPOPILOT_MODEL=填写所在区域可用且支持工具的模型ID
REPOPILOT_INPUT_CNY_PER_MILLION=填写保守估算单价
REPOPILOT_OUTPUT_CNY_PER_MILLION=填写保守估算单价
```

示例中的文字占位符必须替换；端点按账号区域核对。兼容服务选择 `openai-compatible` 并填写其 HTTPS Chat Completions base URL。Claude 先安装可选依赖：

```shell
python -m pip install -c constraints.txt -e ".[anthropic]"
```

然后设置 `REPOPILOT_PROVIDER=anthropic`、`REPOPILOT_BASE_URL=https://api.anthropic.com`，其余变量同上。模型 ID 由操作者填写，不由项目猜测或追随 latest 别名。端点不接受 URL 内凭证、查询参数或 HTTP 明文。环境变量优先于 `.env` 中同名变量，读取不修改进程环境。

非 DeepSeek 必须显式给出模型、端点及两项正数人民币单价，不能静默套用 DeepSeek 默认价格。价格只是预算估计，不等于实付账单；缓存等折扣不计。缺失 token 用量仍停止并保留未知费用。所有供应商使用统一输出、上下文、调用次数和时间限制；非 DeepSeek 不接受 `thinking=enabled`，请勿直接套用启用思考的 DeepSeek profile。输出上限通过 profile 的 `max_output_tokens` 设置，当前最大 8192。

## 恢复与审计

任务创建时冻结 provider、模型、端点、估算价格与执行限制，状态中不保存密钥。resume／retry 只在当前 provider 与端点匹配时注入当前密钥，允许轮换同端点密钥；不匹配时返回 `model_credentials_endpoint_mismatch`，须切回原供应商配置再恢复。仅修改当前模型名不会切换旧任务的冻结模型。旧任务缺少 provider 字段时按 DeepSeek 解释。

运行版本记录选择实际供应商集成包。profile／retry overrides 不接受凭证、provider、模型、端点或价格；要换模型开始新任务。累计预算账本继续共享，不因换供应商清零。实现报告仍走 ToolStrategy 工具结构化输出，不依赖各家 JSON Schema 原生输出能力。Claude 的 `max_tokens` 停止原因与兼容接口的 `length` 均按输出超限处理，在执行其工具前停止。

协议依据：[LangChain 集成边界](https://reference.langchain.com/python/integrations/overview)、[ChatAnthropic 接口](https://reference.langchain.com/python/langchain-anthropic/chat_models/ChatAnthropic)、[Qwen 思考开关](https://docs.qwencloud.com/resources/faq-text-generation)。兼容 API 不等于所有供应商扩展参数兼容，因此扩展字段只由对应适配器注入。
