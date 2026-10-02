# 单次 LLM 基线的模型选择

> 状态：模型方案已确定，API Key 和脚本尚未配置  
> 主模型：`qwen3.7-plus-2026-05-26`

## 1. 最终建议

如果项目在中国大陆开发，单次 LLM 主基线使用阿里云百炼北京地域的：

```text
qwen3.7-plus-2026-05-26
```

不使用会自动更新的 `qwen3.7-plus` 别名作为评测模型。官方文档说明该别名当前等同于上述快照，但别名未来可能更新；评测必须固定快照，才能判断结果变化来自代码还是模型。

官方资料：

- 模型选择：<https://help.aliyun.com/zh/model-studio/text-generation-model>
- 工具调用：<https://help.aliyun.com/zh/model-studio/qwen-function-calling>
- 结构化输出：<https://help.aliyun.com/zh/model-studio/qwen-structured-output>
- 模型价格：<https://help.aliyun.com/zh/model-studio/model-pricing>

## 2. 为什么选 Qwen3.7-Plus

### 能力与成本平衡

阿里云官方将 `qwen3.7-plus` 推荐为 AI 编程和 Agent 开发的平衡选择，支持完整工具调用，并提供足够大的上下文。它适合先完成单次调用基线，后续不更换模型直接进入 Agent Loop。

### 支持后续 Agent 核心能力

同一模型系列支持：

- Function Calling；
- 结构化 JSON 输出；
- 思考模式；
- 长上下文；
- OpenAI 兼容接口；
- Responses API。

因此第 0 期写的模型适配层可以在第 1 期继续使用，避免为了工具调用重新更换供应商和 SDK。

### 在预算内留有很大余量

官方当前中国大陆标准原价在单次输入不超过 256K Token 时为：

| 模型 | 输入/百万 Token | 输出/百万 Token |
| --- | ---: | ---: |
| `qwen3.7-plus-2026-05-26` | 2 元 | 8 元 |

官方页面还列出了限时折扣和新开通后的免费额度，但项目预算按原价计算，不能依赖促销。

假设一次任务输入 20,000 Token、输出（含思考 Token）2,000 Token，原价估算为：

```text
20,000 / 1,000,000 × 2
+ 2,000 / 1,000,000 × 8
= 0.056 元/次
```

6 条任务各运行 2 次约为 0.672 元。实际输入、思考长度和输出会不同，因此脚本必须记录供应商返回的真实 usage 和费用估算，不能把这个示例当作最终账单。

## 3. 对照模型

主基线只使用一个模型，避免混淆结果。以下模型只承担明确的对照角色：

| 角色 | 模型 | 何时使用 |
| --- | --- | --- |
| 主基线与第 1 期 Agent | `qwen3.7-plus-2026-05-26` | 6 条任务和首个 Agent Loop |
| 质量上限对照 | `qwen3.7-max-2026-06-08` | 主基线完成后，每条任务最多运行 1 次，可选 |
| 成本优化对照 | `qwen3.6-flash-2026-04-16` | Agent 达到质量目标后再比较，可选 |

不能用 Plus 做单次调用、再用 Max 做 Agent，然后声称效果提升来自 Agent。主要对比必须使用同一模型快照、同一任务和同一输出标准。

### DeepSeek V4 Pro 与 Flash 对比

以下为选型阶段记录的百炼北京地域信息，仅用于解释当时决策；模型能力与价格可能变化，实际调用前需重新核对官方说明：

| 维度 | `qwen3.7-plus-2026-05-26` | `deepseek-v4-pro` | `deepseek-v4-flash-0731` |
| --- | --- | --- | --- |
| 官方定位 | AI 编程与 Agent 的平衡选择 | 复杂推理、专业代码、长文本分析、深度 Agent | 高并发、低延迟、轻量任务、基础 RAG |
| 最大输入 | 1M Token | 1M Token | 1M Token |
| Function Calling | 支持 | 支持 | 支持 |
| 百炼结构化输出 | 支持 | 不支持 | 不支持 |
| 思考模式 | 混合思考，默认开启 | 混合思考，默认关闭 | 混合思考，默认关闭 |
| 北京区输入原价 | 2 元/百万 Token（不超过 256K） | 12 元/百万 Token | 1 元/百万 Token |
| 北京区输出原价 | 8 元/百万 Token（不超过 256K） | 24 元/百万 Token | 2 元/百万 Token |
| 本项目角色 | 主基线 | 可选的复杂任务质量对照 | 可选的成本/速度对照 |

官方资料：

- DeepSeek V4 Pro：<https://help.aliyun.com/zh/model-studio/deepseek-v4-pro>
- DeepSeek V4 Flash：<https://help.aliyun.com/zh/model-studio/deepseek-v4-flash>
- 思考模式：<https://help.aliyun.com/en/model-studio/deep-thinking>

以“输入 20,000 Token、输出含思考 2,000 Token”为例，且不考虑缓存：

| 模型 | 估算成本/次 | 相对 Flash |
| --- | ---: | ---: |
| `deepseek-v4-flash-0731` | 0.024 元 | 1 倍 |
| `qwen3.7-plus-2026-05-26` | 0.056 元 | 约 2.33 倍 |
| `deepseek-v4-pro` | 0.288 元 | 12 倍 |

这只是相同 Token 数下的单价比较，不代表实际账单一定保持该倍数；不同模型生成的思考和答案长度可能不同。

本项目暂不更换主模型，原因是：

