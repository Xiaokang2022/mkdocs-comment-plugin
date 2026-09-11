# 评论后端服务

FastAPI + SQLite 实现的评论 / 表情 / 统计服务，为 `mkdocs-comment-plugin` 提供数据支撑。

## 运行

```bash
python -m pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
# 监听地址由启动参数决定，没有对应的环境变量
uvicorn main:app --host 127.0.0.1 --port 8000
```

- 交互式接口文档：<http://127.0.0.1:8000/docs>
- 数据文件默认位于 `backend/comments.db`
- 生产部署的环境变量与注意事项见仓库根目录 README 的「部署」一节

## 模块

| 文件 | 职责 |
| --- | --- |
| `main.py` | FastAPI 应用、路由、错误处理 |
| `database.py` | SQLite 表结构与全部查询 |
| `markdown_render.py` | Markdown 渲染（扩展集可配）、裸链接自动识别、bleach 白名单净化 |
| `security.py` | 客户端 IP 解析、删除令牌、按 IP 限流 |
| `settings.py` | 环境变量 / `.env` 配置 |
| `schemas.py` | Pydantic 请求与响应模型 |

### 关于渲染顺序

自动链接（把裸 URL 变成可点击链接）由 `markdown_render.py` 里的 Markdown
扩展完成，而不是用 `bleach.linkify`。原因是 `linkify` 会重新序列化整棵 DOM 树，
把 Markdown 已经生成的实体中的 `&` 再转义一次 —— 代码块里的
`print("x")` 会变成字面量 `&quot;`。放在 Markdown 阶段处理可以保证只有一次转义，
同时 `bleach.clean` 仍是唯一的净化入口。

### 关于扩展集

扩展集来自 `settings.markdown_extensions`（环境变量 `MKC_MARKDOWN_EXTENSIONS`），
**必须与站点 `mkdocs.yml` 的 `markdown_extensions` 对齐**，否则同一段文本在页面里和评论预览里
会渲染成两个样子。它是配置项而不是写死的清单，原因就是这个：列表属于站点，不属于后端。

`Markdown` 实例是**有状态**的（`reset()` 会改写内部映射），所以不能跨线程共用。
每个工作线程各持一份，配置变更时用世代号作废缓存（见 `configure()` / `_instance()`）。

白名单跟着扩展集一起长大：`details` / `summary`、`pymdownx.tabbed` 需要的 `input` / `label`、
以及各扩展挂在主题样式上的 `class`（`admonition-title`、`footnote-ref`、`highlight`、
高亮的 token 类名……）。最后一个看起来宽松，但类名无法执行任何东西，而枚举类名既列不完、
也会在下一次换高亮主题时把输出削掉。

## 数据表

```text
comments       评论（含楼中楼）；无人回复时硬删除，有回复时留墓碑（deleted_at）；
               author 是读者看到的名字，client_ip 是单独下发的地址
reactions      表情记录，(target_type, target_id, emoji, visitor_id) 唯一；
               同时存 author / client_ip，供前端悬停显示「谁点了赞」
page_stats     页面浏览量
visitors       访客 ID -> 昵称，一人一条；表情名单的真实来源
```

新增列由 `Database._ensure_columns` 在启动时按 `PRAGMA table_info` 幂等补齐，
`visitors` 建表与回填也由 `init_schema` 完成，因此从旧版本升级只需直接启动，无需手工迁移。

### 为什么昵称存在 `visitors` 里

最开始昵称只是写在 `reactions.author` 上，相当于**写入那一刻的快照**。这导致两个问题：

- 早于该列存在时写入的行没有昵称，于是永远不出现在悬停名单里；
- 访客后来改了昵称，旧记录还停在老名字上。

把「访客 → 昵称」独立成一张表后，昵称只有一个来源，访客后来留的名字会**回溯补全他所有的历史记录**。
行上的 `author` 仍保留，作为从未回流过的访客的兼底。

