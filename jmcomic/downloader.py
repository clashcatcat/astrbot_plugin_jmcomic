from __future__ import annotations

import asyncio
import io
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyzipper
from PIL import Image
from pypdf import PdfReader, PdfWriter

from .client import JmClient
from .config import PluginConfig
from .entities import JmAlbumEntity, JmPhotoEntity
from .errors import ArtifactError, InvalidInputError
from .utils import ensure_parent, format_output_name, sanitize_file_name


@dataclass
class DownloadAlbumInput:
    album_id: str
    format: str
    password: str | None = None
    unified_msg_origin: str | None = None


@dataclass
class DownloadAlbumResult:
    album_id: str
    title: str
    file_path: str
    file_name: str
    extension: str


class AlbumDownloader:
    def __init__(self, root: Path, client: JmClient, config: PluginConfig, logger: Any):
        self.root = root
        self.client = client
        self.config = config
        self.logger = logger

    async def download(self, input_value: DownloadAlbumInput) -> DownloadAlbumResult:
        if not input_value.album_id.isdigit():
            raise InvalidInputError("albumId must be numeric")
        await self._ensure_free_disk_space()
        album = await self.client.get_album_by_id(input_value.album_id)
        self._validate_album(album)
        self._validate_page_limit(album)

        album_dir = self.root / "album" / album.id
        output_dir = album_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        await self._download_and_decode_album(album, album_dir)

        output_base_name = format_output_name(self.config.file_name, album.name, album.id)
        if input_value.format == "pdf":
            file_path = await self._build_pdf(album, album_dir, output_dir, output_base_name, input_value.password)
        else:
            file_path = await self._build_zip(album, album_dir, output_dir, output_base_name, input_value.password)

        return DownloadAlbumResult(
            album_id=album.id,
            title=album.name,
            file_path=str(file_path),
            file_name=f"{output_base_name}.{input_value.format}",
            extension=input_value.format,
        )

    async def cleanup(self, album_id: str) -> None:
        if not self.config.cache:
            shutil.rmtree(self.root / "album" / album_id, ignore_errors=True)

    async def cleanup_expired(self) -> None:
        album_root = self.root / "album"
        if not album_root.exists():
            return
        expire_seconds = self.config.file_expire_hours * 3600
        now = time.time()
        for item in album_root.iterdir():
            if not item.is_dir():
                continue
            try:
                age = now - item.stat().st_mtime
            except FileNotFoundError:
                continue
            if age >= expire_seconds:
                shutil.rmtree(item, ignore_errors=True)

    async def _download_and_decode_album(self, album: JmAlbumEntity, album_dir: Path) -> None:
        nested = len(album.photos) > 1
        for photo in album.photos:
            await self._download_and_decode_photo(photo, album_dir, nested)

    async def _download_and_decode_photo(self, photo: JmPhotoEntity, album_dir: Path, nested: bool) -> None:
        origin_dir = album_dir / "origin" / photo.id if nested else album_dir / "origin"
        decoded_dir = album_dir / "decoded" / photo.id if nested else album_dir / "decoded"
        shutil.rmtree(decoded_dir, ignore_errors=True)
        origin_dir.mkdir(parents=True, exist_ok=True)
        decoded_dir.mkdir(parents=True, exist_ok=True)

        scramble_id = await self.client.request_scramble_id(photo.id)
        split_numbers = photo.get_split_numbers(scramble_id)

        download_semaphore = asyncio.Semaphore(self.config.concurrent_download_limit)
        decode_semaphore = asyncio.Semaphore(self.config.concurrent_decode_limit)

        async def download_one(image_name: str) -> None:
            async with download_semaphore:
                path = origin_dir / sanitize_file_name(image_name)
                if path.exists() and path.stat().st_size > 0:
                    return
                data = await self.client.download_image(photo.id, image_name)
                if not data:
                    raise ArtifactError(f"empty image: {photo.id}/{image_name}")
                path.write_bytes(data)

        await asyncio.gather(*(download_one(image) for image in photo.images))

        async def decode_one(index: int, image_name: str) -> None:
            async with decode_semaphore:
                origin_path = origin_dir / sanitize_file_name(image_name)
                decoded_path = decoded_dir / sanitize_file_name(image_name)
                await asyncio.to_thread(
                    decode_image,
                    origin_path.read_bytes(),
                    split_numbers[index],
                    decoded_path,
                )

        await asyncio.gather(*(decode_one(index, image) for index, image in enumerate(photo.images)))

    async def _build_pdf(
        self,
        album: JmAlbumEntity,
        album_dir: Path,
        output_dir: Path,
        output_base_name: str,
        password: str | None,
    ) -> Path:
        image_paths = self._list_decoded_images(album, album_dir)
        output_path = output_dir / f"{output_base_name}.pdf"
        await asyncio.to_thread(create_pdf_from_images, image_paths, output_path, password)
        return output_path

    async def _build_zip(
        self,
        album: JmAlbumEntity,
        album_dir: Path,
        output_dir: Path,
        output_base_name: str,
        password: str | None,
    ) -> Path:
        output_path = output_dir / f"{output_base_name}.zip"
        decoded_root = album_dir / "decoded"
        nested = len(album.photos) > 1
        await asyncio.to_thread(
            create_zip_from_directory,
            album,
            decoded_root,
            output_path,
            password,
            nested,
            self.config.zip_level,
        )
        return output_path

    async def _ensure_free_disk_space(self) -> None:
        if self.config.minimum_free_disk_mb <= 0:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(self.root)
        free_mb = usage.free / 1024 / 1024
        if free_mb < self.config.minimum_free_disk_mb:
            raise ArtifactError(
                f"insufficient disk space: about {int(free_mb)} MB available, "
                f"{self.config.minimum_free_disk_mb} MB required"
            )

    def _list_decoded_images(self, album: JmAlbumEntity, album_dir: Path) -> list[Path]:
        decoded_root = album_dir / "decoded"
        nested = len(album.photos) > 1
        paths: list[Path] = []
        for photo in album.photos:
            photo_root = decoded_root / photo.id if nested else decoded_root
            paths.extend(photo_root / sanitize_file_name(image) for image in photo.images)
        return paths

    @staticmethod
    def _validate_album(album: JmAlbumEntity) -> None:
        if not album.photos:
            raise ArtifactError(f"album has no photos: {album.id}")
        for photo in album.photos:
            if not photo.images:
                raise ArtifactError(f"chapter has no images: {photo.id}")

    def _validate_page_limit(self, album: JmAlbumEntity) -> None:
        page_count = sum(len(photo.images) for photo in album.photos)
        if page_count > self.config.max_download_pages:
            raise ArtifactError(
                f"too many pages: {page_count}, max allowed is {self.config.max_download_pages}"
            )


