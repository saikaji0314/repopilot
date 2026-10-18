# 增量16：多模型入口与离线兼容验证

日期：2026-10-18。验收目标：保持原有 DeepSeek 行为，提供 Qwen／Chat Completions 兼容入口与 Claude 原生接口；验证工具循环、用量、超限、凭证冻结与审批边界，明确未做真实服务验证。

## 交付

- `agents/models.py` 集中供应商适配；`loop.py` 保留旧工厂与类导出，现有导入和模型注入不变。DeepSeek 的思考字段处理保留；兼容接口使用 `max_tokens`，Qwen 显式关闭思考；Claude 使用可选 `langchain-anthropic`。
- [模型配置](MODELS.md)与 `.env.example` 提供入口。非 DeepSeek 必须显式指定模型、HTTPS 端点和两项正数人民币 token 估算单价，不借用 DS 密钥、价格或思考参数。SDK 重试为 0，无自动跨模型回退。
- resume／retry 校验当前供应商和端点，再注入当前密钥；冻结模型及价格，支持同端点密钥轮换。旧任务缺少 provider 字段按 DeepSeek 解释。retry 的覆盖项也受执行参数白名单约束，不能改供应商、端点或凭证。
- 运行输入记录实际供应商集成包版本。Claude 原生 `stop_reason=max_tokens` 按输出超限处理，仍先计量，不执行被截断响应中的工具。
- [Harness 诊断](HARNESS-FINDINGS.md)根据增量15逐题证据区分观察、推测和待测优化。本轮没有更改搜索工具、阶段预算、任务记忆或提示词来挑高原有基线分数。

## 实际验证

测试环境为 CPython 3.12、Windows，依赖遵循根目录 constraints；新增可选集成为 langchain-anthropic 1.7.6、anthropic 1.15.0。所有模型响应均为合成数据，本轮模型 API 调用为 0。

| 检查 | 结果 |
| --- | --- |
| `pytest tests/runtime -q -p no:cacheprovider` | 216 passed、7 skipped，109.33s |
| `pytest tests --ignore=tests/runtime -q -p no:cacheprovider` | 234 passed、4 skipped，27.94s |
| 最终 DS 参数隔离调整后的 `test_models.py / test_config.py` | 17 passed，9.57s |
| 新增入口经真实适配器与 LangChain 工具循环，执行本地 Git、登记检查、独立审核 | openai-compatible／qwen／anthropic 均到 awaiting_approval，4 次模型响应，未审批、未推送 |
| DeepSeek 旧配置、旧任务恢复、密钥轮换、端点不匹配拒绝、缺失价格／密钥拒绝、可选依赖缺失提示 | 离线回归通过 |
| 兼容接口 HTTP 请求与工具往返 | 真实 SDK 经 MockTransport，检查请求字段、tool_call_id、工具参数与 token 用量 |
| Claude 原生协议 | 真实 ChatAnthropic 构造请求与转换合成原生 Message，验证 tool_use／tool_result、结构化报告及用量；SDK HTTP 边界未实测 |
| 新建虚拟环境，从本地 wheel 安装含 anthropic extra 的完整依赖 | 安装成功，pip check 无冲突；项目原环境 pip check 也无冲突 |
| 在源码目录外，使用 wheel 安装后的包与 CLI | 4 适配器构造请求、CLI --help 通过；确认导入来自新环境 site-packages |
| wheel 内容、compileall、git diff --check | 新适配文件、CSS、worker 存在；无 .local、.env 或 pycache；其余检查通过 |

两组回归合计 **450 passed、11 skipped**，不是另有 450 项真实模型验证。原始测试、安装和构建日志保留在被忽略的本地证据目录，不提交。Docker 专项与新 SWE-bench 推理本轮未重跑；之前的真实结果仍见[增量15](VALIDATION-15.md)。

## 限制

Qwen、Claude 和任意第三方兼容服务的真实鉴权、服务端参数支持、模型修复效果、价格均未实测，不据此宣称生产可用或比原模型更好。统一输出额度当前最多 8192；仅 DeepSeek 开放思考选项，不支持各家任意扩展参数、流式调用、模型路由、不同角色不同模型或 Claude 云托管接口。

本轮只验证 wheel 可安装与入口可导入，不是发行验收；默认 `.env` 与运行目录仍跟随现有源码根目录定位，安装模式下的配置发现和可写数据目录应在发行准备阶段整理，许可及发布元数据也未定。没有发布 release、合并主分支、清零累计预算或修改用户真实 `.env`。
