# astrbot_plugin_jmcomic

## 使用方式

把整个 `astrbot_plugin_jmcomic` 目录放到 AstrBot 的 `data/plugins/` 下，然后在 WebUI 安装依赖并启用插件。

## 命令

- `/jm搜索 <关键词>`
- `/jm详情 <本子ID>`
- `/jm下载 <本子ID> [pdf|zip]`
- `/jm任务 <任务ID>`
- `/jm推荐 <关键词>`
- `/jm推荐下载 <关键词> [pdf|zip]`

## LLM Tools

- `jmcomic_search`
- `jmcomic_browse`
- `jmcomic_info`
- `jmcomic_download`
- `jmcomic_recommend`
- `jmcomic_recommend_download`
- `jmcomic_task_status`

## 配置

插件配置项在 `_conf_schema.json` 中，可以在 AstrBot WebUI 里直接调整：

- 默认导出格式
- 下载密码
- 并发数
- 缓存保留时间
- 工具名称
- 群组白名单开关和白名单群号列表
- 是否把密码返回给 AI

## 说明

- `/jm推荐` 只返回候选列表，不自动下载。
- `/jm推荐下载` 适合明确要求直接下载时使用。
- 详情和推荐结果中的页数口径是整本所有章节图片总数。