def decode_image(data: bytes, split_number: int, output_path: Path) -> None:
    ensure_parent(output_path)
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        source = image.convert("RGB")
        if split_number <= 0 or source.height < split_number or source.width <= 0:
            source.save(output_path)
            return

        over = source.height % split_number
        move = source.height // split_number
        target = Image.new("RGB", (source.width, source.height), "white")

        for index in range(split_number):
            part_height = move + over if index == 0 else move
            source_top = source.height - over - move * (index + 1)
            target_top = 0 if index == 0 else over + move * index
            box = (0, source_top, source.width, source_top + part_height)
            part = source.crop(box)
            target.paste(part, (0, target_top))

        target.save(output_path)


def create_pdf_from_images(image_paths: list[Path], output_path: Path, password: str | None) -> None:
    ensure_parent(output_path)
    images = []
    for path in image_paths:
        with Image.open(path) as image:
            images.append(image.convert("RGB"))

    if not images:
        raise ArtifactError("no decoded images to build pdf")

    first, rest = images[0], images[1:]
    first.save(output_path, save_all=True, append_images=rest)
    for image in images:
        image.close()

    if password:
        reader = PdfReader(str(output_path))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.encrypt(password)
        with output_path.open("wb") as file_obj:
            writer.write(file_obj)


def create_zip_from_directory(
    album: JmAlbumEntity,
    decoded_root: Path,
    output_path: Path,
    password: str | None,
    nested: bool,
    zip_level: int,
) -> None:
    ensure_parent(output_path)
    compression = pyzipper.ZIP_DEFLATED
    with pyzipper.AESZipFile(output_path, "w", compression=compression, compresslevel=zip_level) as zf:
        if password:
            zf.setpassword(password.encode("utf-8"))
            zf.setencryption(pyzipper.WZ_AES, nbits=256)

        if nested:
            for index, photo in enumerate(album.photos, start=1):
                chapter_name = f"第{index}章"
                photo_root = decoded_root / photo.id
                for image_name in photo.images:
                    source = photo_root / sanitize_file_name(image_name)
                    zf.write(source, arcname=f"{chapter_name}/{source.name}")
        else:
            for image_name in album.photos[0].images:
                source = decoded_root / sanitize_file_name(image_name)
                zf.write(source, arcname=source.name)
