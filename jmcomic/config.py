from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass
class ToolConfig:
    enabled: bool
    tool_name: str
    description: str


@dataclass
class PluginConfig:
    default_format: str = "pdf"
    file_name: str = "{{name}} ({{id}})"
    password: str = ""
    allow_ai_change_format: bool = True
    zip_level: int = 6
    max_download_pages: int = 300
    minimum_free_disk_mb: int = 3000
    retry_count: int = 5
    return_password_to_ai: bool = False
    cache: bool = True
    file_expire_hours: int = 24
    concurrent_task_limit: int = 1
    concurrent_download_limit: int = 8
    concurrent_decode_limit: int = 4
    group_whitelist_enabled: bool = False
    group_whitelist: list[str] = field(default_factory=list)
    debug: bool = False
    search_tool: ToolConfig = field(
        default_factory=lambda: ToolConfig(
            enabled=True,
            tool_name="jmcomic_search",
            description="当用户要求搜索、查找、列出某个关键词相关 JMComic 本子时，必须调用这个工具，返回真实候选列表，不要凭空编造结果。",
        )
    )
    browse_tool: ToolConfig = field(
        default_factory=lambda: ToolConfig(
            enabled=True,
            tool_name="jmcomic_browse",
            description="当用户要求看热门、排行、分类浏览或按时间范围找本子时，使用这个工具返回真实列表。",
        )
    )
    info_tool: ToolConfig = field(
        default_factory=lambda: ToolConfig(
            enabled=True,
            tool_name="jmcomic_info",
            description="当用户已经给出本子 ID，或你需要确认章节数、总页数、标签等详情时，必须调用这个工具。",
        )
    )
    download_tool: ToolConfig = field(
        default_factory=lambda: ToolConfig(
            enabled=True,
            tool_name="jmcomic_download",
            description="当用户已经明确给出本子 ID 并要求下载为 PDF 或 ZIP 时，必须调用这个工具，不要只口头回复。",
        )
    )
    recommend_tool: ToolConfig = field(
        default_factory=lambda: ToolConfig(
            enabled=True,
            tool_name="jmcomic_recommend",
            description="当用户提出模糊需求，例如“来点高质量的本子”“推荐几个尼尔的本子”“先给我看看有哪些”，必须调用这个工具返回 3 到 5 个候选列表，不要先空口追问。",
        )
    )
    recommend_download_tool: ToolConfig = field(
        default_factory=lambda: ToolConfig(
            enabled=True,
            tool_name="jmcomic_recommend_download",
            description="当用户明确要求直接下载但没有给出本子 ID，例如“帮我下一个尼尔的本子 pdf”“来个 mmk 的 zip，换一个不一样的”，必须调用这个工具自动挑选并下载，不要只口头答应。",
        )
    )
    task_status_tool: ToolConfig = field(
        default_factory=lambda: ToolConfig(
            enabled=True,
            tool_name="jmcomic_task_status",
            description="当下载任务已提交后，如果需要等待最终文件结果，必须调用这个工具查询状态。",
        )
    )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "PluginConfig":
        data = dict(raw or {})
        config = cls()

        for key in (
            "default_format",
            "file_name",
            "password",
            "allow_ai_change_format",
            "zip_level",
            "max_download_pages",
            "minimum_free_disk_mb",
            "retry_count",
            "return_password_to_ai",
            "cache",
            "file_expire_hours",
            "concurrent_task_limit",
            "concurrent_download_limit",
            "concurrent_decode_limit",
            "group_whitelist_enabled",
            "group_whitelist",
            "debug",
        ):
            if key in data:
                setattr(config, key, data[key])

        for key, default in (
            ("search_tool", config.search_tool),
            ("browse_tool", config.browse_tool),
            ("info_tool", config.info_tool),
            ("download_tool", config.download_tool),
            ("recommend_tool", config.recommend_tool),
            ("recommend_download_tool", config.recommend_download_tool),
            ("task_status_tool", config.task_status_tool),
        ):
            tool_data = data.get(key) or {}
            setattr(
                config,
                key,
                ToolConfig(
                    enabled=tool_data.get("enabled", default.enabled),
                    tool_name=tool_data.get("tool_name", default.tool_name),
                    description=tool_data.get("description", default.description),
                ),
            )

        return config