1. `deepseek-v4-pro` 更适合检验疑难代码任务的质量上限，但价格明显更高，而且百炼当前不支持结构化输出；
2. `deepseek-v4-flash-0731` 非常便宜，适合在主流程跑通后检验“低成本模型是否够用”，但官方定位偏轻量任务，不宜在尚无实测数据时承担复杂代码诊断主基线；
3. `qwen3.7-plus-2026-05-26` 同时满足编程/Agent 定位、固定快照、Function Calling 和结构化输出，最适合公平比较“单次 LLM”与“Agent Loop”。

如果时间允许，只增加一组跨模型实验：从 6 条任务里选择 2 条（一条简单、一条困难），让 Pro 和 Flash 各运行 1 次。该结果单独标记为“模型对照”，不能混入单次基线与 Agent 的核心对比。

注意：`deepseek-v4-flash` 当前对应预览版；正式评测应使用官方推荐的稳定快照 `deepseek-v4-flash-0731`。如果未来测试 DeepSeek，两种模型都不要启用百炼 `response_format: json_object`，而应让提示词要求 JSON、保留原始响应，再由 Pydantic 做宽松提取与严格校验。格式失败需要单独计分，不能静默调用另一个模型修复。

## 4. OpenAI 模型的条件性备选

如果开发者位于 OpenAI API 官方支持地区，并已有可正常使用的 API 账号，可将 `gpt-5.6-terra` 作为同档备选。OpenAI 官方把它定位为能力与成本的平衡型号，并建议 Agent 工作流使用 Responses API。

官方资料：

- 模型指南：<https://developers.openai.com/api/docs/guides/latest-model>
- 模型选择原则：<https://developers.openai.com/api/docs/guides/model-selection>
- API 价格：<https://developers.openai.com/api/docs/pricing>
- 支持地区：<https://developers.openai.com/api/docs/supported-countries>

OpenAI 当前支持地区列表没有中国大陆，并提示从列表之外访问服务可能导致账号被阻止或暂停。因此本项目不把 OpenAI 直连 API 作为中国大陆开发环境的默认方案，也不以绕过地区限制作为项目依赖。

## 5. 基线调用配置

建议将以下配置作为第 0 期基线协议，而不是散落在代码中：

```yaml
provider: alibaba_model_studio
region: cn-beijing
api_style: openai_compatible
model: qwen3.7-plus-2026-05-26
thinking_mode: enabled
response_format: json_object
repetitions_per_task: 2
timeout_seconds: 120
tools: []
network_tools: []
```

实现注意事项：

- API Key 只从 `DASHSCOPE_API_KEY` 环境变量读取；
- 提示词中明确要求返回 JSON，否则 JSON Mode 会报错；
- 不把 API Key、请求头或完整环境变量写入日志；
- 对输出执行 Pydantic/JSON Schema 校验；
- 结构化输出时不主动设置过小的 `max_tokens`，避免 JSON 被截断；
- 不因答案错误自动重试；只对明确的限流、超时等传输错误按规则重试；
- 每次调用保存模型快照、任务 ID、运行编号、耗时、usage 和原始响应；
- 思考模式如果出现非标准 JSON，应记录为格式失败，不额外调用另一个模型偷偷修复。

在正式写脚本时，需要用 1 条任务先验证“思考模式 + JSON Mode”的实际接口行为。如果该组合在固定快照上不能稳定返回合法 JSON，则保留同一模型快照，将主基线统一改为非思考模式，并把这个决定记录在本文中。

## 6. 单次调用到底给模型看什么

单次 LLM 基线没有搜索和文件读取工具，因此每次请求应包含：

1. 固定的系统提示词；
2. Agent 可见任务输入；
3. 缺陷快照中的完整文本代码、SQL、模板和测试；
4. 文件之间使用明确的路径分隔；
5. 结构化输出字段说明。

不应由人工预先挑选“最相关的三个文件”，否则人工检索能力会被错误算进单次 LLM 基线。Flaskr 规模约 600 行 Python，完整仓库快照足以放入上下文。

应排除：

- `.pyc`、缓存、数据库文件；
- `.git` 元数据；
- 评测者答案和评分规则；
- 正确修复补丁；
- 其他任务的缺陷信息。

## 7. 公平比较规则

第 0 期单次调用和第 1 期 Agent 的主要比较必须保持：

- 同一个模型快照；
- 同一思考模式；
- 同一任务输入；
- 同一输出字段；
- 同一评分器和通过线；
- 都不看到评测者答案。

唯一主要变量是：

```text
单次基线：一次请求，直接获得完整仓库快照，没有工具
Agent 版本：通过受控工具主动搜索、读取和运行测试，可进行多步调用
```

需要分别记录模型质量和系统成本。Agent 即使成功率更高，也可能花费更多调用、Token 和时间，这些都是结论的一部分。

## 8. 本阶段不做的事

- 不同时接入三家模型供应商；
- 不把免费额度作为预算前提；
- 不使用会自动升级的模型别名做正式评测；
- 不用大模型作为唯一评分器；
- 不在第 0 期引入模型路由或自动降级；
- 不为了获得更好分数而给单次基线泄露相关文件或根因。

## 9. 下一步

在 6 条任务中先完成 1 条，使用该任务验证 API Key、结构化输出、usage 记录和单次成本。验证通过后再实现批量基线脚本，避免一开始就在尚未确认的接口上批量消耗预算。
