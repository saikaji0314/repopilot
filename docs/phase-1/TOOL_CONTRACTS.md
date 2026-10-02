# 第 1 期工具安全契约

## 1. 统一原则

四个工具都只能作用于本次临时工作区。模型传入的是相对路径，工具层必须：

1. 拒绝绝对路径、空字节和 `..` 逃逸；
2. `resolve()` 后再次确认目标位于工作区根目录内；
3. 拒绝通过符号链接跳出工作区；
4. 拒绝读取密钥、虚拟环境、Git 元数据、缓存、数据库和二进制文件；
5. 对结果数量、行数、字符数和执行时间设置上限；
6. 不通过 Shell 解释模型输入；
7. 将可预期错误转换为结构化 `ToolResult`，而不是让整个进程崩溃。

统一返回外壳：

```json
{
  "ok": true,
  "tool": "read_file",
  "data": {},
  "error": null,
  "truncated": false
}
```

错误时 `ok=false`，`error` 至少包含稳定的 `code` 和面向模型的 `message`。内部堆栈只写本地 Trace，不回传给模型。

## 2. `list_files`

用途：理解目录结构，不能读取文件内容。

参数：

```json
{
  "path": ".",
  "max_depth": 2
}
```

约束：

- `path` 必须是工作区内目录；
- `max_depth` 范围 1～4；
- 最多返回 200 个条目；
- 结果按相对路径排序；
- 忽略 `.git`、`.venv`、`__pycache__`、`.pytest_cache`、`instance` 和数据库文件；
- 每项返回 `path` 与 `type=file|directory`。

典型错误：`path_outside_workspace`、`not_a_directory`、`invalid_depth`。

## 3. `search_code`

用途：在文本源码中按固定字符串搜索，返回可定位的行证据。

参数：

```json
{
  "query": "get_post",
  "path": ".",
  "file_pattern": "*.py"
}
```

约束：

- 第 1 期只做大小写敏感的固定字符串搜索，不直接执行模型提供的正则；
- `query` 长度 1～200；
- `path` 必须是工作区内目录；
- `file_pattern` 只接受简单扩展名模式，如 `*.py`、`*.html`；
- 搜索 `.py`、`.html`、`.sql`、`.toml`、`.md`、`.yaml`、`.yml`、`.txt`；
- 最多扫描 500 个文件，最多返回 50 个匹配；
- 每项返回相对路径、1-based 行号和单行文本；
- 单行和总结果都要截断，并设置 `truncated=true`。

第 1 期优先使用纯 Python 实现，保证项目不依赖用户另行安装 `rg`。第 2 期再把 ripgrep、AST 和上下文排序作为检索策略进行对比。

典型错误：`empty_query`、`invalid_pattern`、`path_outside_workspace`、`not_a_directory`。

## 4. `read_file`

用途：读取一个文本文件的指定行片段。

参数：

```json
{
  "path": "flaskr/blog.py",
  "start_line": 1,
  "end_line": 160
}
```

约束：

- 只接受允许的文本扩展名；
- `start_line` 从 1 开始；
- `end_line >= start_line`；
- 一次最多返回 200 行、20,000 字符；
- 输出带 1-based 行号；
- 文件过长时只返回请求区间，不自动返回全文；
- 对 UTF-8 解码失败给出 `unsupported_encoding`，不猜测二进制编码。

典型错误：`file_not_found`、`not_a_file`、`unsupported_file_type`、`invalid_line_range`、`path_outside_workspace`。

## 5. `run_test`

用途：执行一个受控 pytest 目标并返回真实测试观察。

参数：

```json
{
  "test_target": "tests/test_blog.py::test_update"
}
```

约束：

- 只允许 `tests`、`tests/...` 或 `tests/...::test_name`；
- 拒绝以 `-` 开头的值、空白分隔的额外参数、Shell 操作符和工作区外路径；
- 主程序固定构造参数，不拼接 Shell 字符串：

```text
[sys.executable, -m, pytest, <test_target>, -q, -p, no:cacheprovider]
```

- `shell=False`；
- 默认超时 30 秒；
- stdout 与 stderr 分开保存，各自限制字符数；
- pytest 返回非 0 仍是“工具执行成功、测试失败”，即 `ok=true` 且 `data.returncode != 0`；
- 只有参数非法、无法启动、超时等情况才是 `ok=false`。

这一点很重要：测试失败是 Agent 需要观察的业务结果，不等于工具基础设施故障。

典型错误：`invalid_test_target`、`test_timeout`、`process_start_failed`。

## 6. 工具注册表

Agent Loop 只通过注册表调用工具：

```python
registry = ToolRegistry(
    tools=[ListFilesTool(...), SearchCodeTool(...), ReadFileTool(...), RunTestTool(...)]
)

result = registry.execute(name=tool_call.name, arguments=tool_call.arguments)
```

注册表负责：

- 工具名唯一；
- 生成发给模型的 JSON Schema；
- JSON 参数解析；
- 使用 Pydantic 拒绝额外字段；
- 统一异常到 `ToolResult`；
- 记录开始、结束和耗时。

工具实现不能访问 `oracle.yaml` 或 `mutation.patch`。这两个文件位于评测任务目录，而不是 Agent 的临时仓库工作区。

## 7. 必测安全用例

| 工具 | 必测输入 | 预期 |
| --- | --- | --- |
| 全部路径工具 | `../../oracle.yaml` | 拒绝越界 |
| 全部路径工具 | Windows 绝对路径 | 拒绝绝对路径 |
| `list_files` | `max_depth=99` | 参数校验失败 |
| `search_code` | 空 query | 参数校验失败 |
| `search_code` | 超过结果上限 | 截断且进程正常 |
| `read_file` | 二进制/数据库文件 | 拒绝类型 |
| `read_file` | 201 行区间 | 参数校验失败或限制为 200 行，行为必须固定 |
| `run_test` | `tests -k x` | 拒绝附加参数 |
| `run_test` | `; whoami`、`|`、`&&` | 拒绝 Shell 注入 |
| `run_test` | 超时测试 | 终止子进程并返回 `test_timeout` |

这些测试默认使用临时目录和 Fake 子进程，不调用真实模型 API。
