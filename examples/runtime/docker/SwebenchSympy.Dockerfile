# 单题开发实验环境；不代表其他SWE-bench案例均兼容。
FROM swebench/sweb.eval.x86_64.sympy_1776_sympy-20590@sha256:3a282752833ce34730ee0621e22033501c993f45742775ca57f04c9ff27178a0
USER root
WORKDIR /work
# 保留来源许可，移除原始checkout和Git历史；Agent仅接触控制面传入的基线快照。
RUN mkdir -p /opt/repopilot-licenses && cp /testbed/LICENSE /opt/repopilot-licenses/sympy-LICENSE && rm -rf /testbed && mkdir -p /work && chmod 755 /work
# 文件服务需Python>=3.10，公开检查显式使用原有conda Python3.9。
ENV PATH=/usr/bin:/bin:/opt/miniconda3/envs/testbed/bin
