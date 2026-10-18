# 单次 LLM 基线运行器

> 状态：实现完成，dry-run 6/6、真实调用 12/12 成功  
> 正式模型：`qwen3.7-plus-2026-05-26`  
> 默认次数：每任务 2 次  
> 真实 API 基线：已完成

## 1. 运行器职责

单次 LLM 基线严格执行：

```text
验证干净仓库完整测试
→ 创建唯一临时工作区
→ 复制 agent_visible 附加文件
→ git apply --check
→ 应用单一缺陷补丁
→ 运行目标测试并核对失败摘要
→ 生成过滤后的完整文本快照
→ 构造无工具、只读、JSON 输出 Prompt
→ 调用固定模型快照
→ Pydantic 校验七个输出字段
→ 保存原始响应、结构化答案、usage、耗时和费用
→ 无论成功失败都清理临时工作区
```

运行器不会读取或向模型暴露 `oracle.yaml`。`mutation.patch` 只由工作区构造器使用，也不会进入仓库快照。

## 2. 安装

在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

复制 `.env.example` 为 `.env`，只在本地填入：

```dotenv
DASHSCOPE_API_KEY=
DASHSCOPE_WORKSPACE_ID=
```

兼容旧变量 `API_KEY` 和 `WORKSPACE_ID`，但新环境统一使用 `DASHSCOPE_*` 命名。密钥不得写进 YAML、日志或运行报告。

## 3. 零费用 dry-run

先验证一条任务：

```powershell
.\.venv\Scripts\python.exe -m scripts.run_baseline `
  --task flaskr-auth-001 `
  --dry-run
```

再验证全部任务：

```powershell
.\.venv\Scripts\python.exe -m scripts.run_baseline --all --dry-run
```

dry-run 不创建模型客户端、不读取密钥，也不产生 API 费用。它仍会真实运行干净测试和缺陷测试，并构造快照与 Prompt。

本地验证结果：

```text
干净基线：24 passed
任务预检：6/6 成功
每条 Prompt：约 40,000 字符
临时目录：全部清理
```

## 4. 首次真实调用

第一次只运行一条任务、一次调用：

```powershell
.\.venv\Scripts\python.exe -m scripts.run_baseline `
  --task flaskr-auth-001 `
  --repetitions 1
```

检查以下内容后，才能运行全量：

- 返回内容是合法 JSON；
- `answer.json` 包含七个规定字段；
- `raw_response.json` 包含真实 usage；
- `meta.json` 记录模型、耗时、Token、估算费用和哈希；
- 没有 `format_error` 或 `api_or_response_error`；
- 百炼实际账单与估算数量级一致。

## 5. 全量基线

配置文件默认每任务运行 2 次：

```powershell
.\.venv\Scripts\python.exe -m scripts.run_baseline --all
```

安装为可编辑包后，也可以使用：

```powershell
run-baseline --all
```

## 6. 结果目录

每次真实运行创建独立批次：

```text
evals/runs/<run-id>/
├── summary.json
└── <task-id>/
    ├── prompt.txt
    ├── preflight.json
    ├── run-01/
    │   ├── answer.txt
    │   ├── answer.json
    │   ├── raw_response.json
    │   └── meta.json
    └── run-02/
        └── ...
```

`evals/runs/` 默认被 Git 忽略。后续从原始结果生成可提交的脱敏汇总报告，而不是直接提交全部 Prompt 和响应。

首轮真实批次为 `baseline-v1`，结果如下：

```text
调用成功：12/12
总 Token：119,928
平均模型耗时：33.79 秒/次
整批时间：约 6 分 52 秒
估算总费用：¥0.379332
人工严格通过：8/12
```

逐次人工评分、失败分类和复盘见 [首轮基线报告](../../evals/reports/baseline-v1.md)。

## 7. 配置边界

非敏感配置统一位于 `configs/baseline.yaml`：

- 固定模型快照；
- 思考模式；
- JSON Mode；
- 每任务次数；
- 超时；
- 单价估算。

路径统一从 `repopilot/legacy/config.py` 所在项目推导，不依赖命令的当前工作目录。
