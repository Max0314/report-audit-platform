# -*- coding: utf-8 -*-
"""复用硬件门户会话，并对写请求实施双提交 Cookie CSRF 校验。"""
from __future__ import annotations

import hashlib
import secrets
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.core.config import get_settings
from app.core.logger import get_logger


logger = get_logger("hardware_auth")
_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}
_client: Optional[httpx.AsyncClient] = None
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_PUBLIC_PATHS = {"/health"}


def validate_auth_config() -> None:
    settings = get_settings()
    auth = settings.hardware_auth
    if not auth.enabled:
        if settings.server.env.lower() == "production":
            raise RuntimeError("生产环境必须启用 HARDWARE_AUTH_ENABLED")
        return
    if not auth.internal_url or not auth.internal_secret:
        raise RuntimeError(
            "启用硬件门户登录复用时，HARDWARE_AUTH_INTERNAL_URL/"
            "HARDWARE_AUTH_INTERNAL_SECRET 必须配置"
        )


async def close_auth_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _verify(cookie_header: str) -> Tuple[Optional[Dict[str, Any]], bool]:
    """返回 (用户, 校验服务是否可用)。"""
    global _client
    auth = get_settings().hardware_auth
    if not auth.enabled:
        return {"id": "dev", "username": "dev", "name": "本地开发"}, True
    if not cookie_header.strip():
        return None, True

    cache_key = hashlib.sha256(cookie_header.encode("utf-8", errors="ignore")).hexdigest()
    now = time.monotonic()
    cached = _cache.get(cache_key)
    if cached and cached[1] > now:
        return cached[0], True
    _cache.pop(cache_key, None)

    if _client is None:
        _client = httpx.AsyncClient(timeout=auth.timeout_seconds)
    url = auth.internal_url.rstrip("/") + "/api/internal/neo/verify-session"
    try:
        response = await _client.get(
            url,
            headers={
                "Cookie": cookie_header,
                "X-Neo-Internal-Secret": auth.internal_secret,
            },
        )
        if response.status_code != 200:
            logger.error("硬件门户会话校验返回 HTTP %s", response.status_code)
            return None, False
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.error("硬件门户会话校验不可用: %s", type(exc).__name__)
        return None, False

    user = data.get("user") if data.get("authenticated") else None
    if not isinstance(user, dict):
        return None, True
    _cache[cache_key] = (user, now + auth.cache_ttl_seconds)
    if len(_cache) > 512:
        _cache.clear()
    return user, True


def _external_path(request: Request) -> str:
    prefix = get_settings().hardware_auth.public_prefix.rstrip("/")
    path = request.url.path if request.url.path != "/" else "/"
    query = f"?{request.url.query}" if request.url.query else ""
    return f"{prefix}{path}{query}"


async def hardware_auth_middleware(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path in _PUBLIC_PATHS:
        return await call_next(request)

    user, available = await _verify(request.headers.get("cookie", ""))
    if not available:
        return JSONResponse({"detail": "硬件门户身份校验暂时不可用"}, status_code=503)
    if not user:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "未登录或会话已失效"}, status_code=401)
        auth = get_settings().hardware_auth
        target = quote(_external_path(request), safe="")
        return RedirectResponse(f"{auth.login_url}?redirect={target}", status_code=302)

    request.state.hardware_user = user
    if request.method not in _SAFE_METHODS and request.url.path.startswith("/api/"):
        cookie_token = request.cookies.get("csrf_token", "")
        header_token = request.headers.get("X-CSRF-Token", "").strip()
        if not cookie_token or not header_token or not secrets.compare_digest(cookie_token, header_token):
            return JSONResponse({"detail": "CSRF 校验失败"}, status_code=403)

    response = await call_next(request)
    if not request.cookies.get("csrf_token"):
        forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        response.set_cookie(
            "csrf_token",
            secrets.token_urlsafe(32),
            path="/",
            secure=forwarded_proto == "https",
            httponly=False,
            samesite="lax",
            max_age=604800,
        )
    return response


def current_user_key(request: Request) -> str:
    user = getattr(request.state, "hardware_user", None) or {}
    value = user.get("userKey") or user.get("userid") or user.get("id") or user.get("username")
    if value is None:
        raise HTTPException(status_code=401, detail="未登录")
    return str(value)


def require_task_owner(request: Request, task: dict) -> None:
    if task.get("owner_key") != current_user_key(request):
        # 不泄露任务是否属于其他用户。
        raise HTTPException(status_code=404, detail="任务不存在")
