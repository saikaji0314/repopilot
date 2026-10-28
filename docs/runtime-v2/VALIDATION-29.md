# 增量29：双视角审核、发现合并与争议复核

日期：2026-10-28。范围：M5。基线`dev/75fb7fd`。本轮只做本地机制验证：确定性审核／A／B／C报告替身、真实本地Git／SQLite／工作区和CLI，没有真实模型双审、Docker双审、真实平台评论或PR写入，未推送、未调用付费模型，原预算台账未动。

## 交付内容

- `repopilot/review/`：审核依据快照（`basis`）、报告合并与阻断（`merge`）、双视角并行执行（`runner`）、一轮编排与恢复（`rounds`）、争议记录与结论应用（`disputes`）、状态推进与交接（`outcome`）、人工争议判断与额度延长（`gate`）、只读CLI（`cli`）。
- 两个只读视角：`correctness`与`context`并行审核同一提交、基线、检查、资料和任务说明；首轮互不看实现说明和对方结论，复审逐条回应固定ID。
- 发现契约扩展：依据位置或需求／资料引用、严重程度、证据状态、违反要求、触发条件、影响、观察和建议；`ReviewReport`支持复审`responses`；新增A／B的`DisputeReviewReport`和C的`ArbitrationReport`。
- 返工上限与人工出口：默认3次修改、最多4轮正式审核；到限打开`handover`请求；`review continue`明确延长，`human answer`补充需求，二者保留历史和次数；`retry`仍为新尝试。
- 依据失效与复用：依据变化撤销旧检查／审核／批准；同提交同依据在恢复时复用已完成审核，不再调用模型。
- CLI：`review show`、`review decide`、`review continue`，不加载模型／平台凭证。
- 报告：视角结论、合并发现、证据状态、争议A／B／C、修改次数与上限。

详见[双审与争议](REVIEW.md)。

## 验证方法

测试环境：仓库内`.venv` Python 3.12；进程级`TEMP`／`TMP`／`TMPDIR`与`GIT_CEILING_DIRECTORIES`置于`.local/tmp/m5-system-temp`，pytest使用`-p no:cacheprovider`与仓库内`--basetemp`。审核模型调用由`review.runner.run_reviewer`的确定性替身替换；A／B／C分别替换`run_dispute_reviewer`和`run_arbitration`。Git、SQLite、工作区、CLI和报告均为真实本地组件。

命令：

```text
python -m pytest tests/runtime/test_dual_review.py tests/runtime/test_report_contract.py tests/evaluation/test_solver.py -q -p no:cacheprovider --basetemp .local/tmp/m5-targeted-final --tb=short
python -m pytest .local/evidence/m5-independent-review/test_probes.py -q -p no:cacheprovider --basetemp .local/tmp/m5-original-probes2 --tb=line
python -m pytest .local/evidence/m5-review-fixes/test_fixed_probes.py -q -s -p no:cacheprovider --basetemp .local/tmp/m5-fixed-probes2 --tb=short
python -m pytest .local/evidence/m5-independent-rereview/test_rereview_probes.py -q -p no:cacheprovider --basetemp .local/tmp/m5rr --tb=short
python -m pytest tests -q -p no:cacheprovider --basetemp .local/tmp/m5-r5-full --tb=short
```

## 结果

| 检查 | 结果 |
| --- | --- |
| M5双审／争议专项 | 13 passed（含旧运行兼容用例） |
| 审核报告契约 | 11 passed（含4项新增证据结构／A／B／C契约） |
| 评测接线与M5相关用例 | 40 passed，59.64秒 |
| 完整`tests` | 687 passed，16 skipped，649.42秒（R1—R4修复前） |
| 真实模型／平台／Docker | 0次 |

`test_solver` 的两个失败曾在中间版本出现：双审直接导入`repository_session`，绕过了评测替身对`phases.repository_session`的替换，导致尝试真实Docker会话而`workspace_error`。修复为把会话工厂从阶段传入审核执行，评测替身重新生效；最终完整测试通过。该失败保留在本地记录，不隐藏。

旧运行兼容另有两处修正：恢复时为没有M5字段的旧状态补默认审核记录；没有M5标记的旧运行继续使用原批准摘要算法，新增用例确认旧摘要仍能批准。新运行的批准摘要绑定审核依据、合并发现和争议结论。

## 独立复审修复（R1—R4）

独立复核提出四项P2程序缺陷，修复后重跑原探针与相关回归：

