from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import Field
from pydantic.dataclasses import dataclass as pydantic_dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from .downloader import DownloadAlbumInput

DEFAULT_TASK_STATUS_WAIT_MS = 3 * 60 * 1000


def _format_search_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "title": item.get("name") or "",
        "author": item.get("author") or "",
        "description": item.get("description") or "",
        "cover": item.get("image") or "",
        "category": ((item.get("category") or {}).get("title")) or "",
        "subcategory": ((item.get("category_sub") or {}).get("title")) or "",
    }


def _tool_access_denied(plugin: Any, event: Any) -> str | None:
    if event is None:
        return "current event is missing"
    denied = plugin._check_group_access(event)
    return denied


def _sanitize_task_payload_for_llm(payload: dict[str, Any]) -> dict[str, Any]:
    copied = json.loads(json.dumps(payload))
    result = copied.get("result")
    if isinstance(result, dict):
        file_path = result.pop("file_path", None)
        if file_path:
            copied["file_delivery"] = {
                "mode": "auto_sent_by_plugin",
                "note": "The plugin already sent the file to the user session automatically. Do not send it again."
            }
    return copied


@pydantic_dataclass
class JmSearchTool(FunctionTool[AstrAgentContext]):
    plugin: Any = field(default=None, repr=False)
    name: str = "jmcomic_search"
    description: str = "当用户要求搜索、查找、列出某个关键词相关 JMComic 本子时，必须调用这个工具，返回真实候选列表，不要凭空编造结果。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "limit": {"type": "number", "description": "返回数量，1-20", "default": 10},
            },
            "required": ["query"],
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        denied = _tool_access_denied(self.plugin, context.context.event)
        if denied:
            return json.dumps({"ok": False, "error": denied}, ensure_ascii=False)
        query = str(kwargs["query"]).strip()
        limit = max(1, min(int(kwargs.get("limit", 10)), 20))
        result = await self.plugin.client.search(query)
        return json.dumps(
            {
                "ok": True,
                "query": query,
                "total": int(result.get("total") or 0),
                "results": [_format_search_item(item) for item in (result.get("content") or [])[:limit]],
            },
            ensure_ascii=False,
        )


@pydantic_dataclass
class JmBrowseTool(FunctionTool[AstrAgentContext]):
    plugin: Any = field(default=None, repr=False)
    name: str = "jmcomic_browse"
    description: str = "当用户要求看热门、排行、分类浏览或按时间范围找本子时，使用这个工具返回真实列表。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "page": {"type": "number", "description": "页码", "default": 1},
                "time": {
                    "type": "string",
                    "description": "时间范围",
                    "enum": ["today", "week", "month", "all"],
                    "default": "all",
                },
                "category": {
                    "type": "string",
                    "description": "分类",
                    "enum": ["all", "doujin", "single", "short", "another", "hanman", "meiman", "doujin_cosplay", "cosplay", "3d", "english_site"],
                    "default": "all",
                },
                "orderBy": {
                    "type": "string",
                    "description": "排序方式",
                    "enum": ["latest", "view", "picture", "like", "month_rank", "week_rank", "day_rank"],
                    "default": "latest",
                },
                "limit": {"type": "number", "description": "返回数量，1-20", "default": 10},
            },
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        denied = _tool_access_denied(self.plugin, context.context.event)
        if denied:
            return json.dumps({"ok": False, "error": denied}, ensure_ascii=False)
        page = max(1, int(kwargs.get("page", 1)))
        time_range = str(kwargs.get("time", "all"))
        category = str(kwargs.get("category", "all"))
        order_by = str(kwargs.get("orderBy", "latest"))
        limit = max(1, min(int(kwargs.get("limit", 10)), 20))
        result = await self.plugin.client.browse(
            page=page,
            time_range=time_range,
            category=category,
            order_by=order_by,
        )
        return json.dumps(
            {
                "ok": True,
                "page": page,
                "total": int(result.get("total") or 0),
                "params": {"time": time_range, "category": category, "order_by": order_by},
                "results": [_format_search_item(item) for item in (result.get("content") or [])[:limit]],
            },
            ensure_ascii=False,
        )


