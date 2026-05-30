from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .client import JmClient
from .config import PluginConfig
from .downloader import AlbumDownloader, DownloadAlbumInput, DownloadAlbumResult
from .http import JmHttp
from .queue import TaskQueue
from .tools import build_llm_tools

DEFAULT_TASK_STATUS_WAIT_MS = 3 * 60 * 1000
PLUGIN_NAME = "astrbot_plugin_jmcomic"


@register(
    PLUGIN_NAME,
    "Sor85",
    "为 AstrBot 提供 JMComic 搜索、浏览、详情查询和整本下载工具。",
    "0.1.0",
)
class JmComicPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.log = logger
        self.config = PluginConfig.from_mapping(config)
        self.root = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME / "jmcomic"
        self.http = JmHttp(self.config, self.log)
        self.client = JmClient(self.http, self.config, self.log)
        self.downloader = AlbumDownloader(self.root, self.client, self.config, self.log)
        self.album_locks: dict[str, asyncio.Lock] = {}
        self.queue = TaskQueue(self._process_download_task, self.config.concurrent_task_limit)
        self.context.add_llm_tools(*build_llm_tools(self))
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def terminate(self):
        self._cleanup_task.cancel()
        try:
            await self._cleanup_task
        except asyncio.CancelledError:
            pass
        await self.http.close()

    @filter.command("jm搜索")
    async def jm_search(self, event: AstrMessageEvent, query: str):
        event.stop_event()
        result = await self.client.search(query)
        items = result.get("content") or []
        lines = [f"搜索 `{query}` 共 {result.get('total', 0)} 条，展示前 {min(len(items), 10)} 条："]
        for item in items[:10]:
            lines.append(f"- {item.get('id')}: {item.get('name')}")
        yield event.plain_result("\n".join(lines))

    @filter.command("jm详情")
    async def jm_info(self, event: AstrMessageEvent, album_id: str):
        event.stop_event()
        album = await self.client.get_album_by_id(album_id)
        lines = [
            f"ID: {album.id}",
            f"标题: {album.name}",
            f"作者: {', '.join(album.authors) if album.authors else '未知'}",
            f"标签: {', '.join(album.tags) if album.tags else '无'}",
            f"章节数: {len(album.photos)}",
            f"总页数: {sum(len(photo.images) for photo in album.photos)}",
        ]
        yield event.plain_result("\n".join(lines))

    @filter.command("jm下载")
    async def jm_download(self, event: AstrMessageEvent, album_id: str, fmt: str = ""):
        event.stop_event()
        fmt = (fmt or "").strip().lower()
        if fmt not in {"", "pdf", "zip"}:
            yield event.plain_result("格式只支持 pdf 或 zip。")
            return
        download_format = fmt or self.config.default_format
        added = await self.queue.add(
            DownloadAlbumInput(
                album_id=album_id,
                format=download_format,
                password=self.config.password or None,
                unified_msg_origin=event.unified_msg_origin,
            )
        )
        yield event.plain_result(
            f"任务已提交。task_id={added.task_id}，前方待处理 {added.pending_ahead} 个任务。"
        )

    @filter.command("jm任务")
    async def jm_task(self, event: AstrMessageEvent, task_id: int):
        event.stop_event()
        payload = await self.get_task_status_payload(task_id, DEFAULT_TASK_STATUS_WAIT_MS)
        yield event.plain_result(json.dumps(payload, ensure_ascii=False, indent=2))

    async def get_task_status_payload(self, task_id: int, wait_ms: int) -> dict[str, Any]:
        task = await self.queue.wait_for_task(task_id, wait_ms)
        if task is None:
            return {"ok": False, "task_id": task_id, "error": "task not found"}

        payload: dict[str, Any] = {
            "ok": task.status != "failed",
            "task_id": task.id,
            "status": task.status,
            "created_at": task.created_at,
            "processed_at": task.processed_at,
        }
        if task.status == "completed" and task.result is not None:
            payload["result"] = {
                "album_id": task.result.album_id,
                "title": task.result.title,
                "file_name": task.result.file_name,
                "file_path": task.result.file_path,
                "extension": task.result.extension,
            }
            if self.config.return_password_to_ai and task.input.password:
                payload["password"] = task.input.password
        if task.status == "failed":
            payload["error"] = task.error
        return payload

    async def _process_download_task(self, input_value: DownloadAlbumInput) -> DownloadAlbumResult:
        lock = self.album_locks.setdefault(input_value.album_id, asyncio.Lock())
        async with lock:
            result: DownloadAlbumResult | None = None
            try:
                result = await self.downloader.download(input_value)
                if input_value.unified_msg_origin:
                    await self._send_download_result(input_value.unified_msg_origin, result)
                return result
            finally:
                if not self.config.cache:
                    await self.downloader.cleanup((result or input_value).album_id)
                if self.album_locks.get(input_value.album_id) is lock and not lock.locked():
                    self.album_locks.pop(input_value.album_id, None)

    async def _send_download_result(self, unified_msg_origin: str, result: DownloadAlbumResult) -> None:
        try:
            await self.context.send_message(
                unified_msg_origin,
                [
                    Comp.Plain(f"JMComic 下载完成：{result.file_name}"),
                    Comp.File(file=result.file_path, name=result.file_name),
                ],
            )
        except Exception as exc:
            self.log.warning("Failed to send JMComic file, falling back to text: %s", exc)
            await self.context.send_message(
                unified_msg_origin,
                [Comp.Plain(f"JMComic 下载完成，但文件发送失败。文件路径：{result.file_path}")],
            )

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                await self.downloader.cleanup_expired()
                await self.queue.prune_terminal_tasks(self.config.file_expire_hours * 3600 * 1000)
            except Exception as exc:
                self.log.warning("JMComic cleanup failed: %s", exc)
            await asyncio.sleep(3600)