| 发现 | 修复与验证 |
| --- | --- |
| R1 一侧漏答／无法核验仍可关闭旧阻断发现 | 复审关闭旧发现要求两个视角都给出可核对的关闭意见；漏答、`unable_to_verify`、`insufficient_information` 保留为`unverified`，合并结论不能批准；`approved`契约也拒绝携带此类回应。新增单元、工作流和契约用例。 |
| R2 旧提交争议结论无条件用于新候选 | 争议记录只在同提交、同依据时可复用；更换候选或依据后旧结论只作历史，新候选上的有依据冲突重新核对一次并产生新记录。新增同版本复用／跨版本不复用的单元、完整工作流和适配安全探针。 |
| R3 重报旧发现吞掉双方冲突 | 争议判定先于“重报去重”；一方坚持、另一方有依据排除时进入争议并保留原发现ID，不因审核者多列一条 finding 而继续返工。新增完整工作流回归。 |
| R4 A／B一侧完成后中断会重跑 | A／B每侧报告完成即写入争议记录和检查点；恢复只运行缺失侧，C只在两侧齐备后执行；C结果也先落盘再应用。新增中断恢复回归，确认A调用1次、B在恢复后补1次。 |

在原独立探针上，六项缺陷断言全部不再复现（预期失败）；改写为安全行为断言后的六项探针全部通过。定向复审随后确认R1—R4关闭，但发现R5：返工计数本身进入审核依据哈希，导致同一提交、同一实质证据下的同一争议在无改动返工时再次启动A／B＋C。

## 定向复审R5修复

- 新增“实质依据哈希”：候选提交、基线、独立检查、资料清单、任务说明、计划、需求版本；**不包含返工计数**。
- 争议记录同时保存完整审核依据哈希和实质依据哈希；复用只按同提交＋同实质依据，单纯返工计数变化不再重开同一争议；提交或实质证据变化仍产生新记录并重新核对。
- 旧记录没有实质依据字段时，仅在完整依据也相同时回退复用。
- 新增`test_noop_rework_cannot_repeat_ab_on_same_commit`和实质依据字段单元回归；定向复审的`test_rereview_probes.py`（含R5、R1—R4及C检查点恢复）9项全部通过，38.67秒。

最终检查：

| 检查 | 结果 |
| --- | --- |
| 定向复审安全探针（含R5） | 9 passed，38.67秒 |
| 原独立缺陷探针 | 6项断言失败（缺陷不再复现），22.74秒 |
| 改写后的安全探针 | 6 passed，24.58秒 |
| 双审／契约／评测接线 | 33 passed（R5新增用例后），73.15秒 |
| 完整`tests` | 696 passed，16 skipped，696.73秒 |

原始复现输出、安全探针输出、定向复审探针、47项和全量输出保存在`.local/evidence/increment-29/review-fixes/`与`.local/evidence/m5-independent-rereview/`。

## 验收场景对应

| 编号 | 本轮覆盖 |
| --- | --- |
| A10 | 两个视角读取同一`basis_hash`与提交，首轮载荷不含对方发现或实现说明；线程屏障确认并行；合并后保留两条互补发现及各自来源 |
| A11 | 合并去重保留双方措辞；`minor`只作非阻断建议；`basis=requirement`发现用验收引用而不编造行号；契约拒绝非代码发现携带代码位置 |
| A12 | 无争议不创建争议记录；复审“已解决”与“仍未解决”冲突时A／B互不看对方报告、C拿到两份报告；C`refuted`排除发现、`confirmed`继续返工、`undetermined`打开人工请求；普通`human answer`不能代替争议判断；重复判断被拒绝 |
| A13 | 默认3次上限由运行状态保存；测试用1次上限验证到限`handover`、人工`review continue`延长后继续并保留次数；`human answer`路径不清零 |
| A16 | 提交／检查／任务说明／返工次数变化产生新依据哈希；依据变化撤销旧审核、检查和批准；同提交同依据在中断恢复后直接复用审核，不增加模型调用 |
| 明确问题、误报、信息不足、到限 | 分别由changes_requested返工、C refuted、无证据一致→unverified→不批准、rework_limit_reached覆盖 |

## 已知限制

- 审核与争议报告都是确定性替身；本轮不能说明真实模型发现缺陷、共同遗漏、AB判断质量或双审收益。
- 没有Docker后端的双审并行；每个视角会各自创建只读容器会话，未实测。
- 没有真实GitHub／GitLab Issue评论争议往返；`dispute`／`handover`只走本地CLI。
- 去重是“依据位置＋文本相似度”的保守规则，可能拆开措辞差异大的同一问题，或在相邻行合并不同问题。
- 争议只在复审回应冲突时触发；首轮两个视角的隐含矛盾不会自动升级为人工判断。
- 依据复用限同一运行、同一提交和同一依据，不跨重试或跨运行。
- 未重跑SWE-bench或真实任务评测，本轮结果不构成效果提升证据。
