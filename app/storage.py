# -*- coding: utf-8 -*-
"""报告原文件的 OSS 写通持久化。"""
from __future__ import annotations

import asyncio
import mimetypes
import re
import secrets
from typing import Iterable, Optional

from app.object_store import ObjectStoreError, OssStore, build_oss_store_from_env


_store: Optional[OssStore] = None
_initialized = False


def safe_filename(filename: str) -> str:
    """只保留文件名，防止路径穿越和对象 key 注入。"""
    name = (filename or "upload.xlsx").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "_", name).strip().strip(".")
    return name[:180] or "upload.xlsx"


def get_store() -> Optional[OssStore]:
    global _store, _initialized
    if not _initialized:
        _store = build_oss_store_from_env()
        _initialized = True
    return _store


def reset_store_for_tests() -> None:
    global _store, _initialized
    _store = None
    _initialized = False


def object_key(category: str, task_id: str, filename: str, index: Optional[int] = None) -> str:
    clean_name = safe_filename(filename)
    indexed_name = f"{index:03d}-{clean_name}" if index is not None else clean_name
    return f"uploads/{category}/{task_id}/{indexed_name}"


async def persist_upload(
    category: str,
    task_id: str,
    filename: str,
    contents: bytes,
    index: Optional[int] = None,
) -> Optional[str]:
    """先写 OSS 再允许业务处理；local 模式返回 None。"""
    store = get_store()
    if store is None:
        return None
    key = object_key(category, task_id, filename, index)
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    await asyncio.to_thread(store.put_bytes, key, contents, content_type)
    return key


async def rollback_objects(keys: Iterable[Optional[str]]) -> None:
    store = get_store()
    if store is None:
        return
    for key in keys:
        if key:
            try:
                await asyncio.to_thread(store.delete, key)
            except ObjectStoreError:
                # 上传失败路径上的补偿删除是 best-effort；不能掩盖原始错误。
                pass


async def check_storage_ready() -> None:
    """启动时做一次真实写/读/删，避免只有请求到来后才发现权限错误。"""
    store = get_store()
    if store is None:
        return
    key = f"_health/startup-{secrets.token_hex(12)}"
    probe = b"report-audit-storage-probe"
    await asyncio.to_thread(store.put_bytes, key, probe, "text/plain")
    try:
        value = await asyncio.to_thread(store.get_bytes, key)
        if value != probe:
            raise ObjectStoreError("OSS 启动探针读回内容不一致")
    finally:
        await asyncio.to_thread(store.delete, key)
