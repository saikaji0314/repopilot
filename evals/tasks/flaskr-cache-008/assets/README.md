# 本地工单模块

宿主提供 Flask app 和 flaskr.db.get_db；集成时注册 flaskr.tickets.bp。
测试 fixture 负责注册及创建 ticket 表，原博客路由保持不变。

- GET /tickets：返回全部工单，按 id 排序。
- GET /tickets?status=open 或 closed：只返回指定状态；其他值返回 400。
- GET /tickets/<id>：返回详情，启用当前应用独立的进程内缓存。
- PATCH /tickets/<id>，JSON {"title":"新标题"}：持久化标题并返回新记录。
  更新后的后续 GET 必须看到新标题；其他工单的缓存不应被清空。
- 详情或更新目标不存在返回 404；空标题返回 400。

这是本地教学模块，无鉴权、多进程一致性、TTL 或公网部署承诺。