@pydantic_dataclass
class JmInfoTool(FunctionTool[AstrAgentContext]):
    plugin: Any = field(default=None, repr=False)
    name: str = "jmcomic_info"
    description: str = "当用户已经给出本子 ID，或你需要确认章节数、总页数、标签等详情时，必须调用这个工具。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "albumId": {"type": "string", "description": "本子 ID"},
            },
            "required": ["albumId"],
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        denied = _tool_access_denied(self.plugin, context.context.event)
        if denied:
            return json.dumps({"ok": False, "error": denied}, ensure_ascii=False)
        album = await self.plugin.client.get_album_by_id(str(kwargs["albumId"]))
        total_pages = self.plugin._get_album_total_pages(album)
        return json.dumps(
            {
                "ok": True,
                "id": album.id,
                "title": album.name,
                "description": album.description,
                "cover": album.cover,
                "published_at": album.published_at,
                "authors": album.authors,
                "tags": album.tags,
                "actors": album.actors,
                "chapter_count": len(album.photos),
                "total_image_pages": total_pages,
                "page_count_note": "This is the total image count across all chapters in the whole album, not a single chapter page count.",
                "chapters": [
                    {
                        "id": chapter.id,
                        "title": chapter.name,
                        "sort": next((series.sort for series in album.series if series.id == chapter.id), ""),
                        "page_count": len(chapter.images),
                    }
                    for chapter in album.photos
                ],
                "likes": album.likes,
                "total_views": album.total_views,
            },
            ensure_ascii=False,
        )


@pydantic_dataclass
class JmDownloadTool(FunctionTool[AstrAgentContext]):
    plugin: Any = field(default=None, repr=False)
    name: str = "jmcomic_download"
    description: str = "当用户已经明确给出本子 ID 并要求下载为 PDF 或 ZIP 时，必须调用这个工具，不要只口头回复。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "albumId": {"type": "string", "description": "单个本子 ID"},
                "albumIds": {"type": "array", "description": "多个本子 ID", "items": {"type": "string"}},
                "format": {"type": "string", "description": "导出格式", "enum": ["pdf", "zip"]},
                "password": {"type": "string", "description": "导出密码"},
                "waitMs": {"type": "number", "description": "等待下载结果的毫秒数，默认 180000", "default": DEFAULT_TASK_STATUS_WAIT_MS},
            },
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        event = context.context.event
        denied = _tool_access_denied(self.plugin, event)
        if denied:
            return json.dumps({"ok": False, "error": denied}, ensure_ascii=False)
        album_ids = []
        if kwargs.get("albumId"):
            album_ids.append(str(kwargs["albumId"]))
        for item in kwargs.get("albumIds") or []:
            album_ids.append(str(item))
        album_ids = [item for item in album_ids if item]
        if not album_ids:
            return json.dumps({"ok": False, "error": "albumId or albumIds is required"}, ensure_ascii=False)

        format_value = self.plugin.config.default_format
        if self.plugin.config.allow_ai_change_format and kwargs.get("format") in {"pdf", "zip"}:
            format_value = str(kwargs["format"])
        password = (kwargs.get("password") or self.plugin.config.password or None)
        wait_ms = max(0, min(int(kwargs.get("waitMs", DEFAULT_TASK_STATUS_WAIT_MS)), 600000))

        tasks = []
        for album_id in album_ids[:10]:
            added = await self.plugin._submit_download_request(
                DownloadAlbumInput(
                    album_id=album_id,
                    format=format_value,
                    password=password,
                    unified_msg_origin=event.unified_msg_origin,
                )
            )
            tasks.append(
                {
                    "album_id": album_id,
                    "task_id": added.task_id,
                    "pending_ahead": added.pending_ahead,
                    "queue_position": added.queue_position,
                }
            )

        if len(tasks) == 1:
            task = tasks[0]
            task_payload = _sanitize_task_payload_for_llm(
                await self.plugin.get_task_status_payload(task["task_id"], wait_ms)
            )
            return json.dumps(
                {
                    "ok": True,
                    "message": "Task submitted. If the task finishes within the wait window, task_status will already contain the final result.",
                    "format": format_value,
                    "task_id": task["task_id"],
                    "album_id": task["album_id"],
                    "pending_ahead": task["pending_ahead"],
                    "queue_position": task["queue_position"],
                    "task_status": task_payload,
                },
                ensure_ascii=False,
            )

        task_statuses = [
            _sanitize_task_payload_for_llm(
                await self.plugin.get_task_status_payload(task["task_id"], wait_ms)
            )
            for task in tasks
        ]
        return json.dumps(
            {
                "ok": True,
                "message": "Tasks submitted. If tasks finish within the wait window, task_statuses will already contain final results.",
                "format": format_value,
                "tasks": tasks,
                "task_statuses": task_statuses,
            },
            ensure_ascii=False,
        )


