from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .jmcomic.client import JmClient
from .jmcomic.config import PluginConfig
from .jmcomic.downloader import AlbumDownloader, DownloadAlbumInput, DownloadAlbumResult
from .jmcomic.http import JmHttp
from .jmcomic.queue import TaskQueue
from .jmcomic.tools import build_llm_tools

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
        self.config.group_whitelist = [
            str(item).strip() for item in (self.config.group_whitelist or []) if str(item).strip()
        ]
        self.root = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME / "jmcomic"
        self.http = JmHttp(self.config, self.log)
        self.client = JmClient(self.http, self.config, self.log)
        self.downloader = AlbumDownloader(self.root, self.client, self.config, self.log)
        self.album_locks: dict[str, asyncio.Lock] = {}
        self.query_pick_history: dict[str, list[str]] = {}
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
        denied = self._check_group_access(event)
        if denied:
            yield event.plain_result(denied)
            return
        result = await self.client.search(query)
        items = result.get("content") or []
        lines = [f"搜索 `{query}` 共 {result.get('total', 0)} 条，展示前 {min(len(items), 10)} 条："]
        for item in items[:10]:
            lines.append(f"- {item.get('id')}: {item.get('name')}")
        yield event.plain_result("\n".join(lines))

    @filter.command("jm详情")
    async def jm_info(self, event: AstrMessageEvent, album_id: str):
        event.stop_event()
        denied = self._check_group_access(event)
        if denied:
            yield event.plain_result(denied)
            return
        album = await self.client.get_album_by_id(album_id)
        total_pages = self._get_album_total_pages(album)
        chapter_preview = self._format_chapter_preview(album)
        lines = [
            f"ID: {album.id}",
            f"标题: {album.name}",
            f"作者: {', '.join(album.authors) if album.authors else '未知'}",
            f"标签: {', '.join(album.tags) if album.tags else '无'}",
            f"章节数: {len(album.photos)}",
            f"总图片页数(整本累计): {total_pages}",
        ]
        if chapter_preview:
            lines.append(f"章节页数预览: {chapter_preview}")
        yield event.plain_result("\n".join(lines))

    @filter.command("jm下载")
    async def jm_download(self, event: AstrMessageEvent, album_id: str, fmt: str = ""):
        event.stop_event()
        denied = self._check_group_access(event)
        if denied:
            yield event.plain_result(denied)
            return
        fmt = (fmt or "").strip().lower()
        if fmt not in {"", "pdf", "zip"}:
            yield event.plain_result("格式只支持 pdf 或 zip。")
            return
        download_format = fmt or self.config.default_format
        added = await self._submit_download_request(
            DownloadAlbumInput(
                album_id=album_id,
                format=download_format,
                password=self.config.password or None,
                unified_msg_origin=event.unified_msg_origin,
            ),
        )
        yield event.plain_result(
            f"任务已提交。task_id={added.task_id}，前方待处理 {added.pending_ahead} 个任务。"
        )

    @filter.command("jm推荐下载")
    async def jm_recommend_download(self, event: AstrMessageEvent, query: str, fmt: str = ""):
        event.stop_event()
        denied = self._check_group_access(event)
        if denied:
            yield event.plain_result(denied)
            return
        fmt = (fmt or "").strip().lower()
        if fmt not in {"", "pdf", "zip"}:
            yield event.plain_result("格式只支持 pdf 或 zip。")
            return

        picked = await self._pick_album_for_query(query, self._get_group_scope_key(event))
        if picked is None:
            yield event.plain_result(f"没有找到适合“{query}”的候选本子。")
            return

        album, reason = picked
        page_count = self._get_album_total_pages(album)
        chapter_preview = self._format_chapter_preview(album)
        if page_count > self.config.max_download_pages:
            lines = [
                f"已根据“{query}”找到最接近的候选：",
                f"标题: {album.name}",
                f"ID: {album.id}",
                f"作者: {', '.join(album.authors) if album.authors else '未知'}",
                f"章节数: {len(album.photos)}",
                f"总图片页数(整本累计): {page_count}",
                f"说明: {reason}",
                f"当前插件限制: 最多 {self.config.max_download_pages} 页",
            ]
            if chapter_preview:
                lines.append(f"章节页数预览: {chapter_preview}")
            yield event.plain_result("\n".join(lines))
            return
        download_format = fmt or self.config.default_format
        added = await self._submit_download_request(
            DownloadAlbumInput(
                album_id=album.id,
                format=download_format,
                password=self.config.password or None,
                unified_msg_origin=event.unified_msg_origin,
            ),
        )
        lines = [
            f"已根据“{query}”为你推荐并提交下载：",
            f"标题: {album.name}",
            f"ID: {album.id}",
            f"作者: {', '.join(album.authors) if album.authors else '未知'}",
            f"章节数: {len(album.photos)}",
            f"总图片页数(整本累计): {page_count}",
            f"推荐理由: {reason}",
            f"下载格式: {download_format}",
            f"任务已提交。task_id={added.task_id}，前方待处理 {added.pending_ahead} 个任务。",
        ]
        if chapter_preview:
            lines.insert(5, f"章节页数预览: {chapter_preview}")
        yield event.plain_result("\n".join(lines))

    @filter.command("jm推荐")
    async def jm_recommend(self, event: AstrMessageEvent, query: str):
        event.stop_event()
        denied = self._check_group_access(event)
        if denied:
            yield event.plain_result(denied)
            return

        candidates = await self._build_recommend_candidates(query, self._get_group_scope_key(event), 5)
        if not candidates:
            yield event.plain_result(f"没有找到适合“{query}”的候选本子。")
            return

        lines = [f"根据“{query}”找到这些候选本子，你可以回复 ID 再下载："]
        for index, item in enumerate(candidates, start=1):
            album = item["album"]
            lines.extend([
                f"{index}. {album.id} | {album.name}",
                f"作者: {', '.join(album.authors) if album.authors else '未知'}",
                f"章节数: {len(album.photos)} | 总图片页数(整本累计): {item['total_pages']}",
                f"章节页数预览: {self._format_chapter_preview(album)}",
                f"推荐理由: {item['reason']}",
                f"可直接下载: {'是' if item['downloadable'] else f'否（超过 {self.config.max_download_pages} 页限制）'}",
            ])
        yield event.plain_result("\n".join(lines))

    @filter.command("jm任务")
    async def jm_task(self, event: AstrMessageEvent, task_id: int):
        event.stop_event()
        denied = self._check_group_access(event)
        if denied:
            yield event.plain_result(denied)
            return
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
            message_chain = MessageChain(
                chain=[
                    Comp.Plain(f"JMComic 下载完成：{result.file_name}"),
                    Comp.File(file=result.file_path, name=result.file_name),
                ]
            )
            await self.context.send_message(
                unified_msg_origin,
                message_chain,
            )
        except Exception as exc:
            self.log.warning("Failed to send JMComic file, falling back to text: %s", exc)
            fallback_chain = MessageChain(
                chain=[
                    Comp.Plain(f"JMComic 下载完成，但文件发送失败。文件路径：{result.file_path}")
                ]
            )
            await self.context.send_message(
                unified_msg_origin,
                fallback_chain,
            )

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                await self.downloader.cleanup_expired()
                await self.queue.prune_terminal_tasks(self.config.file_expire_hours * 3600 * 1000)
            except Exception as exc:
                self.log.warning("JMComic cleanup failed: %s", exc)
            await asyncio.sleep(3600)

    async def _pick_album_for_query(self, query: str, scope_key: str = "global") -> tuple[Any, str] | None:
        candidates = await self._build_recommend_candidates(query, scope_key, 5)
        downloadable = [item for item in candidates if item["downloadable"]]
        if downloadable:
            picked = downloadable[0]
            self._remember_query_pick(f"{scope_key}::{query.strip().lower()}", picked["album"].id)
            return picked["album"], picked["reason"]
        if candidates:
            picked = candidates[0]
            return picked["album"], (
                f"候选本《{picked['album'].name}》页数为 {picked['total_pages']}，超过当前插件限制 "
                f"{self.config.max_download_pages} 页，无法自动下载"
            )
        return None

    async def _build_recommend_candidates(
        self,
        query: str,
        scope_key: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        result = await self.client.search(query)
        candidates = list(result.get("content") or [])[:8]
        if not candidates:
            return []

        query_lower = query.strip().lower()
        query_tokens = [token for token in query_lower.replace("　", " ").split() if token]
        scored_candidates = [
            (self._score_search_candidate(item, query_lower, query_tokens), item)
            for item in candidates
        ]
        scored_candidates.sort(key=lambda item: item[0], reverse=True)
        top_candidates = [item for _, item in scored_candidates[:5]]

        history_key = f"{scope_key}::{query.strip().lower()}"
        recent_album_ids = self.query_pick_history.get(history_key, [])
        ranked: list[dict[str, Any]] = []
        for item in top_candidates:
            album = await self.client.get_album_by_id(str(item.get("id")))
            total_pages = self._get_album_total_pages(album)
            score, reason = self._score_album_candidate(album, item, query_lower, query_tokens)
            repeated = album.id in recent_album_ids
            if repeated:
                score -= 20
                reason = f"{reason}、避免重复推荐"
            ranked.append({
                "album": album,
                "score": score,
                "reason": reason,
                "total_pages": total_pages,
                "downloadable": total_pages <= self.config.max_download_pages,
                "repeated": repeated,
            })

        ranked.sort(
            key=lambda item: (
                0 if item["downloadable"] else 1,
                0 if not item["repeated"] else 1,
                -item["score"],
            )
        )
        return ranked[:limit]

    def _score_search_candidate(
        self,
        item: dict[str, Any],
        query_lower: str,
        query_tokens: list[str],
    ) -> float:
        title = str(item.get("name") or "").lower()
        author = str(item.get("author") or "").lower()
        description = str(item.get("description") or "").lower()
        score = 0.0

        if query_lower and query_lower in title:
            score += 12
        if query_lower and query_lower in author:
            score += 10
        if query_lower and query_lower in description:
            score += 6

        for token in query_tokens:
            if token in title:
                score += 4
            if token in author:
                score += 3
            if token in description:
                score += 1.5

        if title:
            score += 1
        return score

    def _score_album_candidate(
        self,
        album: Any,
        item: dict[str, Any],
        query_lower: str,
        query_tokens: list[str],
    ) -> tuple[float, str]:
        title = (album.name or "").lower()
        authors = " ".join(album.authors or []).lower()
        tags = " ".join(album.tags or []).lower()
        description = (album.description or "").lower()
        page_count = sum(len(photo.images) for photo in album.photos)

        score = self._score_search_candidate(item, query_lower, query_tokens)
        reasons: list[str] = []

        if query_lower and query_lower in title:
            score += 8
            reasons.append("标题匹配度高")
        if query_lower and query_lower in authors:
            score += 7
            reasons.append("作者匹配")
        if query_lower and query_lower in tags:
            score += 7
            reasons.append("标签接近")
        if query_lower and query_lower in description:
            score += 4
            reasons.append("简介相关")

        for token in query_tokens:
            if token in title:
                score += 2
            if token in authors:
                score += 2
            if token in tags:
                score += 2

        if 1 <= page_count <= self.config.max_download_pages:
            score += 3
            reasons.append("页数适中")
        if album.photos:
            score += 2
            reasons.append("章节信息完整")

        if not reasons:
            reasons.append("综合匹配度最高")

        deduped_reasons: list[str] = []
        for reason in reasons:
            if reason not in deduped_reasons:
                deduped_reasons.append(reason)

        return score, "、".join(deduped_reasons[:3])

    def _remember_query_pick(self, history_key: str, album_id: str) -> None:
        history = self.query_pick_history.get(history_key, [])
        history = [item for item in history if item != album_id]
        history.append(album_id)
        self.query_pick_history[history_key] = history[-5:]

    def _check_group_access(self, event: AstrMessageEvent) -> str | None:
        if not self.config.group_whitelist_enabled:
            return None
        group_id = self._extract_group_id(event)
        if not group_id:
            return "这个插件只允许白名单群组使用。"
        if group_id not in self.config.group_whitelist:
            return f"当前群 {group_id} 不在插件白名单中，禁止使用。"
        return None

    def _extract_group_id(self, event: AstrMessageEvent) -> str | None:
        try:
            group_id = event.get_group_id()
        except Exception:
            group_id = None
        if group_id is None:
            return None
        group_id = str(group_id).strip()
        return group_id or None

    def _get_group_scope_key(self, event: AstrMessageEvent) -> str:
        group_id = self._extract_group_id(event)
        return f"group:{group_id}" if group_id else "private"

    async def _submit_download_request(self, input_value: DownloadAlbumInput):
        existing = await self._find_existing_active_task(input_value)
        if existing is not None:
            return type(
                "ExistingTask",
                (),
                {
                    "task_id": existing.id,
                    "pending_ahead": 0,
                    "queue_position": 1,
                },
            )()
        return await self.queue.add(input_value)

    async def _find_existing_active_task(self, input_value: DownloadAlbumInput):
        tasks = await self.queue.list()
        for task in tasks:
            if task.status not in {"pending", "processing"}:
                continue
            queued = task.input
            if (
                queued.album_id == input_value.album_id
                and queued.format == input_value.format
                and queued.password == input_value.password
            ):
                return task
        return None

    def _get_album_total_pages(self, album: Any) -> int:
        return sum(len(photo.images) for photo in album.photos)

    def _format_chapter_preview(self, album: Any, limit: int = 5) -> str:
        parts: list[str] = []
        for index, photo in enumerate(album.photos[:limit], start=1):
            title = photo.name or f"第{index}章"
            parts.append(f"{title}:{len(photo.images)}页")
        remaining = len(album.photos) - limit
        if remaining > 0:
            parts.append(f"其余 {remaining} 章未展开")
        return "；".join(parts)


__all__ = ["JmComicPlugin"]
