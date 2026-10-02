# 任务样例、输入输出与成功标准

> 说明：这是一个虚构的购物车服务任务，只用于解释任务设计方法，与 `flaskDemo` 无关，也不会进入正式评测集。

## 1. 为什么要把一条任务拆成两部分

一条评测任务需要严格分离：

- **Agent 可见输入**：模拟真实用户会提供的信息；
- **评测者答案**：根因、关键证据和评分规则，Agent 运行时不可见。

如果把预期根因写进 Agent 输入，评测得到的只是复述能力，而不是调查能力。

## 2. 虚构仓库背景

假设存在一个小型 Python 项目 `cart-service`：

```text
cart-service/
├── cart/
│   ├── api.py
│   └── service.py
└── tests/
    ├── test_api.py
    └── test_service.py
```

用户发现购物车内同一种商品购买多件时，总价计算错误。

## 3. Agent 可见输入示例

下面的数据可以保存为 JSON 或 YAML。第 0 期先确定字段含义，后续再实现读取代码。

```yaml
id: sample-cart-total-001
title: 购物车多件商品总价错误
task_type: bug_diagnosis

repository:
  path: examples/cart-service
  language: python
  revision: 4f7a1c2

user_request: |
  用户反馈购物车中某商品单价为 10 元、数量为 3 时，
  GET /cart/total 返回 10 元，但预期为 30 元。
  请定位根因，给出代码证据、最小修复计划和验证方式。

initial_evidence:
  failing_test: tests/test_service.py::test_total_uses_quantity
  observed: "assert 10 == 30"

constraints:
  mode: read_only
  max_steps: 8
  timeout_seconds: 120
  allowed_tools:
    - list_files
    - search_code
    - read_file
    - run_test
  forbidden_actions:
    - write_file
    - install_dependency
    - network_access

required_output:
  - status
  - summary
  - root_cause
  - evidence
  - proposed_fix
  - verification
  - risks
```

几个重要设计点：

- 输入提供了现象和失败测试，但没有直接告诉 Agent 根因；
- `revision` 固定仓库版本，保证每次任务面对相同代码；
- `mode` 和工具白名单明确本期只能分析；
- `max_steps`、超时和禁止项控制成本与风险；
- `required_output` 使结果可以结构化评分。

## 4. 期望输出示例

这不是要求 Agent 逐字匹配的标准答案，而是一个合格输出的示范：

```yaml
status: diagnosed

summary: >
  购物车总价计算忽略了商品数量，因此每个条目只累加了一次单价。

root_cause: >
  cart/service.py 中 calculate_total 对每个条目累加 item.unit_price，
  没有乘以 item.quantity。当 quantity 大于 1 时返回值偏小。

evidence:
  - source: cart/service.py:18
    observation: "循环只累加 item.unit_price，没有使用 item.quantity。"
  - source: tests/test_service.py:24
    observation: "测试构造 unit_price=10、quantity=3，并断言结果为 30。"
  - source: test-run
    observation: "test_total_uses_quantity 实际得到 10，预期 30。"

proposed_fix:
  - 将单条商品金额改为 item.unit_price * item.quantity。
  - 保持 API 返回结构不变。

verification:
  - 运行 tests/test_service.py::test_total_uses_quantity。
  - 运行购物车服务完整测试集，确认单件商品和空购物车行为没有回归。

risks:
  - 需要确认 quantity 是否允许为 0 或负数；这属于输入校验问题，不能在没有需求时顺带改变语义。
```

## 5. 评测者答案示例（Agent 不可见）

### 5.1 事实答案

- 目标根因：总价计算遗漏 `quantity`；
- 必须命中的实现证据：`cart/service.py` 中的金额累加表达式；
- 必须命中的行为证据：失败测试中的单价、数量、期望值和实际值；
- 最小修复方向：使用 `unit_price * quantity`；
- 最小验证：目标失败测试恢复，并执行相关回归测试。

### 5.2 硬性成功条件

下列任一项不满足，任务直接判定失败：

1. 根因必须明确指出“数量未参与总价计算”；
2. 至少提供一条正确的实现代码证据；
3. 不得修改文件或执行未授权工具；
4. 不得声称测试已经通过，除非工具轨迹中确实运行且通过；
5. 不能把数据库、网络或浮点精度问题当作主要根因。

硬性条件用于防止一个语言流畅但事实错误的答案凭主观评分通过。

### 5.3 量化评分

| 维度 | 分值 | 满分要求 |
| --- | ---: | --- |
| 根因正确性 | 3 | 准确说明遗漏数量，并解释为何得到 10 而不是 30 |
| 代码证据 | 2 | 引用正确文件位置并说明该代码与缺陷的关系 |
| 行为证据 | 1 | 正确引用失败测试或实际测试结果 |
| 修复计划 | 2 | 提出最小修改，不夹带无关重构 |
| 验证方案 | 1 | 包含目标测试和相关回归测试 |
| 风险与边界 | 1 | 不虚构事实，并指出合理但未确认的边界条件 |
| **总分** | **10** | 通过线为 8 分，且全部硬性条件通过 |

## 6. 输出成功不等于修复成功

第 0～2 期处于只读分析模式，此任务的“成功”是：正确定位、证据充分、计划可执行且遵守权限。

第 3 期允许人工审批后的写操作时，同一任务可以增加第二阶段成功条件：

- 实际补丁只修改必要代码；
- 目标测试通过；
- 相关回归测试通过；
- 文件差异中没有无关修改；
- 最终报告与真实工具轨迹一致。

因此应分别记录：

- `diagnosis_success`：是否正确分析；
- `execution_success`：是否正确修改；
- `verification_success`：是否通过确定性验证。

不能只使用一个模糊的“任务成功”字段覆盖全部阶段。

## 7. 正式任务编写检查表

每写一条正式任务，都要确认：

- 问题在固定仓库版本上可以稳定复现；
- Agent 输入没有泄露根因；
- 至少存在一条可定位的实现证据；
- 成功标准能由代码、测试或工具轨迹验证；
- 硬性失败条件明确；
- 评分不依赖输出与参考答案逐字一致；
- 任务之间不会共享上一次运行留下的修改；
- 预算、步骤数、超时和工具权限已明确。