三条规则值得记住：

- 只记**真实昵称**，不记匿名占位名或 IP 回落值，否则一次匿名操作会把真名字改掉；
- 只记**能标识浏览器的访客 ID**，`ip-…` 前缀（由 IP + User-Agent 推导）是同一出口下所有人共用的，
  给它记名字会把一个人的昵称贴到陌生人身上；
- 写入接口因此有两个名字参数：`display_name`（读者看到的名字）与 `nickname`
  （只包含访客真正手填的内容），只有后者会写进 :table:`visitors`。

### 匿名署名与 IP

留空昵称不再回落成 IP，而是 `settings.anonymous_name`（默认「匿名用户」），
地址只作为单独的 `author_ip` 字段下发，由前端以淡色小字显示。这样才可能真正把 IP 藏起来：
一旦地址已经变成名字，就只能改库了。

| 配置 | 作用 |
| --- | --- |
| `MKC_DEFAULT_AUTHOR` | `anonymous`（默认）\| `ip` \| 一个固定昵称 |
| `MKC_ANONYMOUS_NAME` | 留空时显示的名字 |
| `MKC_SHOW_AUTHOR_IP` | `anonymous`（仅未署名）\| `always` \| `never` |

`shows_author_ip()` 在 `serialize_comment()` 里生效：不发布时字段根本不出现，
而不是让前端自己藏。

启动时会执行一次幂等归并：把 `author == client_ip`（即旧版把地址当名字）的行改写成匿名昵称。
地址仍在 `client_ip` 里，所以没有信息丢失；把 `MKC_DEFAULT_AUTHOR` 设为 `ip`
（数据库构造时传空占位名）会跳过这一步。

### 删除策略

`DELETE /api/v1/comments/{id}` 会自动在两种模式间选择，并在响应里回报 `mode` 与 `removed`：

| 条件 | mode | 行为 |
| --- | --- | --- |
| 无任何回复（`parent_id` 指向它的评论） | `hard` | 删除该行及其表情 |
| 有回复 | `soft` | 置 `deleted_at`、清空正文；保留 `author` 与点赞 |

墓碑保留作者名是为了让逐层回复中的 `@提及` 仍能解析；保留点赞是因为
附着在这条线索上的互动不因正文消失而失效。

但墓碑只是临时结构：`delete_comment_only` 在删掉一条子评论后会沿着 parent 链往上走，
把**已经没有任何子节点**的墓碑一并腾清（连同它的点赞）。否则「先删一层、再删二层」
会在页面上永远留下一个空的占位。`removed` 字段就是为此存在的：它列出本次真正被移除的全部 id，
硬删除可能一次带走多个，前端需要把它们都从列表里移除。

## 备份

```bash
sqlite3 comments.db ".backup 'backup-$(date +%F).db'"
```

## 限流

写操作（发表评论、切换表情）与浏览量使用**独立**的按 IP 限流桶：

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `MKC_RATE_LIMIT_REQUESTS` / `MKC_RATE_LIMIT_WINDOW` | `30` / `60` | 写操作上限 |
| `MKC_VIEW_RATE_LIMIT_REQUESTS` / `MKC_VIEW_RATE_LIMIT_WINDOW` | `60` / `60` | 浏览量上限 |

浏览量每次请求都会计数（刷新即 +1），限流仅用于阻止单个 IP 刷量；超限返回 `429`，
前端会静默保留当前数值。

## 审核

```bash
# 硬删除整条评论及其回复（需要 MKC_ADMIN_TOKEN）
curl -X DELETE "http://127.0.0.1:8000/api/v1/comments/<id>?hard=true" \
  -H "X-Admin-Token: <token>"
```

也可以使用仓库根目录的维护脚本：

```bash
MKC_ADMIN_TOKEN=<token> python ../scripts/admin.py list /guide
MKC_ADMIN_TOKEN=<token> python ../scripts/admin.py purge /guide
```
