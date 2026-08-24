# -*- coding: utf-8 -*-
"""最小阿里云 OSS 客户端。

对象 key 均相对于 ``OSS_PREFIX``。生产环境的业务文件必须先写入 OSS，
本地 ``data`` 卷只用于解析缓存和迁移暂存。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import formatdate
from typing import Optional, Tuple


class ObjectStoreError(RuntimeError):
    """对象存储操作失败。"""


_KEY_BAD = re.compile(r"(^/)|(\.\.)|(//)|(\\)")


def check_key(key: str) -> str:
    if not key or _KEY_BAD.search(key):
        raise ObjectStoreError(f"非法对象 key: {key!r}")
    return key


class OssStore:
    """单 Bucket、固定 prefix 的 OSS V1 签名客户端。"""

    def __init__(
        self,
        endpoint: str,
        bucket: str,
        access_key_id: str,
        access_key_secret: str,
        prefix: str = "",
        timeout: int = 30,
        max_retries: int = 3,
    ):
        self.endpoint = endpoint.replace("https://", "").replace("http://", "").strip("/")
        self.bucket = bucket
        self._ak = access_key_id
        self._sk = access_key_secret
        self.prefix = prefix.strip("/")
        if self.prefix:
            check_key(f"{self.prefix}/_prefix-check")
        self.timeout = timeout
        self.max_retries = max_retries

    def _full_key(self, key: str) -> str:
        check_key(key)
        return f"{self.prefix}/{key}" if self.prefix else key

    def _sign(self, method: str, resource: str, date: str, content_type: str = "") -> str:
        value = f"{method}\n\n{content_type}\n{date}\n{resource}"
        digest = hmac.new(self._sk.encode(), value.encode("utf-8"), hashlib.sha1).digest()
        return f"OSS {self._ak}:{base64.b64encode(digest).decode()}"

    def _request(
        self,
        method: str,
        key: str,
        body: Optional[bytes] = None,
        content_type: str = "",
        ok_codes: Tuple[int, ...] = (200,),
    ) -> Tuple[int, bytes]:
        full_key = self._full_key(key)
        resource = f"/{self.bucket}/{full_key}"
        url = f"https://{self.bucket}.{self.endpoint}/{urllib.parse.quote(full_key, safe='/')}"
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            date = formatdate(usegmt=True)
            headers = {
                "Date": date,
                "Authorization": self._sign(method, resource, date, content_type),
            }
            if content_type:
                headers["Content-Type"] = content_type
            request = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.status, response.read()
            except urllib.error.HTTPError as exc:
                detail = exc.read()[:300]
                if exc.code in ok_codes:
                    return exc.code, detail
                if 400 <= exc.code < 500:
                    raise ObjectStoreError(f"OSS {method} 请求失败: HTTP {exc.code}") from exc
                last_error = ObjectStoreError(f"OSS {method} 请求失败: HTTP {exc.code}")
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = ObjectStoreError(f"OSS {method} 网络失败: {type(exc).__name__}")
            if attempt < self.max_retries - 1:
                time.sleep(1.5 * (2 ** attempt))

        raise last_error or ObjectStoreError("OSS 调用失败")

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._request("PUT", key, body=data, content_type=content_type)

    def get_bytes(self, key: str) -> Optional[bytes]:
        try:
            return self._request("GET", key)[1]
        except ObjectStoreError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise

    def exists(self, key: str) -> bool:
        try:
            self._request("HEAD", key)
            return True
        except ObjectStoreError as exc:
            if "HTTP 404" in str(exc):
                return False
            raise

    def delete(self, key: str) -> None:
        self._request("DELETE", key, ok_codes=(200, 204))


def build_oss_store_from_env() -> Optional[OssStore]:
    backend = (os.getenv("STORAGE_BACKEND") or "local").strip().lower()
    if backend == "local":
        return None
    if backend != "oss":
        raise ObjectStoreError("STORAGE_BACKEND 仅支持 local 或 oss")

    endpoint = (os.getenv("OSS_ENDPOINT") or "").strip()
    bucket = (os.getenv("OSS_BUCKET") or "").strip()
    access_key_id = (os.getenv("OSS_ACCESS_KEY_ID") or "").strip()
    access_key_secret = (os.getenv("OSS_ACCESS_KEY_SECRET") or "").strip()
    prefix = (os.getenv("OSS_PREFIX") or "").strip("/")
    if not (endpoint and bucket and access_key_id and access_key_secret and prefix):
        raise ObjectStoreError(
            "STORAGE_BACKEND=oss 时 OSS_ENDPOINT/OSS_BUCKET/OSS_ACCESS_KEY_ID/"
            "OSS_ACCESS_KEY_SECRET/OSS_PREFIX 必须完整"
        )
    return OssStore(endpoint, bucket, access_key_id, access_key_secret, prefix=prefix)
