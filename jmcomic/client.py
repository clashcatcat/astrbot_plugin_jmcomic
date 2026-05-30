from __future__ import annotations

import asyncio
import time
from typing import Any

from .config import PluginConfig
from .constants import JM_APP_TOKEN_SECRET_2
from .crypto import create_token_headers, decode_encrypted_json
from .entities import JmAlbumEntity, JmPhotoEntity, create_album_entity, create_photo_entity
from .errors import AlbumNotFoundError, PhotoNotFoundError
from .http import JmHttp

BROWSE_TIME_MAP = {
    "today": "t",
    "week": "w",
    "month": "m",
    "all": "a",
}

BROWSE_CATEGORY_MAP = {
    "all": "0",
    "doujin": "doujin",
    "single": "single",
    "short": "short",
    "another": "another",
    "hanman": "hanman",
    "meiman": "meiman",
    "doujin_cosplay": "doujin_cosplay",
    "cosplay": "doujin_cosplay",
    "3d": "3D",
    "english_site": "english_site",
}

BROWSE_ORDER_MAP = {
    "latest": "mr",
    "view": "mv",
    "picture": "mp",
    "like": "tf",
    "month_rank": "mv_m",
    "week_rank": "mv_w",
    "day_rank": "mv_t",
}


class JmClient:
    def __init__(self, http: JmHttp, config: PluginConfig, logger: Any):
        self.http = http
        self.config = config
        self.logger = logger

    async def search(self, query: str) -> dict[str, Any]:
        timestamp = int(time.time() * 1000)
        response = await self.http.request_json(
            "/search",
            "POST",
            headers=create_token_headers(timestamp),
            params={"search_query": query},
        )
        return decode_encrypted_json(response["data"], timestamp)

    async def browse(self, *, page: int, time_range: str, category: str, order_by: str) -> dict[str, Any]:
        timestamp = int(time.time() * 1000)
        order = BROWSE_ORDER_MAP[order_by]
        time_code = BROWSE_TIME_MAP[time_range]
        response = await self.http.request_json(
            "/categories/filter",
            "POST",
            headers=create_token_headers(timestamp),
            params={
                "page": page,
                "order": "",
                "c": BROWSE_CATEGORY_MAP[category],
                "o": order if time_code == "a" else f"{order}_{time_code}",
            },
        )
        return decode_encrypted_json(response["data"], timestamp)

    async def get_album_by_id(self, album_id: str) -> JmAlbumEntity:
        timestamp = int(time.time() * 1000)
        response = await self.http.request_json(
            "/album",
            "POST",
            headers=create_token_headers(timestamp),
            params={"id": album_id},
        )
        album_json = decode_encrypted_json(response["data"], timestamp)
        if not album_json.get("name"):
            raise AlbumNotFoundError(f"Album not found: {album_id}")

        series = album_json.get("series") or [{"id": album_id, "name": album_json["name"], "sort": "1"}]
        photos = await asyncio.gather(*(self.get_photo_by_id(str(item["id"])) for item in series))
        album_json["series"] = series
        return create_album_entity(album_json, photos)

    async def get_photo_by_id(self, photo_id: str) -> JmPhotoEntity:
        timestamp = int(time.time() * 1000)
        response = await self.http.request_json(
            "/chapter",
            "POST",
            headers=create_token_headers(timestamp),
            params={"id": photo_id},
        )
        photo_json = decode_encrypted_json(response["data"], timestamp)
        if not photo_json.get("name"):
            raise PhotoNotFoundError(f"Photo not found: {photo_id}")
        return create_photo_entity(photo_json)

    async def request_scramble_id(self, photo_id: str) -> int:
        timestamp = int(time.time() * 1000)
        html = await self.http.request_text(
            "/chapter_view_template",
            "POST",
            headers=create_token_headers(timestamp, JM_APP_TOKEN_SECRET_2),
            params={"id": photo_id},
        )
        marker = "var scramble_id = "
        if marker not in html:
            return 0
        suffix = html.split(marker, 1)[1]
        number = suffix.split(";", 1)[0].strip()
        return int(number) if number.isdigit() else 0

    async def download_image(self, photo_id: str, image: str) -> bytes:
        path = f"/media/photos/{photo_id}/{image}"
        if self.config.debug:
            self.logger.info("Download JM image: %s", path)
        return await self.http.request_bytes(path, "GET", kind="image")
