# 工单业务夹具

该模块为 Flaskr 提供跨文件诊断场景。宿主提供 Flask app 与 `flaskr.db.get_db`，集成时注册 `flaskr.tickets.bp`；测试 fixture 注册蓝图并创建 ticket 表，原博客路由保持不变。

## API 行为契约

- `GET /tickets` 返回全部工单，按 id 排序。
- `GET /tickets?status=open` 或 `closed` 只返回指定状态；其他值返回 400。
- `GET /tickets/<id>` 返回详情，启用当前应用独立的进程内缓存。
- `PATCH /tickets/<id>` 接收 JSON `{"title":"新标题"}`，持久化标题并返回新记录。更新后的后续 GET 必须看到新标题，其他工单的缓存不应被清空。
- 详情或更新目标不存在返回 404；空标题返回 400。

## 模块结构

`routes.py` 处理 HTTP 输入输出，`service.py` 组织业务操作，`repository.py` 访问 SQLite，`cache.py` 管理应用内缓存，`test_tickets.py` 验证 API 行为。

夹具用于本地受控测试，覆盖路由、业务层与数据层的协作；未实现鉴权、多进程缓存一致性或 TTL。业务契约适用于干净与缺陷版本，诊断时需根据当前源码和实际测试核对实现。