@pydantic_dataclass
class JmRecommendDownloadTool(FunctionTool[AstrAgentContext]):
    plugin: Any = field(default=None, repr=False)
    name: str = "jmcomic_recommend_download"
    description: str = "当用户明确要求直接下载但没有给出本子 ID，例如“帮我下一个尼尔的本子 pdf”“来个 mmk 的 zip，换一个不一样的”，必须调用这个工具自动挑选并下载，不要只口头答应。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "题材、作者名或搜索关键词"},
                "format": {"type": "string", "description": "导出格式", "enum": ["pdf", "zip"]},
                "password": {"type": "string", "description": "导出密码"},
                "waitMs": {"type": "number", "description": "等待下载结果的毫秒数，默认 180000", "default": DEFAULT_TASK_STATUS_WAIT_MS},
            },
            "required": ["query"],
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        event = context.context.event
        denied = _tool_access_denied(self.plugin, event)
        if denied:
            return json.dumps({"ok": False, "error": denied}, ensure_ascii=False)
        query = str(kwargs["query"]).strip()
        picked = await self.plugin._pick_album_for_query(query, self.plugin._get_group_scope_key(event))
        if picked is None:
            return json.dumps(
                {"ok": False, "query": query, "error": "no suitable album found"},
                ensure_ascii=False,
            )

        album, reason = picked
        page_count = self.plugin._get_album_total_pages(album)
        if page_count > self.plugin.config.max_download_pages:
            return json.dumps(
                {
                    "ok": False,
                    "query": query,
                    "recommended_album": {
                        "id": album.id,
                        "title": album.name,
                        "authors": album.authors,
                        "tags": album.tags,
                        "chapter_count": len(album.photos),
                        "total_image_pages": page_count,
                        "chapter_page_preview": self.plugin._format_chapter_preview(album),
                    },
                    "error": (
                        f"recommended album exceeds max_download_pages: "
                        f"{page_count} > {self.plugin.config.max_download_pages}"
                    ),
                    "message": reason,
                },
                ensure_ascii=False,
            )
        format_value = self.plugin.config.default_format
        if self.plugin.config.allow_ai_change_format and kwargs.get("format") in {"pdf", "zip"}:
            format_value = str(kwargs["format"])
        password = kwargs.get("password") or self.plugin.config.password or None
        wait_ms = max(0, min(int(kwargs.get("waitMs", DEFAULT_TASK_STATUS_WAIT_MS)), 600000))

        added = await self.plugin._submit_download_request(
            DownloadAlbumInput(
                album_id=album.id,
                format=format_value,
                password=password,
                unified_msg_origin=event.unified_msg_origin,
            )
        )
        task_payload = _sanitize_task_payload_for_llm(
            await self.plugin.get_task_status_payload(added.task_id, wait_ms)
        )

        return json.dumps(
            {
                "ok": True,
                "query": query,
                "recommended_album": {
                    "id": album.id,
                    "title": album.name,
                    "authors": album.authors,
                    "tags": album.tags,
                    "chapter_count": len(album.photos),
                    "total_image_pages": page_count,
                    "chapter_page_preview": self.plugin._format_chapter_preview(album),
                    "reason": reason,
                },
                "download": {
                    "format": format_value,
                    "task_id": added.task_id,
                    "pending_ahead": added.pending_ahead,
                    "queue_position": added.queue_position,
                },
                "task_status": task_payload,
                "message": "Recommendation selected and download submitted. If the task finishes within the wait window, task_status will already contain the final result.",
            },
            ensure_ascii=False,
        )


