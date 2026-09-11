---
comments: true
---

# 使用指南

## 两分钟接入

1. 启动后端服务
2. 在 `mkdocs.yml` 中启用插件（并确保 `markdown_extensions` 里有 `meta`）
3. 在需要评论区的页面头部写上 `comments: true`
4. `mkdocs serve` 预览

本页的头部就是这样写的，所以页面底部有评论区。演示站里的[「没有评论区的一页」](no-comments.md)
没有写这一行，因此那一页既没有评论区，也不会发出任何评论相关的请求。

> 不要用 `page_selector` 来「挑选」哪些页面有评论区：它是个 CSS 选择器，
> 会在所有命中的页面上生效，等于整站开启。它只能在已经开启的页面里换一个渲染位置。

## 配置项速查

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `api_base` | str | `/api/v1` | 后端 API 根地址 |
| `title` | str | `评论` | 评论区标题 |
| `meta_key` | str | `comments` | 页面元数据里用哪个字段表示「本页要评论区」 |
| `meta_default` | bool | `false` | 没写该字段时的默认值；`false` 即默认不开 |
| `page_selector` | str | 空 | 在**已开启评论区的页面里**换一个挂载容器；只能选位置，不能决定哪一页有评论区 |
| `reactions` | list | `👍 ❤️ 😄 🎉 🚀 👀` | 单条评论可用的表情 |
| `page_reactions` | list | 同 `reactions` | 页面级表情 |
| `emoji_picker` | list | 24 个表情 | 表情选择面板 |
| `show_stats` | bool | `true` | 是否显示统计栏 |
| `count_views` | bool | `true` | 是否统计浏览量 |
| `per_page` | int | `20` | 每页根评论数 |
| `default_author` | str | `anonymous` | 昵称留空时的处理：`anonymous` 用 `anonymous_name`，`ip` 用访客 IP，也可直接写一个默认昵称 |
| `anonymous_name` | str | `匿名用户` | 未填写昵称时显示的名字；需与后端 `MKC_ANONYMOUS_NAME` 一致 |
| `require_author` | bool | `false` | 是否强制填写昵称 |
| `allow_delete` | bool | `true` | 后端与前端都开时才显示删除按钮；能不能删由后端按来源地址判定，昵称不参与 |
| `labels` | dict | `{}` | 覆盖任意界面文案（置空则隐藏该元素）；空内容提示是 `emptyContent` |

> IP 展示给谁由**后端**的 `MKC_SHOW_AUTHOR_IP` 决定（默认 `always`，即所有访客都显示）。
> `anonymous` 只标未署名的评论，`never` 则完全不显示且不会下发字段。

### 外观与交互

以下选项只影响展示，不影响后端数据。

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `accent_color` | str | 空 | 强调色，留空则跟随主题（选中色取主题主色、高亮色取主题的链接悬停色） |
| `avatar_style` | str | `initial` | `none` 可完全隐藏头像；未署名时用主题的用户图标 |
| `avatar_shape` | str | `circle` | `circle` 或 `square` |
| `density` | str | `comfortable` | `compact` 收紧行距，适合长楼 |
| `editor_rows` | int | `4` | 评论输入框默认行数 |
| `sort_order` | str | `newest` | `oldest` 按时间正序 |
| `time_style` | str | `relative` | `absolute` 显示具体日期时间 |
| `remember_author` | bool | `true` | 是否记住昵称 |
| `reply_quote` | bool | `true` | 回复时是否自动带 `@提及` |

配色分两层：**选中**（已点赞的胶囊填充、回复竖线、默认按钮）用主题主色，
**高亮**（输入框聚焦的边框与光晕、昵称框聚焦、胶囊悬停）用主题里链接悬停时的颜色
`--md-accent-fg-color`——把鼠标移到正文任意链接上看到的那个色。

评论作者旁边的「我」标签由服务端按**来源地址**判定（`is_mine`），
和能不能删除是同一条规则：清掉浏览器数据、换个浏览器都还是「我」，
同一个出口下的其他人则不是。关掉 `allow_delete` 不会让标签消失——
删不了不等于不是你写的。

## 本页面也有评论区

向下滚动即可看到——每个页面拥有独立的评论流。

提交评论请按「发表评论」按钮：回车在输入框里是换行，在昵称框里也不会把评论发出去。

每条评论右上角有一个 Markdown 图标：点它可以把该条评论切到 Markdown 原文（含代码围栏与缩进），
再点一次回到渲染结果。原文与渲染结果是一起下发的，所以切换不会重新加载页面，也不会关掉正在填写的回复框。

```python
def hello():
    print("支持语法高亮")
```
