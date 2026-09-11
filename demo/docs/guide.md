# 使用指南

## 两分钟接入

1. 启动后端服务
2. 在 `mkdocs.yml` 中启用插件
3. `mkdocs serve` 预览

## 配置项速查

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `api_base` | str | `/api/v1` | 后端 API 根地址 |
| `title` | str | `评论` | 评论区标题 |
| `page_selector` | str | `.md-content__inner` | 挂载容器选择器 |
| `reactions` | list | `👍 ❤️ 😄 🎉 🚀` | 单条评论可用的表情 |
| `page_reactions` | list | 同 `reactions` | 页面级表情 |
| `emoji_picker` | list | 24 个表情 | 表情选择面板 |
| `show_stats` | bool | `true` | 是否显示统计栏 |
| `count_views` | bool | `true` | 是否统计浏览量 |
| `per_page` | int | `20` | 每页根评论数 |
| `default_author` | str | `anonymous` | 昵称留空时的处理：`anonymous` 用 `anonymous_name`，`ip` 用访客 IP，也可直接写一个默认昵称 |
| `anonymous_name` | str | `匿名用户` | 未填写昵称时显示的名字 |
| `require_author` | bool | `false` | 是否强制填写昵称 |
| `allow_delete` | bool | `true` | 是否允许自助删除 |
| `labels` | dict | `{}` | 覆盖任意界面文案（置空则隐藏该元素） |

### 外观与交互

以下选项只影响展示，不影响后端数据。

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `accent_color` | str | 空 | 强调色，留空则跟随主题主色 |
| `avatar_style` | str | `initial` | `none` 可完全隐藏头像；未署名时用主题的用户图标 |
| `avatar_shape` | str | `circle` | `circle` 或 `square` |
| `density` | str | `comfortable` | `compact` 收紧行距，适合长楼 |
| `editor_rows` | int | `4` | 评论输入框默认行数 |
| `sort_order` | str | `newest` | `oldest` 按时间正序 |
| `time_style` | str | `relative` | `absolute` 显示具体日期时间 |
| `remember_author` | bool | `true` | 是否记住昵称 |
| `reply_quote` | bool | `true` | 回复时是否自动带 `@提及` |

## 本页面也有评论区

向下滚动即可看到——每个页面拥有独立的评论流。

```python
def hello():
    print("支持语法高亮")
```
