# 演示材料

本目录存放可复现的展示材料，全部由本机真实运行产物生成，不含凭证、原始模型会话与个人资料。

| 文件 | 内容 |
| --- | --- |
| [run-report.mp4](run-report.mp4) | 38 秒演示视频：开场 → 复现命令与真实输出 → 整页报告滚动 → 四个区块特写 → 能力与缺口总结 |
| [run-report.gif](run-report.gif) | 视频关键帧拼成的动图，用于 README 预览（GitHub 不内嵌播放 mp4） |

## 这条视频展示的是什么

运行 `demo-test-count-sync-07d7b2a3519d`：目标仓库 README 写「82 项离线测试」、`docs/validation.md` 写「86 项」，而登记检查实测 93 项。工单要求只改这两处文档并复跑检查。

画面里的每一项都能在本地核对：终端里是真实的 `repopilot report` 命令与输出，报告页是真实的运行状态、候选改动与差异、编排层独立复核的退出码、审核结论与绑定提交。视频不模拟进行中的进度，也不把历史回放说成实时。

## 重新生成

视频由脚本合成，帧来自真实产物；重新录制不会产生模型费用：

```powershell
# 1) 生成报告（不调用模型）
.\.venv\Scripts\repopilot.exe report demo-test-count-sync-07d7b2a3519d --html .local\demo\report.html

# 2) 合成视频（需要 Pillow、Chrome 与 ffmpeg；用带 Pillow 的 Python 运行）
python scripts/record_demo.py --run-id demo-test-count-sync-07d7b2a3519d --output docs\demo\run-report.mp4
```

脚本会先用 Chrome 无头渲染整页报告并截图，再按垂直留白切出区块，逐段推进并叠加字幕，最后交给 ffmpeg 编码。它读取的只是该 run 已落盘的 `summary.json` 与报告 HTML。

## 边界

- 这是**开发复测**的回放，不是隐藏评测，也不构成成功率结论。
- 视频里出现的仓库是公开的个人练习仓库；画面中的工单、提交与检查数字都来自该次真实运行。
- 本目录没有录屏软件录制的实时操作画面；如需实时演示，命令与步骤见[交付演示](../runtime-v2/DEMO.md)。
