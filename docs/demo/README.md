# 演示材料

## 当前首页动图

[delivery-report.gif](delivery-report.gif)：16.7 秒，868×610，约435 KiB。五幅画面依次浏览交付概览、候选改动、独立复核、审核返工、人工决定与草稿 PR；最后一幅同时包含人工决定和 PR。

![真实历史运行的只读报告回放](delivery-report.gif)

静态预览为 [delivery-overview.png](delivery-overview.png)。原始截图来自浏览器，导出时裁掉预览面板外的右侧／底部空白，统一画布，不拉伸、不重排截图内容。PNG 按实际格式重新编码，不用文件扩展名冒充格式。

来源是历史运行 `issue-1-1d3bc15b6211`：公开仓库的 [Issue #1](https://github.com/saikaji0314/zhixianji-jobs/issues/1)，104 项测试、两轮审核与一次返工，批准的提交为 `e3ac2677a620a93b844eaae98ab0fa7898efeaed`，产物为[草稿 PR #2](https://github.com/saikaji0314/zhixianji-jobs/pull/2)，尚未合并。

动图是**同一次已结束运行的证据浏览**，不是 Agent 实时执行的录屏；画面切换不表示后台重新执行了实现、检查或发布。生成没有模型费用，也没有远端写入。

## 重新生成

先用本机已有运行生成离线报告：

```powershell
.\.venv\Scripts\repopilot.exe report RUN_ID --html .local/demo/report.html
```

在浏览器打开报告，固定视口，依次定位概览、候选改动、独立复核、审核与交付区域并保存截图。不要截取应用外层的聊天面板或空白；不要用不同尺寸的截图直接拉伸拼接。

截图和清单留在被忽略的本地证据目录。清单示例（路径相对清单文件，crop 需按实际截图确定）：

```json
{
  "crop": [0, 0, 868, 610],
  "frames": [
    {"file": "00-overview.jpg", "duration_ms": 3200},
    {"file": "02-changes.jpg", "duration_ms": 3500}
  ]
}
```

使用带 Pillow 的 Python 编码，运行时产品不依赖 Pillow：

```powershell
python scripts/assemble_report_gif.py path/to/frames.json --output docs/demo/delivery-report.gif --poster docs/demo/delivery-overview.png
```

脚本按图像内容识别输入格式，校验裁切区域，统一裁切并量化成 GIF，同时输出尺寸、帧数、时长和字节数。它不生成页面内容，也不访问凭证、模型或 GitHub。

## 历史视频

[run-report.mp4](run-report.mp4) 与 [run-report.gif](run-report.gif) 保留为前一版报告的38秒演示，来源是另一条较小的本地任务 `demo-test-count-sync-07d7b2a3519d`。它没有接入 Issue，也没有发布 PR；不能拿它代表完整交付。视频由脚本排版、截图及字幕合成，终端画面不是现场终端录屏。

本轮没有重录该视频，历史脚本基于旧布局的像素分段，不能保证适用于当前页面。当前主页以本页的新 GIF 为准。真实执行演示步骤见[交付演示](../runtime-v2/DEMO.md)。
