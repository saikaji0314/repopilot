# 项目资料与决定管理

更新：2026-10-27。范围：版本化文档、来源／确认状态、增量全文索引、运行内固定资料及决定记录。单机本地机制；未验证真实模型收益，不含平台评论或多Agent规划。

## 解决什么问题

源码书签和业务约束用途不同。原有`memory`保存带文件指纹的代码事实，默认关闭；本模块保存项目规则、任务说明、决定和证据。显式登记并确认的业务资料会作为执行依据，`--no-memory`不会取消这些约束。

正文保留在文件中。已有项目文档可按原路径登记，不自动搬迁或修改；SQLite保存位置、元数据、状态、关联、版本和派生检索词项。正文历史保存在运行目录内的不可变版本文件中，不维护另一份可独立编辑的SQLite正文。

四种类型为`project`、`task`、`decision`、`evidence`。一个稳定ID对应一份资料，内容或元数据变化产生新的版本。版本由内容和来源元数据共同计算，和仓库HEAD不同：`observed_commit`仅表示登记／同步时观察到的仓库提交，不声称未提交文档已经存在于该提交中。

## 登记与确认

```powershell
repopilot knowledge register --repository TARGET_REPO --id api-compat --path docs/api-policy.md --kind project --title "接口兼容约束" --required --output .local/runtime
repopilot knowledge confirm --repository TARGET_REPO --id api-compat --version MATERIAL_VERSION --reason "已核对兼容要求" --output .local/runtime
repopilot knowledge search --repository TARGET_REPO --query "旧接口 空输入" --output .local/runtime
```

`MATERIAL_VERSION`来自登记或查看的实际输出。登记后为`proposed`，确认具体版本后为`confirmed`；只有确认后的资料进入运行依据。`--required`表示正文是必须提供的约束，未确认、丢失或过大的必需资料会暂停任务，不能默默忽略。普通资料按相关词项提供片段，并保留全部已确认资料的目录入口。

`--source`可重复登记来源，如Issue链接、需求文档版本或运行引用；`--relate implements:requirement-id`可记录关联。`--scope RUN_ID`限定单次任务，默认`repository`为仓库共享。范围通过Git公共目录身份隔离，linked worktree共享同仓库资料，不同clone不自动共享。

## 修改、删除与历史

```powershell
repopilot knowledge sync --repository TARGET_REPO --output .local/runtime
repopilot knowledge list --repository TARGET_REPO --output .local/runtime
repopilot knowledge read --repository TARGET_REPO --id api-compat --version MATERIAL_VERSION --start-line 1 --end-line 100 --output .local/runtime
```

正常编辑后同步，程序检测登记文件，只解析变化的文档；仅更新受影响块，其他文档的索引行不重建。未变化的章节块尽量保留键，行号随正文更新。修改后旧确认失效，新内容退回提议，不能自动继承确认。

删除文件／段落会撤掉当前检索项，完整历史版本仍可显式读取并标记`historical`。恢复已删除文件仍需重新确认。需要用新决定替代旧决定时，先确认新资料，再执行：

```powershell
repopilot knowledge supersede --repository TARGET_REPO --id old-policy --replacement new-policy --reason "新要求替代旧规则" --output .local/runtime
```

旧资料标为`superseded`，不再作当前依据，替代去向和理由仍能查看。关联目标已删除、被替代或缺失时，读取结果会给出状态；不自动删除历史引用。

必需规则的替代仍继承必需性，清单用`required_by`标明来源；后继资料被改动或失去确认也会暂停，不能通过替代操作静默取消约束。

文件快照先原子写入，再在一个SQLite事务中提交本批索引变化；中途失败时索引回滚，可能留下未引用的历史文件，重试同步可以补齐。日常改删不执行全局索引重建。

## 运行时怎么使用

任务准备时固定已确认资料的清单与版本，实现和审核使用相同清单。必需正文直接提供，其他资料通过有界词项检索提供片段。`read_material`只允许按清单中的ID和版本回查原文，每次最多200行／8000字节；不会让Agent任意读取运行目录。

工具调用正常计入原有用量限制。资料只是有来源的业务依据，不能改变工具权限、检查配置、预算或推送授权。候选worktree中的规则文件变化不会自动同步为已确认依据。

