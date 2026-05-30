from __future__ import annotations

import json
from typing import Any

import aiohttp

from .config import PluginConfig
from .constants import (
    JM_API_DOMAIN_SERVER_LIST,
    JM_API_DOMAIN_SERVER_SECRET,
    JM_CLIENT_URL_LIST,
    JM_IMAGE_URL_LIST,
)
from .crypto import decode_encrypted_json
from .errors import JmRequestError


class JmHttp:
    def __init__(self, config: PluginConfig, logger: Any):
        self.config = config
        self.logger = logger
        self.updated_client_hosts: list[str] | None = None
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def request_json(
        self,
        path: str,
        method: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        kind: str = "client",
    ) -> dict[str, Any]:
        return await self._request(path, method, headers=headers, params=params, kind=kind, response_type="json")

    async def request_text(
        self,
        path: str,
        method: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        kind: str = "client",
    ) -> str:
        return await self._request(path, method, headers=headers, params=params, kind=kind, response_type="text")

    async def request_bytes(
        self,
        path: str,
        method: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        kind: str = "image",
    ) -> bytes:
        return await self._request(path, method, headers=headers, params=params, kind=kind, response_type="bytes")

    async def _request(
        self,
        path: str,
        method: str,
        *,
        headers: dict[str, str] | None,
        params: dict[str, Any] | None,
        kind: str,
        response_type: str,
        did_update_domain: bool = False,
    ) -> Any:
        hosts = self.updated_client_hosts if kind == "client" and self.updated_client_hosts else (
            JM_CLIENT_URL_LIST if kind == "client" else JM_IMAGE_URL_LIST
        )
        last_error: Exception | None = None
        session = await self._ensure_session()

        for host in hosts:
            url = f"https://{host}{path}"
            for attempt in range(1, self.config.retry_count + 1):
                try:
                    async with session.request(
                        method=method.upper(),
                        url=url,
                        headers=headers,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=60),
                    ) as response:
                        response.raise_for_status()
                        if response_type == "json":
                            return await response.json(content_type=None)
                        if response_type == "text":
                            return await response.text()
                        body = await response.read()
                        if not body:
                            raise JmRequestError("empty image response")
                        return body
                except Exception as exc:
                    last_error = exc if isinstance(exc, Exception) else Exception(str(exc))
                    if self.config.debug:
                        self.logger.warning(
                            "JM request failed: %s %s (%s/%s): %s",
                            method.upper(),
                            url,
                            attempt,
                            self.config.retry_count,
                            exc,
                        )

        if kind == "client" and not did_update_domain:
            latest_hosts = await self._fetch_latest_client_hosts()
            if latest_hosts:
                self.updated_client_hosts = latest_hosts
                return await self._request(
                    path,
                    method,
                    headers=headers,
                    params=params,
                    kind=kind,
                    response_type=response_type,
                    did_update_domain=True,
                )

        raise JmRequestError(f"JM request failed: {method.upper()} {path}: {last_error}")

    async def _fetch_latest_client_hosts(self) -> list[str]:
        session = await self._ensure_session()
        for url in JM_API_DOMAIN_SERVER_LIST:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    response.raise_for_status()
                    encoded = await response.text()
                encoded = encoded.encode("ascii", "ignore").decode("ascii")
                data = decode_encrypted_json(encoded, "", JM_API_DOMAIN_SERVER_SECRET)
                servers = data.get("Server")
                if isinstance(servers, list) and servers:
                    return [str(item) for item in servers]
            except Exception as exc:
                if self.config.debug:
                    self.logger.warning("JM API domain update failed: %s: %s", url, exc)
        return []

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "accept": "application/json, text/plain, */*",
                    "user-agent": "okhttp/4.11.0",
                }
            )
        return self._session
