# 手写 Agent Loop 伪代码
```
while True:
    result = model.complete(prompt, tool)
    message.append(result.assist_message)
    if result.tool_uses:
        for tool_use in result.tool_uses:
            # tool_use限制
            if tool_use_limit_reach():
                finish("max_tool_use")
                break
            resp = tool_use(tool=result.tool_use)
            message.append(resp.as_tool_message())
    else:
        answer = validate_json(result.content)
        save(answer)
        finish("end")
```