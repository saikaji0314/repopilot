# Flaskr —— Flask 入门博客应用

## 一、项目概述

Flaskr 是一个基于 **Flask** 框架构建的**简易博客应用**，源自 Flask 官方教程。它实现了博客的核心功能：用户注册/登录、文章的增删改查（CRUD），适合作为 Flask Web 开发的入门项目或二次开发的基础脚手架。

### 技术栈

| 层次 | 技术选型 |
|------|----------|
| Web 框架 | **Flask**（Python） |
| 数据库 | **SQLite**（通过 `sqlite3` 标准库） |
| 模板引擎 | **Jinja2**（Flask 内置） |
| 密码加密 | **Werkzeug** `generate_password_hash` / `check_password_hash` |
| 命令行工具 | **Click**（Flask CLI 内置） |
| 测试框架 | **pytest** |
| 构建工具 | **flit**（`pyproject.toml`） |

---

## 二、项目目录结构

```
flaskr-tutorial/
├── flaskr/                          # 主应用包
│   ├── __init__.py                  # 应用工厂（create_app）
│   ├── db.py                        # 数据库连接、初始化、CLI 命令
│   ├── auth.py                      # 用户认证蓝图（注册/登录/登出）
│   ├── blog.py                      # 博客文章蓝图（首页/创建/编辑/删除）
│   ├── schema.sql                   # 数据库建表 SQL
│   ├── static/
│   │   └── style.css                # 全局样式表
│   └── templates/
│       ├── base.html                # 基础模板（导航栏 + 消息闪现 + 内容块）
│       ├── auth/
│       │   ├── login.html           # 登录页
│       │   └── register.html        # 注册页
│       └── blog/
│           ├── index.html           # 首页（文章列表）
│           ├── create.html          # 创建文章页
│           └── update.html          # 编辑/删除文章页
├── tests/                           # 测试目录
│   ├── conftest.py                  # pytest 夹具（fixtures）
│   ├── data.sql                     # 测试用初始数据
│   ├── test_auth.py                 # 认证模块测试
│   ├── test_blog.py                 # 博客模块测试
│   ├── test_db.py                   # 数据库模块测试
│   └── test_factory.py              # 应用工厂测试
├── pyproject.toml                   # 项目元数据与构建配置
├── README.rst                       # 原始英文 README
└── .gitignore                       # Git 忽略规则
```

---

## 三、核心模块详解

### 3.1 应用工厂 — `flaskr/__init__.py`

这是整个应用的**入口**，核心是 `create_app()` 函数（工厂模式）：

- **配置加载**：默认配置（`SECRET_KEY`、`DATABASE` 路径），并支持从 `instance/config.py` 读取实例配置，测试时可传入 `test_config` 覆盖。
- **实例目录**：自动创建 `instance/` 文件夹存放数据库文件。
- **测试路由**：`/hello` 返回 `"Hello, World!"`，用于验证应用是否正常运行。
- **模块注册**：依次初始化数据库模块（`db.init_app`），注册 `auth` 和 `blog` 两个蓝图。
- **根路由**：`app.add_url_rule("/", endpoint="index")` 将首页映射到 `blog.index`，使得 `url_for('index')` 等价于 `url_for('blog.index')`。

> **关键设计理念**：采用应用工厂模式而非全局 `app = Flask(__name__)`，便于测试时创建独立实例。

### 3.2 数据库层 — `flaskr/db.py`

| 函数 | 职责 |
|------|------|
| `get_db()` | 获取当前请求的数据库连接，存储在 `g.db` 中，同一请求内复用 |
| `close_db(e)` | 请求结束时关闭数据库连接，注册为 `teardown_appcontext` 回调 |
| `init_db()` | 执行 `schema.sql` 建表（**先 DROP 再 CREATE**，会清空旧数据） |
| `init_db_command()` | Click CLI 命令 `flask --app flaskr init-db`，供命令行初始化数据库 |
| `init_app(app)` | 将 `close_db` 和 `init-db` 命令注册到 Flask 应用 |

**SQLite 行工厂**：`g.db.row_factory = sqlite3.Row`，使查询结果可以用列名访问（如 `row["username"]`）。

**时间戳处理**：注册了自定义转换器，将 SQLite 的 `TIMESTAMP` 字段自动转为 Python `datetime` 对象。

### 3.3 数据库表结构 — `flaskr/schema.sql`

```sql
-- user 表：用户
CREATE TABLE user (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,    -- 用户名唯一
  password TEXT NOT NULL            -- 存储加密后的密码哈希
);

-- post 表：文章
CREATE TABLE post (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  author_id INTEGER NOT NULL,       -- 外键，关联 user.id
  created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  FOREIGN KEY (author_id) REFERENCES user (id)
);
```

### 3.4 认证模块 — `flaskr/auth.py`

**蓝图**：`auth`，URL 前缀 `/auth`

| 路由 | 方法 | 功能 |
|------|------|------|
| `/auth/register` | GET/POST | 注册新用户 |
| `/auth/login` | GET/POST | 用户登录 |
| `/auth/logout` | GET | 用户登出 |

