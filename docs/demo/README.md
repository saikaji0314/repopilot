# 真实交付的报告回放

![App交付、负责人批准与合并确认](delivery-report.gif)

这份动图来自已经结束的真实运行`issue-6-77078d068ae6`：[分页Issue #6](https://github.com/saikaji0314/zhixianji-jobs/issues/6) → [PR #7](https://github.com/saikaji0314/zhixianji-jobs/pull/7)。它是离线HTML报告的只读截图回放，不是实时终端录像，不用字幕合成模型执行过程。

## 回放里能核对什么

七帧依次展示交付概览、候选改动、独立复核、双审与一次返工、人工发布决定、远端PR、原生Approve及合并确认。候选SHA是`73d36465be46538894c15cb193c03bce7cea4203`，确认的合并提交是`f74b92091215ef9db734cefaa0fe40d205c82d51`。

动图呈现正常交付路径。冲突处理的独立案例、验证结论及失败记录见[验证总览](../VALIDATION.md)；原始模型会话不包含在展示素材中。

- [动图](delivery-report.gif)：1200×733，7帧，23.2秒。
- [静态封面](delivery-overview.png)：便于文档或讲解时展示。
- HTML为单文件报告，原始会话、账本、密钥和本机数据不随媒体公开。报告可能含业务内容，分享自己的报告前应核对。

## 用自己的运行生成回放

以下为PowerShell示例。使用安装了RepoPilot和Pillow的解释器，另需本机Chrome或Edge；这两个截图工具不属于运行时依赖。

```powershell
.\.venv\Scripts\python.exe -m pip install Pillow
.\.venv\Scripts\repopilot.exe report RUN_ID --html .local/demo/report.html
.\.venv\Scripts\python.exe scripts/capture_report_frames.py --run-id RUN_ID --html .local/demo/report.html --work .local/demo/frames
.\.venv\Scripts\python.exe scripts/assemble_report_gif.py .local/demo/frames/frames.json --output docs/demo/delivery-report.gif --poster docs/demo/delivery-overview.png
```

将`RUN_ID`替换为已有运行ID。导出、截图和编码均不调用模型，也不执行发布或审批。当前素材使用Pillow 12.3.0和本机无头浏览器生成；其他浏览器路径可用`--chrome`指定。

截图按实际章节标题定位，不依赖旧章节序号；工作目录可复用，不会被整体删除。缺少某个章节会明确报错，不能用其他章节冒充。旧的合成视频及旧版报告媒体已归档，不再作为当前流程的展示入口；Git历史仍保留原版本。
