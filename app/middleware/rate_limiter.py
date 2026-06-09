# -*- coding: utf-8 -*-
"""
硬件研发部测试报告审核平台 - 限流中间件
防止恶意请求和过度使用资源
"""

import time
from typing import Dict, Optional
from collections import defaultdict
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class RateLimiter(BaseHTTPMiddleware):
    """请求限流器"""

    def __init__(
        self,
        app,
        requests_per_minute: int = 60,
        burst_size: int = 10
    ):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.burst_size = burst_size

        self._request_times: Dict[str, list] = defaultdict(list)
        self._last_cleanup = time.time()
        self._cleanup_interval = 300

    def _get_client_ip(self, request: Request) -> str:
        """获取客户端IP"""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _cleanup_old_requests(self):
        """清理过期的请求记录"""
        current_time = time.time()

        if current_time - self._last_cleanup < self._cleanup_interval:
            return

        cutoff_time = current_time - 60

        for client_ip in list(self._request_times.keys()):
            self._request_times[client_ip] = [
                t for t in self._request_times[client_ip]
                if t > cutoff_time
            ]

            if not self._request_times[client_ip]:
                del self._request_times[client_ip]

        self._last_cleanup = current_time

    def _check_rate_limit(self, client_ip: str) -> tuple[bool, Optional[str]]:
        """检查速率限制

        Returns:
            (is_allowed, error_message)
        """
        current_time = time.time()
        cutoff_time = current_time - 60

        request_times = self._request_times[client_ip]
        recent_requests = [t for t in request_times if t > cutoff_time]

        if len(recent_requests) >= self.requests_per_minute:
            oldest_request = min(recent_requests)
            wait_time = int(60 - (current_time - oldest_request)) + 1
            return False, f"请求过于频繁，请等待 {wait_time} 秒后再试"

        self._request_times[client_ip] = recent_requests + [current_time]

        return True, None

    async def dispatch(self, request: Request, call_next):
        """处理请求"""
        client_ip = self._get_client_ip(request)

        self._cleanup_old_requests()

        if request.url.path.startswith("/api/"):
            is_allowed, error_msg = self._check_rate_limit(client_ip)

            if not is_allowed:
                return JSONResponse(
                    status_code=429,
                    content={
                        "code": 429,
                        "message": error_msg,
                        "detail": "Too Many Requests"
                    }
                )

        response = await call_next(request)
        return response