**核心逻辑**：

- **`login_required` 装饰器**：检查 `g.user` 是否存在，若未登录则重定向到登录页。用于保护需要登录的视图。
- **`load_logged_in_user()`**：注册为 `before_app_request`，在每个请求前从 `session` 中读取 `user_id` 并加载对应用户到 `g.user`。
- **注册流程**：验证用户名/密码非空 → 检查用户名是否重复 → 用 `generate_password_hash` 加密密码 → 存入数据库 → 重定向到登录页。
- **登录流程**：查询用户 → 用 `check_password_hash` 验证密码 → 将 `user_id` 存入 `session` → 重定向到首页。
- **登出流程**：清空 `session`，重定向到首页。

### 3.5 博客模块 — `flaskr/blog.py`

**蓝图**：`blog`，无 URL 前缀

| 路由 | 方法 | 功能 | 权限 |
|------|------|------|------|
| `/` | GET | 文章列表首页 | 公开 |
| `/create` | GET/POST | 创建新文章 | 需登录 |
| `/<int:id>/update` | GET/POST | 编辑文章 | 需登录 + 作者本人 |
| `/<int:id>/delete` | POST | 删除文章 | 需登录 + 作者本人 |

**核心逻辑**：

- **`index()`**：JOIN 查询 `post` 和 `user` 表，按 `created` 降序排列，展示所有文章。
- **`get_post(id, check_author)`**：根据 ID 获取文章，可选检查当前用户是否为作者。文章不存在返回 404，非作者返回 403。
- **`create()`**：校验标题非空 → 插入 `post` 表（`author_id` 取自 `g.user["id"]`）。
- **`update(id)`**：先获取文章并校验权限 → 更新标题和正文。
- **`delete(id)`**：先获取文章并校验权限 → 执行 DELETE。

---

## 四、模板系统详解

### 4.1 基础模板 — `base.html`

所有页面的骨架，使用 Jinja2 模板继承：

- **导航栏**：显示站点标题 "Flaskr"（链接到首页） + 根据登录状态显示不同按钮（未登录：Register / Log In；已登录：用户名 + Log Out）。
- **消息闪现**：遍历 `get_flashed_messages()` 显示操作反馈（如错误提示）。
- **内容块**：定义 `{% block title %}`、`{% block header %}`、`{% block content %}` 供子模板填充。

### 4.2 各页面模板

| 模板文件 | 继承 | 说明 |
|----------|------|------|
| `auth/register.html` | `base.html` | 注册表单：用户名 + 密码 |
| `auth/login.html` | `base.html` | 登录表单：用户名 + 密码 |
| `blog/index.html` | `base.html` | 文章列表，登录用户看到 "New" 按钮，自己的文章显示 "Edit" 链接 |
| `blog/create.html` | `base.html` | 创建文章表单：标题 + 正文（textarea） |
| `blog/update.html` | `base.html` | 编辑文章表单 + 删除按钮（带 JS 确认弹窗） |

### 4.3 模板关键技术点

- **表单回填**：编辑页使用 `request.form['title'] or post['title']`，优先使用提交失败后的表单数据，否则使用数据库中的原始值。
- **日期格式化**：`post['created'].strftime('%Y-%m-%d')` 在模板中直接调用 Python 方法。
- **条件渲染**：`{% if g.user['id'] == post['author_id'] %}` 判断当前用户是否为文章作者，决定是否显示编辑链接。

---

## 五、静态资源 — `style.css`

纯 CSS 样式，无框架依赖：

- 整体布局：最大宽度 960px，居中显示，白色背景
- 导航栏：灰色背景，flex 布局
- 文章列表：每篇文章显示标题、作者、日期、正文，文章间用 `<hr>` 分隔
- 表单：纵向 flex 布局，textarea 最小高度 12em，可垂直缩放
- 消息闪现：浅蓝色背景，带边框
- 危险按钮（删除）：红色文字 `#cc2f2e`

---

## 六、测试体系

### 6.1 测试配置 — `tests/conftest.py`

提供四个核心 fixture：

| Fixture | 作用域 | 说明 |
|---------|--------|------|
| `app` | 每个测试函数 | 创建临时 SQLite 数据库，执行 `schema.sql` 建表 + `data.sql` 插入测试数据 |
| `client` | 每个测试函数 | Flask 测试客户端，用于模拟 HTTP 请求 |
| `runner` | 每个测试函数 | Click CLI 测试运行器，用于测试命令行命令 |
| `auth` | 每个测试函数 | `AuthActions` 辅助类，封装 `login()` 和 `logout()` 方法 |

### 6.2 测试数据 — `tests/data.sql`

预置两个用户和一条文章：
- 用户 `test`（密码 `test`，id=1）+ 用户 `other`（密码 `other`，id=2）
- 文章：`test title` / `test\nbody`，作者为用户 `test`，创建时间 `2018-01-01`

