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
| `security.py` | 客户端 IP 解析、由地址推导身份与归属、删除令牌哈希、按 IP 限流 |
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
```

新增列由 `Database._ensure_columns` 在启动时按 `PRAGMA table_info` 幂等补齐，
因此从旧版本升级只需直接启动，无需手工迁移。启动同时还做两件清理：
孤儿表情（`delete_orphan_reactions`）和无回复的已删除占位（`purge_childless_tombstones`，
见「不变式：墓碑一定还挂着回复」）。

### 身份就是地址

谁点的赞、谁能删，全部由**请求的来源地址**推导（`security.make_visitor_id`），
不采用任何客户端自报的 ID：请求体里带着 `visitor_id` 也会被忽略。

这样做的原因是「一人一票」必须有一个客户端伪造不了的身份，而匿名评论区没有登录。
代价同样明确：**一个出口地址背后可能是很多人**（公司、学校、咖啡馆、运营商网络），
他们会被当作同一个人：同一个表情只能点一次，且会互相抵消。
要区分同一个地址下的人，只有引入登录。

同一个判断也回答了「这条是不是我写的」：`serialize_comment()` 把它写在响应的 `is_mine` 里，
前端拿它渲染评论作者旁的「我」标签。以前这个标签是前端看自己 localStorage 里的删除令牌得出的，
改成地址判身份之后它就不准了——清掉站点数据还是同一个人，却丢了标签；
接手别人浏览器的人又会在自己没写过的评论上看到「我」。
`is_mine` 与 `can_delete` 是两个字段而不是一个：`MKC_ALLOW_DELETE=0` 只改变能不能删，
不改变是谁写的，标签必须照旧显示。与 `can_delete` 不同的是，
**墓碑也保留 `is_mine`**：正文没了，作者没变，而且同一串里活下来的回复继续带着标签，
只有根评论没有反而像坏了。

旧版本用 `visitors` 表把「浏览器 ID → 昵称」记下来，好让悬停名单显示名字。
身份改成地址后这张表就空了：一个地址背后坐着多少人无从得知，
拿其中一个人的昵称去覆盖其他人的记录只会张冠李戴。所以新库不再建它，
`reactions.author` 保留**写入时手填的名字**作为名单的唯一来源。
已有库里的 `visitors` 表**不会被动删除**（那里可能有读者留下的名字），只是不再读写。

### 匿名署名与 IP

留空昵称不再回落成 IP，而是 `settings.anonymous_name`（默认「匿名用户」），
地址只作为单独的 `author_ip` 字段下发，由前端以淡色小字显示。这样才可能真正把 IP 藏起来：
一旦地址已经变成名字，就只能改库了。

| 配置 | 作用 |
| --- | --- |
| `MKC_DEFAULT_AUTHOR` | `anonymous`（默认）\| `ip` \| 一个固定昵称 |
| `MKC_ANONYMOUS_NAME` | 留空时显示的名字 |
| `MKC_SHOW_AUTHOR_IP` | `always`（所有人，默认）\| `anonymous`（仅未署名）\| `never` |

默认对所有访客下发地址：在按地址判身份的前提下，地址是读者唯一能核对的东西，
只给未署名的人看反而让最需要辨认的那条留言成为唯一没线索的一条。
`shows_author_ip()` 在 `serialize_comment()` 里生效：不发布时字段根本不出现，
而不是让前端自己藏。

启动时会执行一次幂等归并：把 `author == client_ip`（即旧版把地址当名字）的行改写成匿名昵称。
地址仍在 `client_ip` 里，所以没有信息丢失；把 `MKC_DEFAULT_AUTHOR` 设为 `ip`
（数据库构造时传空占位名）会跳过这一步。

### 删除策略

`DELETE /api/v1/comments/{id}` 会先判权限，再在两种模式间选择，并在响应里回报 `mode` 与 `removed`。

**谁有权删**（`security.owns_row`）：

| 凭据 | 说明 |
| --- | --- |
| 来源地址 == 该行的 `client_ip` | 默认规则，与「谁点的赞算我的」同一套身份 |
| `X-Delete-Token` 匹配 | 发布时下发的一次性令牌，库里只存 SHA-256 |
| `X-Admin-Token` | 硬删除整串回复 |

**昵称不参与这个判断**，这是刻意的：名字是自己声明的，谁都能取同一个，
靠名字放行的删除权等于把它送给任何读过页面的人。权限同样不看 `author` 列。

`serialize_comment()` 会把结论写在响应的 `can_delete` 里（同时要求 `MKC_ALLOW_DELETE` 开着、
且不是墓碑），前端据此显示删除按钮——它自己判断不了，因为 `MKC_SHOW_AUTHOR_IP=never`
时地址根本不下发。同一个判断还写在 `is_mine` 里供「我」标签使用，
两者分开的原因见上面的「身份就是地址」。

代价要说清楚：**一个出口地址背后的所有人共用一个删除权**，可以互相删除评论。
这与他们共用点赞身份是同一个原因，要区分只能引入登录；`MKC_ALLOW_DELETE=0`
可以整体关掉自助删除。

**删掉之后怎么处理**：

| 条件 | mode | 行为 |
| --- | --- | --- |
| 无任何回复（`parent_id` 指向它的评论） | `hard` | 删除该行及其表情 |
| 有回复 | `soft` | 置 `deleted_at`、清空正文；保留 `author` 与点赞 |

墓碑保留作者名是为了让逐层回复中的 `@提及` 仍能解析；保留点赞是因为
附着在这条线索上的互动不因正文消失而失效。**不下发 `author_ip`**：地址不参与回复的上下文，
而地址默认是展示给所有读者的，提出删除的人并没有同意继续被它认出来。
`content` / `content_html` 都清空，因此连「查看源码」也无从泄露原文。

但墓碑只是临时结构：`delete_comment_only` 在删掉一条子评论后会沿着 parent 链往上走，
把**已经没有任何子节点**的墓碑一并腾清（连同它的点赞）。否则「先删一层、再删二层」
会在页面上永远留下一个空的占位。`removed` 字段就是为此存在的：它列出本次真正被移除的全部 id，
硬删除可能一次带走多个，前端需要把它们都从列表里移除。

### 不变式：墓碑一定还挂着回复

上面那条规则等价于一句话：`deleted_at IS NOT NULL` 的行必然有子行。
正常路径都由 `delete_comment_only` / `_purge_orphaned_tombstones` 维持，所以
**当前代码不会造出无回复的墓碑**；这种行只会从别处进库——早于该逻辑的版本写下的数据、
从旧备份恢复的库、被手工改过的行。

`db.purge_childless_tombstones()` 在**服务启动时**清掉它们（并连同点赞），
日志会写「已清理 N 条无回复的已删除占位」。它自底向上循环删除，直到没有可删的，
因此一条墓碑链会一次清完；幂等，也可以随时手动再调。

之所以要自愈而不是只依赖删除时的清理：读者分不清「服务端的陈年残留」和
「现在就坏着」，页面上一个空的「该评论已被删除」占位在两种情况下都像 bug。

> 排查时的第一步是看它有没有子行——
> `select c.id, (select count(*) from comments k where k.parent_id = c.id) from comments c where c.deleted_at is not null`。
> 为 0 就是需要被清掉的残留，大于 0 则说明那条线索还在用它，属于正常状态。

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
