# 演示材料

## 首页动图

[delivery-report.gif](delivery-report.gif)：**19.6 秒，1200×733，约 406 KiB**。六幅画面依次是交付概览、候选改动、独立复核、审核与返工、人工决定、远端产物；每一幅都保留页头、证据导航和右侧「运行信息」，只显示当前区块。

![真实历史运行的只读报告回放](delivery-report.gif)

静态预览为 [delivery-overview.png](delivery-overview.png)（1600×978）。

来源是历史运行 `issue-1-1d3bc15b6211`：公开仓库的 [Issue #1](https://github.com/saikaji0314/zhixianji-jobs/issues/1)，104 项测试、两轮审核与一次返工，批准的提交为 `e3ac2677a620a93b844eaae98ab0fa7898efeaed`，产物为[草稿 PR #2](https://github.com/saikaji0314/zhixianji-jobs/pull/2)，尚未合并。

动图是**同一次已结束运行的证据浏览**，不是 Agent 实时执行的录屏；画面切换不表示后台重新执行了实现、检查或发布。生成没有模型费用，也没有远端写入。

## 为什么重录

上一版是 868×610，源截图只有 1054×741（1 倍像素密度），再以 128 色量化、帧源为 JPEG，正文小字边缘发糊。新版的做法：

| 项 | 旧版 | 现在 |
| --- | --- | --- |
| 源截图 | 1054×741，1 倍 | 2880×1760，2 倍像素密度 |
| 成图宽度 | 868 | 1200（自 2880 缩小，属于超采样） |
| 帧源格式 | JPEG（有损） | PNG（无损） |
| 调色板 | 128 色，无抖动 | 256 色，Floyd–Steinberg 抖动 |
| 取景 | 人工在浏览器滚动截图 | 命令驱动，特写帧只渲染目标区块 |

体积从约 435 KiB 降到约 406 KiB，同时多了一幅「人工决定」。

## 重新生成

三步都是命令，不需要人工在浏览器里翻页：

```powershell
# 1) 生成离线报告（不调用模型）
.\.venv\Scripts\repopilot.exe report issue-1-1d3bc15b6211 --html .local\demo\report.html

# 2) 用本机 Chrome 无头模式截 2 倍像素密度的帧，并写出清单
python scripts/capture_report_frames.py --run-id issue-1-1d3bc15b6211 --html .local\demo\report.html

# 3) 编码成 GIF 与静态预览
python scripts/assemble_report_gif.py .local\demo\frames\frames.json `
    --output docs/demo/delivery-report.gif --poster docs/demo/delivery-overview.png
```

截图与清单留在被忽略的 `.local/demo/frames/`，不随仓库分发。两个脚本都需要 Pillow；产品运行时本身不依赖 Pillow 或浏览器。

抓帧的约束值得记下来，因为它决定了实现方式：

- 无头 Chrome 的 `--screenshot` **不会**在锚点滚动后重绘，`file:///x.html#evidence-5` 会得到纯白画面。因此特写帧改为为每个区块生成一份只显示该区块的临时页面，不依赖滚动。
- `--screenshot` 必须给绝对路径，相对路径在部分版本上会被静默忽略。
- 截图是异步落盘，脚本会等文件尺寸稳定后再继续。

编码器按清单里的 `crop` 统一裁切、按 `scale_to` 等比缩放、按 `trim` 只去掉底部空白，并校验裁切区域在截图范围内；它不生成页面内容，也不访问凭证、模型或 GitHub。

## 历史视频

[run-report.mp4](run-report.mp4) 与 [run-report.gif](run-report.gif) 保留为更早一版报告的 38 秒演示，来源是另一条较小的本地任务 `demo-test-count-sync-07d7b2a3519d`。它没有接入 Issue，也没有发布 PR；不能拿它代表完整交付。视频由脚本排版、截图及字幕合成，终端画面不是现场终端录屏。

那一版基于旧的报告布局，不再随当前页面重新生成；当前首页以上面的 GIF 为准。真实执行演示步骤见[交付演示](../runtime-v2/DEMO.md)。