原M1本机人工答复会导出为当前任务范围的版本化资料，确认来源绑定已保存的本地答复事件；原事件继续作为操作记录，机器不能借这个入口确认自己的提议。正文已在`human_answers`提供时不重复整段放入资料载荷。

任务库与资料索引没有跨库事务。导出按人工事件生成稳定ID；文件／索引已保存而任务检查点未提交时，恢复先补齐同一事件的派生产物，再检查清单，不因此新增答复或误接纳其他新资料。

实现／审核报告可填写`decisions`：选择了什么、简短理由、来源。程序生成单独的Markdown决定记录，默认是任务范围的提议；不会把实现者的理由自动塞给首轮审核，也不提升为已确认项目政策。记录不包含原始模型思考链，完整性仍依赖角色提交的结构化报告。

审核记录注明当时的资料清单版本，决定记录也保存当时的清单、代码现场摘要和修改次数。刷新前的完整清单保留在`knowledge_history`，可用`status`回查，再按资料ID和版本读取原文；不会因刷新只剩“当时用过某个版本号”却找不到对应清单。代码现场摘要是内容指纹，不冒充Git提交SHA。

## 资料变化后如何继续

运行期间会核对当前来源与固定清单，并检查候选工作区是否修改了作为依据的文件。资料变化、删除、失去确认或候选改写规则时，保存现场并创建`context_update`人工请求；旧检查、审核结论和批准不再有效。批准前及任何远端发布操作前也检查，不能凭旧批准继续交付。

核对也检查完整正文历史的散列；即使原来源未变化，版本文件丢失或被改写也不能沿用旧依据。

先核对资料，必要时确认新内容，再显式刷新这个任务：

```powershell
repopilot human list RUN_ID --output .local/runtime
repopilot knowledge refresh --run-id RUN_ID --request-id REQUEST_ID --version REQUEST_VERSION --commit CURRENT_FULL_SHA --reason "已核对资料变化与当前现场，重新验证" --output .local/runtime
repopilot resume RUN_ID --output .local/runtime
```

这里`REQUEST_VERSION`是人工请求版本，和资料的`MATERIAL_VERSION`不同。`CURRENT_FULL_SHA`表示操作者已核查当前候选现场。普通`human answer`不能代替刷新；刷新不能修改原Issue、任务或执行权限，不能批准交付。

本增量采用保守规则：重新从实现／当前返工阶段检查任务，保留调用量、费用和已有修改次数，再运行独立检查与审核。刷新只固定新的资料和当前文件状态，不自动合并来源仓库变更、覆盖候选文档或更新Git基线；源码／文档不一致仍须在实际开发与审核中解决。细粒度依赖影响恢复留给任务规划增量。

## 存储、检索与限制

- `knowledge.sqlite3`存储登记、确认、关系及章节索引；`knowledge/<仓库ID>/documents/`为运行生成文档，`versions/`为内容版本。自动生成的答复文档来自原事件，应通过新答复变更业务要求，不直接改写生成文件冒充原答案。
- FTS5使用英文词与中文重叠双字作为派生词项，支持本地关键词查找；不是向量检索或语义理解。没有引入额外数据库／embedding服务。
- 单份正文上限256KB；资料载荷上限24KB。必需正文超出分段／载荷限制会暂停，不能截掉约束后继续。其他检索片段合计6000字节、最多5项；可按目录继续分段读取。
- 备份需同时保留SQLite、版本文件及原仓库；本增量不支持跨机器同步、企业身份、不可篡改审计，数据库丢失不等于可从正文自动恢复全部确认记录。
- 新能力对已登记资料生效，不自动遍历或上传整个仓库。已有无此快照的历史运行不自动接纳后来新增的资料，需新运行或明确的上下文处理。

实现分工：[文档与切分](../../repopilot/knowledge/documents.py)、[存储／增量索引](../../repopilot/knowledge/store.py)、[检索与关联](../../repopilot/knowledge/retrieval.py)、[阶段接线与失效](../../repopilot/orchestration/knowledge.py)。资料命令和本机确认不加载模型／平台凭证。