@pydantic_dataclass
class JmRecommendTool(FunctionTool[AstrAgentContext]):
    plugin: Any = field(default=None, repr=False)
    name: str = "jmcomic_recommend"
    description: str = "当用户提出模糊需求，例如“来点高质量的本子”“推荐几个尼尔的本子”“先给我看看有哪些”，必须调用这个工具返回 3 到 5 个候选列表，不会直接下载。只要工具返回了候选，就应直接把候选列表展示给用户，不要先追问。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "题材、作者名或搜索关键词"},
                "limit": {"type": "number", "description": "返回候选数，1-5", "default": 3},
            },
            "required": ["query"],
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        event = context.context.event
        denied = _tool_access_denied(self.plugin, event)
        if denied:
            return json.dumps({"ok": False, "error": denied}, ensure_ascii=False)

        query = str(kwargs["query"]).strip()
        limit = max(1, min(int(kwargs.get("limit", 3)), 5))
        candidates = await self.plugin._build_recommend_candidates(
            query,
            self.plugin._get_group_scope_key(event),
            limit,
        )
        if not candidates:
            return json.dumps(
                {"ok": False, "query": query, "error": "no suitable album found"},
                ensure_ascii=False,
            )

        return json.dumps(
            {
                "ok": True,
                "query": query,
                "message": "Show these candidates to the user now. Do not ask a clarifying question first. Ask the user to choose one by ID or index, or only use jmcomic_recommend_download if the user explicitly requests direct download.",
                "candidates": [
                    {
                        "id": item["album"].id,
                        "title": item["album"].name,
                        "authors": item["album"].authors,
                        "tags": item["album"].tags,
                        "chapter_count": len(item["album"].photos),
                        "total_image_pages": item["total_pages"],
                        "chapter_page_preview": self.plugin._format_chapter_preview(item["album"]),
                        "reason": item["reason"],
                        "downloadable": item["downloadable"],
                    }
                    for item in candidates
                ],
            },
            ensure_ascii=False,
        )


@pydantic_dataclass
class JmTaskStatusTool(FunctionTool[AstrAgentContext]):
    plugin: Any = field(default=None, repr=False)
    name: str = "jmcomic_task_status"
    description: str = "当下载任务已提交后，如果需要等待最终文件结果，必须调用这个工具查询状态。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "taskId": {"type": "number", "description": "任务 ID"},
                "waitMs": {"type": "number", "description": "等待毫秒数，默认 180000", "default": DEFAULT_TASK_STATUS_WAIT_MS},
            },
            "required": ["taskId"],
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        denied = _tool_access_denied(self.plugin, context.context.event)
        if denied:
            return json.dumps({"ok": False, "error": denied}, ensure_ascii=False)
        task_id = int(kwargs["taskId"])
        wait_ms = max(0, min(int(kwargs.get("waitMs", DEFAULT_TASK_STATUS_WAIT_MS)), 600000))
        payload = _sanitize_task_payload_for_llm(
            await self.plugin.get_task_status_payload(task_id, wait_ms)
        )
        return json.dumps(payload, ensure_ascii=False)


def build_llm_tools(plugin: Any) -> list[FunctionTool[AstrAgentContext]]:
    tools = []
    for config, factory in (
        (plugin.config.search_tool, JmSearchTool),
        (plugin.config.browse_tool, JmBrowseTool),
        (plugin.config.info_tool, JmInfoTool),
        (plugin.config.download_tool, JmDownloadTool),
        (plugin.config.recommend_tool, JmRecommendTool),
        (plugin.config.recommend_download_tool, JmRecommendDownloadTool),
        (plugin.config.task_status_tool, JmTaskStatusTool),
    ):
        if not config.enabled:
            continue
        tools.append(factory(plugin=plugin, name=config.tool_name, description=config.description))
    return tools
