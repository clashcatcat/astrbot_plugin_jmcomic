from __future__ import annotations

from dataclasses import dataclass

from .crypto import md5_hex

SCRAMBLE_268850 = 268850
SCRAMBLE_421926 = 421926


@dataclass
class JmAlbumSeries:
    id: str
    name: str
    sort: str


@dataclass
class JmPhotoEntity:
    id: str
    name: str
    images: list[str]
    image_names: list[str]

    def get_split_numbers(self, scramble_id: int) -> list[int]:
        values: list[int] = []
        numeric_id = int(self.id)
        for image_name in self.image_names:
            if numeric_id < scramble_id:
                values.append(0)
                continue
            if numeric_id < SCRAMBLE_268850:
                values.append(10)
                continue

            base = 10 if numeric_id < SCRAMBLE_421926 else 8
            hashed = md5_hex(f"{numeric_id}{image_name}")
            last_char = hashed[-1]
            values.append((ord(last_char) % base) * 2 + 2)
        return values


@dataclass
class JmAlbumEntity:
    id: str
    name: str
    description: str
    cover: str | None
    published_at: str
    authors: list[str]
    tags: list[str]
    actors: list[str]
    likes: str
    total_views: str
    series: list[JmAlbumSeries]
    photos: list[JmPhotoEntity]


def create_photo_entity(data: dict) -> JmPhotoEntity:
    images = list(data.get("images") or [])
    return JmPhotoEntity(
        id=str(data["id"]),
        name=data.get("name") or "",
        images=images,
        image_names=[image.split(".")[0] for image in images],
    )


def create_album_entity(data: dict, photos: list[JmPhotoEntity]) -> JmAlbumEntity:
    series = [
        JmAlbumSeries(
            id=str(item["id"]),
            name=item.get("name") or "",
            sort=str(item.get("sort") or ""),
        )
        for item in (data.get("series") or [])
    ]
    images = list(data.get("images") or [])
    return JmAlbumEntity(
        id=str(data["id"]),
        name=data.get("name") or "",
        description=data.get("description") or "",
        cover=images[0] if images else None,
        published_at=data.get("addtime") or "",
        authors=list(data.get("author") or []),
        tags=list(data.get("tags") or []),
        actors=list(data.get("actors") or []),
        likes=str(data.get("likes") or ""),
        total_views=str(data.get("total_views") or ""),
        series=series,
        photos=photos,
    )
