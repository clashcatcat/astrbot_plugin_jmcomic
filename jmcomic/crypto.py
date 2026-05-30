from __future__ import annotations

import base64
import hashlib
import json

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

from .constants import JM_APP_DATA_SECRET, JM_APP_TOKEN_SECRET, JM_APP_VERSION


def md5_hex(value: str, encoding: str = "utf-8") -> str:
    return hashlib.md5(value.encode(encoding)).hexdigest()


def create_token_headers(timestamp: int, secret: str = JM_APP_TOKEN_SECRET) -> dict[str, str]:
    return {
        "token": md5_hex(f"{timestamp}{secret}"),
        "tokenparam": f"{timestamp},{JM_APP_VERSION}",
    }


def decode_encrypted_json(base64_data: str, timestamp: int | str, secret: str = JM_APP_DATA_SECRET) -> dict:
    key = md5_hex(f"{timestamp}{secret}").encode("utf-8")
    cipher = AES.new(key, AES.MODE_ECB)
    decrypted = unpad(cipher.decrypt(base64.b64decode(base64_data)), AES.block_size)
    return json.loads(decrypted.decode("utf-8"))
