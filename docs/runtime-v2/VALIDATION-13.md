# 增量13：SWE-bench 单题评分环境预检

日期：2026-10-18。

目标是为后续真实修复实验建立可追溯输入和官方评分链路。该增量不调用模型、不改变交付工作流，也不产生 RepoPilot 的修复成功率。

## 实现与验收

- `evaluation/swebench.py` 从本地官方 JSONL 显式选定单题，冻结数据集 revision、文件摘要、案例摘要与评分器 SHA；重复身份、非固定提交和无效路径身份拒绝。
- 解题输入仅保留身份、仓库、起始提交、需求和版本；不包含答案、验收补丁、测试清单、hints 或未知字段。完整评分记录为另一个 JSONL 文件，可直接交给官方工具。
- Agent、参考和空补丁三种来源分别导出官方预测 JSONL 与本地摘要清单。变更过的冻结记录不能导出，已有输出目录不能覆盖；导出不赋予成功结论。
- 评分仍由官方工具执行，不把 Agent 自己的检查或审核结果改造成基准分数；未接入批量解题、自动准备仓库、运行与补丁绑定或成绩聚合。

操作与边界见 [评测协议](../../evals/swebench/README.md)。这些文件的逻辑分离不等于 OS 权限隔离，控制记录不能放进解题工作区。

## 固定来源与真实预检

| 项目 | 实际值 |
| --- | --- |
| 评分器 | 官方 SWE-bench 5.0.2，提交 `02e7a74ffd0b707aab73d203fe87bdc7c76afc8e` |
| 数据 | SWE-bench/SWE-bench_Lite，test，300项；只执行下述1项 |
| 数据集 revision | `b0dde1093fe417d83b7184254edf8199c1f0dff5` |
| 案例 | `sympy__sympy-20590`，官方快速开始使用的参考案例 |
| 仓库基线 | `cffd4e0f86fefd4802349a9f9b19ed70934ea354` |
| 镜像 | `swebench/sweb.eval.x86_64.sympy_1776_sympy-20590` |
| 实际镜像 digest | `sha256:3a282752833ce34730ee0621e22033501c993f45742775ca57f04c9ff27178a0` |
| 环境 | Windows宿主、Ubuntu 24.04 WSL2、x86_64、Python3.12.3、独立评分器venv与Docker Engine |

| 对照 | 实际结果 | 证明什么 |
| --- | --- | --- |
| 参考补丁 | resolved=true；FAIL_TO_PASS 1成功／0失败，PASS_TO_PASS 21成功／0失败 | 该题环境能够验证正确修复 |
| 未修复基线 | 官方 `run_instance(..., skip_patch=True)`；resolved=false；缺陷验收1失败，原有21项回归通过 | 问题在原始基线上确实存在，非环境导入错误 |
| 空补丁 | 官方CLI列出 empty_patch_instances=1、resolved_instances=0、error_instances=0 | 未作答没有被记为成功；该对照没有执行测试 |

参考预检使用本增量导出的 JSONL，通过官方 `swebench eval`，1个worker、单题300秒测试超时。基线直接调用同一固定版本官方 API 的 skip_patch 模式，不修改评分器源码。三个实验使用不同 run ID，未复用不同补丁的评分缓存。

评分器使用数据集提供的镜像标签；表中记录实际拉取的 digest。再次运行前应核对 digest，标签本身不是不可变版本。本次使用官方预构建环境，没有验证从源码重建镜像的可复现性，也没有证明 RepoPilot 解题沙箱能直接运行此环境。

## 验证与限制

- 评测模块测试：32 passed，包含13项新测试与已有19项独立评分测试。覆盖答案字段不传播、文件变化拒绝、重复身份拒绝、标准补丁字节保存、来源分类与 CLI 行为。
- 真实容器验证：如上三组对照；不是 FakeModel 修复，不调用付费模型。所有本次评分容器已移除，约4GB案例镜像保留缓存。
- 未运行全项目回归；生产运行代码与既有预算、队列、工作树未改动。数据集与官方工具安装在独立本地目录／环境，不加入核心依赖。
- 没有模型解题、基线 Agent 对比、组件收益或留出集结论；不能将参考补丁的1/1写成 RepoPilot 的成功率，也不能据此宣称生产级可用。

本地保留数据来源、安装依赖清单、冻结案例、预测摘要、逐题报告和测试输出。最初准备venv时因Ubuntu缺ensurepip失败，安装python3.12-venv后完成；原失败记录保留。开发中身份校验曾接受`../bad`，专项测试发现后修正，未放宽测试。

来源：[官方评分器](https://github.com/SWE-bench/SWE-bench)、[快速开始](https://www.swebench.com/SWE-bench/guides/quickstart/)、[Lite 数据集](https://huggingface.co/datasets/SWE-bench/SWE-bench_Lite)。原始数据、参考答案与上游 checkout 不提交。
