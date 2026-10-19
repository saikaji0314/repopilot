# 仓库事实记忆

本增量保存跨运行可复用的**文件事实书签**：相对路径、行范围、真实代码片段、来源提交和依赖文件摘要。它用于下一次任务定位源码；不保存模型自由总结、完整对话、原始思考、历史批准或“上次修好了所以这次也正确”的结论。任务经验归纳和长期语义检索尚未实现。

它与当前任务的[运行内工作集](CONTEXT.md)分开：工作集从本次工具观察整理、随阶段使用；仓库记忆按本机 Git 仓库身份跨运行复用，同一仓库的 worktree 共享身份，不同 clone 默认不共享。

## 何时使用

- 实现启动：按当前需求检索相关的已确认事实，作为路径与片段参考；仍须读取当前源码核对。
- 审核：可读同一批文件事实，但不继承实现方的判断；候选已改动对应源码时，旧事实会被过滤。
- 返工、恢复：继续使用运行开始固定的命中集合，使用前重查文件、依赖、TTL和撤销状态，不从其他任务悄悄补入新事实。
- 审核及独立检查通过后：从实现报告中的文件证据定位真实代码，自动收集最多5个候选。模型的观察文字不入库；候选未经操作者确认，不进入后续检索。

没有记忆时照常调查；记忆数据库异常只留下 `memory_warning`，不绕过检查、审批或阻断原交付流程。只读、无Git快照无法建立提交来源，启用时可能报告记忆不可用；核心只读调查仍可继续。

## 开启与管理

默认关闭。新运行可用 `--memory` 开启，`--no-memory`关闭；两者适用于run、issue、retry。也可在可信profile／watch target settings中指定 `memory_enabled: true`，或设置环境变量 `REPOPILOT_MEMORY_ENABLED=true`。命令行覆盖profile，resume使用原运行冻结配置，不在中途切换开关。

```powershell
# 下面的运行命令会调用模型并产生费用；管理记忆的命令不会。
.\.venv\Scripts\repopilot.exe run task.yaml --memory
.\.venv\Scripts\repopilot.exe run task.yaml --no-memory
```

操作者可以先登记一个已经核对的文件事实。替换示例中的仓库和文件，行范围必须存在，最多40行、片段最多1600 UTF-8字节；来源及依赖文件必须与HEAD中的版本一致。

```powershell
$repo = 'path/to/your/git-repo'
.\.venv\Scripts\repopilot.exe memory remember --repository $repo --path app.py --start-line 1 --end-line 1
.\.venv\Scripts\repopilot.exe memory list --repository $repo
.\.venv\Scripts\repopilot.exe memory search --repository $repo --query 'app'
# 可显式登记影响事实有效性的锁文件或配置，最多共8个依赖（含来源文件）
.\.venv\Scripts\repopilot.exe memory remember --repository $repo --path app.py --start-line 1 --end-line 1 --depends-on pyproject.toml
# 对自动产生的候选核对内容和来源后，再确认其ID
.\.venv\Scripts\repopilot.exe memory confirm --repository $repo --id MEMORY_ID --reason '已核对文件与依赖'
.\.venv\Scripts\repopilot.exe memory revoke --repository $repo --id MEMORY_ID --reason '暂不使用'
.\.venv\Scripts\repopilot.exe memory forget --repository $repo --id MEMORY_ID --reason '删除索引内容'
```

默认存储在 `.local/runtime/memory.sqlite3`，管理命令和运行命令应使用相同的 `--output` 根目录。它与jobs／budget数据库分开，不迁移或清空既有台账。管理命令不加载模型或GitHub凭证。

## 来源和生命周期

记录包含：`id / repository_id / kind / path / start_line / end_line / content / source_commit / source_run / dependencies / status / created_at / expires_at`；人工操作另有时间、动作、理由和local_operator记录。首版是同用户本机操作记录，不提供组织身份认证或RBAC。

- `remember` 是操作者直接登记已核对的实际代码；新记录为active。
- 自动收集为candidate，需要 `confirm`。确认重新核对源文件、依赖与提交存在性，然后激活并刷新30天TTL。独立测试／模型审核通过本身不会自动激活候选。
- 来源／登记依赖内容变化、文件丢失、Git来源对象不可用或TTL过期时，检索停用并返回原因，不因为其他无关提交而全部失效。
- `revoke` 停用；`forget` 停用并清空索引片段，保留ID墓碑，避免同一事实被事件重放复活。重复remember返回原记录，不自动激活已有候选或恢复被删除记录。
- 删除索引不擦除之前已经生成的运行快照、事件和其他本地审计文件；它们保留历史证据，但后续阶段不会再次注入被撤销／删除的事实。

自动候选同时登记存在的pyproject.toml、requirements.txt、package.json及常见锁文件。无法验证这些依赖时会略过候选并记原因，不省略依赖后强行记住。路径、片段长度和校验失败均不能让记忆升级为权限或审批规则。

## 隔离、检索与快照

仓库作用域取自本机Git公用目录的规范化身份，不能由Issue文本指定。一个仓库的worktree共享身份；不同clone即使同源，也默认不共享。首版只有本机仓库作用域，没有跨仓库／组织经验共享。

采用SQLite结构化过滤和简单关键词匹配，支持标识符与中文片段。只扫描本仓库最近200条active记录，对相关性最高的20条验证新鲜度，最多取5条，条目JSON总计不超过6000 UTF-8字节。候选／删除记录不会挤掉active扫描名额。这是有界启发式检索，不保证全库召回，没有向量服务或embedding调用。

命中集合保存在运行检查点及 `memory-snapshot.json`，含snapshot_id、条目来源、命中分数和略过原因。各阶段的 `memory-implement.json`、`memory-review.json`、`memory-rework.json` 与事件记录实际送入载荷的ID及过滤结果。新确认事实只影响后续新运行；现有快照中的事实如果后来被改动或撤销，会被移除，不会重新填满空位。

复核以阶段载荷构建时的状态为准；不能追溯撤回已经构建／发给模型的内容，也不能物理抹去既有审计。长时间模型请求期间的撤销，会在下一次构建阶段载荷时生效。

事实通过 `repository_memory` 用户载荷传给模型，不提升为系统指令，也不改变工具列表和可信检查。日志只能证明已检索／已提供参考，不能据此断言模型实际采用或提高了成功率。

## 数据边界与实际验证

复用文件工具的路径／链接和UTF-8限制，另拒绝.env示例、evals、oracle、patches、evaluation等路径；不从评测答案或控制补丁学习。写入时过滤常见凭证格式和当前运行已知模型密钥；这不是完整秘密扫描或OS隔离，仍只接入可信本机仓库，操作者应核对登记内容。

验证覆盖真实Git／SQLite及CLI；以确定性模型检查实现、审核、返工和恢复的载荷。它证明存储、来源、隔离和接线机制，不证明真实模型减少调查、降低费用或提高修复率。后续需先冻结相关但不同的任务序列和可见记忆版本，再与关闭记忆的同配置运行比较，严禁把留出答案导入记忆。结果见 [VALIDATION-08](VALIDATION-08.md)。

真实收益对照入口与种子约束见[评测协议](../../evals/swebench/README.md#显式仓库记忆对照)。比较时分别统计官方修复、完整审核、调用量、输入／输出 tokens、费用、检索命中和失效过滤；命中事实本身不算收益。人工导航书签实验与未来跨任务自动收集／确认的实验必须分别表述。