### 6.3 测试文件

| 文件 | 测试内容 |
|------|----------|
| `test_factory.py` | 应用工厂创建、配置覆盖、`/hello` 路由 |
| `test_db.py` | 数据库连接获取与关闭、`init-db` CLI 命令 |
| `test_auth.py` | 注册（成功/重复/空输入）、登录（成功/错误密码/错误用户名）、登出、session 验证 |
| `test_blog.py` | 首页渲染、登录保护、作者权限（403）、文章存在性（404）、CRUD 操作、表单验证 |

**测试覆盖的核心场景**：
- ✅ 正常流程（注册 → 登录 → 创建文章 → 编辑 → 删除）
- ✅ 权限校验（未登录访问受保护页面 → 302 重定向；非作者编辑/删除 → 403）
- ✅ 边界条件（空标题提交、不存在的文章 ID → 404、重复用户名注册）
- ✅ 参数化测试（`@pytest.mark.parametrize` 覆盖多种输入组合）

---

## 七、项目配置 — `pyproject.toml`

```toml
[project]
name = "flaskr"
version = "1.0.0"
dependencies = ["flask"]                    # 唯一运行时依赖

[project.optional-dependencies]
test = ["pytest"]                           # 测试依赖

[build-system]
requires = ["flit_core<4"]                  # 使用 flit 构建
build-backend = "flit_core.buildapi"

[tool.pytest.ini_options]
testpaths = ["tests"]                       # pytest 测试路径
```

唯一的外部依赖是 `flask`，其余全部使用 Python 标准库。

---

## 八、快速开始

### 8.1 环境准备

```bash
# 创建虚拟环境
python -m venv .venv

# 激活虚拟环境
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

# 安装项目（开发模式）
pip install -e .
```

### 8.2 初始化数据库

```bash
flask --app flaskr init-db
```

执行后会在 `instance/` 目录下生成 `flaskr.sqlite` 数据库文件。

### 8.3 启动开发服务器

```bash
flask --app flaskr run --debug
```

浏览器访问 **http://127.0.0.1:5000** 即可看到博客首页。

### 8.4 运行测试

```bash
pip install -e '.[test]'
pytest
```

---

## 九、请求流程图

```
浏览器请求
    │
    ▼
┌──────────────────────────────────────┐
│  before_app_request                  │
│  auth.load_logged_in_user()          │
│  从 session 加载用户 → g.user        │
└──────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────┐
│  路由分发                            │
│  /              → blog.index()       │
│  /auth/register → auth.register()    │
│  /auth/login    → auth.login()       │
│  /auth/logout   → auth.logout()      │
│  /create        → blog.create()      │
│  /<id>/update   → blog.update()      │
│  /<id>/delete   → blog.delete()      │
└──────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────┐
│  视图函数执行                        │
│  get_db() → 获取数据库连接           │
│  业务逻辑处理                        │
│  render_template() → 渲染模板        │
│  或 redirect() → 重定向              │
└──────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────┐
│  teardown_appcontext                 │
│  db.close_db() → 关闭数据库连接      │
└──────────────────────────────────────┘
```

---

## 十、数据流示意

```
用户注册/登录
    │
    ├── POST /auth/register
    │   username + password → generate_password_hash() → INSERT INTO user
    │
    └── POST /auth/login
        username + password → check_password_hash() → session["user_id"] = user.id

文章 CRUD
    │
    ├── GET /  → SELECT post JOIN user → 渲染文章列表
    ├── POST /create → INSERT INTO post (title, body, author_id)
    ├── POST /<id>/update → UPDATE post SET title, body WHERE id=?
    └── POST /<id>/delete → DELETE FROM post WHERE id=?
```

---

## 十一、常见扩展方向

基于当前项目结构，可以方便地扩展以下功能：

1. **分页**：在 `blog.index()` 中添加 LIMIT/OFFSET 查询
2. **文章分类/标签**：新增 `category` 或 `tag` 表，多对多关系
3. **评论功能**：新增 `comment` 表，关联 `post` 和 `user`
4. **用户头像**：扩展 `user` 表，添加 `avatar` 字段
5. **Markdown 渲染**：在模板中使用 `markdown` 库渲染正文
6. **REST API**：新增 `api` 蓝图，返回 JSON 数据
7. **部署配置**：将 `SECRET_KEY` 等敏感配置移至环境变量或 `instance/config.py`

---

## 十二、注意事项

- **SECRET_KEY**：默认值为 `"dev"`，生产环境必须在 `instance/config.py` 中覆盖。
- **数据库路径**：SQLite 文件存储在 `instance/` 目录下，该目录已加入 `.gitignore`。
- **`init-db` 命令**：每次执行会**清空所有数据**并重建表，仅用于开发/测试环境。
- **密码安全**：使用 Werkzeug 的 `pbkdf2:sha256` 算法加密，不存储明文密码。
- **CSRF 保护**：当前版本未包含 CSRF 保护，生产环境建议使用 Flask-WTF 或类似方案。