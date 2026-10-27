# -*- coding: utf-8 -*-
"""
硬件研发部测试报告审核平台 - 主应用
FastAPI 后端入口
"""

from contextlib import asynccontextmanager
import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.gzip import GZipMiddleware

from app.core import get_logger, get_settings, init_directories, BaseAPIException
from app.routers import review_router, analysis_router
from app.routers.cqc import router as cqc_router
from app.routers.cqc_library import router as cqc_library_router
from app.auth import can_manage_library, require_library_admin
from app.middleware import RateLimiter
from app.auth import close_auth_client, hardware_auth_middleware, validate_auth_config
from app.storage import check_storage_ready

logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    logger.info("应用启动中...")

    # 初始化目录
    init_directories()

    # 加载配置
    settings = get_settings()
    if settings.server.env.lower() == "production":
        if settings.object_storage.backend.lower() != "oss":
            raise RuntimeError("生产环境 STORAGE_BACKEND 必须为 oss")
        if settings.object_storage.prefix.strip("/") != "prod/report-audit":
            raise RuntimeError("生产环境 OSS_PREFIX 必须为 prod/report-audit")
    validate_auth_config()
    await check_storage_ready()
    logger.info(f"配置加载完成，服务器端口: {settings.server.port}")

    yield

    await close_auth_client()
    logger.info("应用关闭中...")


# 创建 FastAPI 应用
_startup_settings = get_settings()
_production = _startup_settings.server.env.lower() == "production"

app = FastAPI(
    title="硬件研发部测试报告审核平台",
    description="智能测试报告审核与数据分析系统",
    version="1.2.0",
    lifespan=lifespan,
    docs_url=None if _production else "/docs",
    redoc_url=None if _production else "/redoc",
    openapi_url=None if _production else "/openapi.json",
)

app.add_middleware(RateLimiter, requests_per_minute=300, burst_size=60)

app.add_middleware(GZipMiddleware, minimum_size=500)

# 报告服务不维护第二套账号，所有页面和 API 都复用 htmlsystm 的会话。
app.middleware("http")(hardware_auth_middleware)


# ============ 静态文件缓存 ============

class CacheStaticFiles(StaticFiles):
    """带缓存头的静态文件服务"""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        # 静态文件缓存 1 小时
        response.headers["Cache-Control"] = "public, max-age=3600"
        return response


# 挂载带缓存的静态文件
app.mount("/static", CacheStaticFiles(directory="static"), name="static")

# 配置模板引擎
templates = Jinja2Templates(directory="templates")


def _template_context(request: Request) -> dict:
    settings = get_settings()
    base_path = settings.hardware_auth.public_prefix.rstrip("/") if settings.hardware_auth.enabled else ""
    return {"request": request, "base_path": base_path,
            "can_manage_library": can_manage_library(getattr(request.state, "hardware_user", {}))}


# ============ 页面路由 ============

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """保留原门户入口，直接进入审核页面。"""
    return RedirectResponse(_template_context(request)["base_path"] + "/review", status_code=302)


@app.get("/review", response_class=HTMLResponse)
async def review_page(request: Request):
    """入口A - 智能报告审核与校验系统"""
    return templates.TemplateResponse(
        "review.html",
        _template_context(request),
    )


@app.get("/analysis", response_class=HTMLResponse)
async def analysis_page(request: Request):
    """入口B - 测数聚合智能分析系统"""
    return templates.TemplateResponse(
        "analysis.html",
        _template_context(request),
    )


@app.get("/test-progress", response_class=HTMLResponse)
async def test_progress_page():
    """测试进度条页面"""
    with open("templates/test_progress.html", "r", encoding="utf-8") as f:
        return f.read()


# ============ API 路由 ============

@app.get("/cqc-review", response_class=HTMLResponse)
async def cqc_review_page(request: Request):
    return templates.TemplateResponse("cqc_review.html", _template_context(request))


@app.get("/cqc-compare", response_class=HTMLResponse)
async def cqc_compare_page(request: Request):
    return templates.TemplateResponse("cqc_compare.html", _template_context(request))

app.include_router(review_router)
app.include_router(analysis_router)
app.include_router(cqc_router)
app.include_router(cqc_library_router)


@app.get("/cqc-library", response_class=HTMLResponse)
async def cqc_library_page(request: Request):
    await require_library_admin(request)
    return templates.TemplateResponse("cqc_library.html", _template_context(request))


# ============ Favicon（防止浏览器请求favicon导致延迟）============

@app.get("/favicon.ico")
async def favicon():
    """返回空响应防止404延迟"""
    return Response(content=b"", media_type="image/x-icon")


# ============ 健康检查 ============

@app.get("/health")
async def health_check():
    """公开的轻量存活探针；不泄露主机资源和存储凭据。"""
    return {
        "status": "healthy",
        "service": "report-audit-platform",
        "storage": (os.getenv("STORAGE_BACKEND") or "local").strip().lower(),
        "hardware_auth": get_settings().hardware_auth.enabled,
    }


@app.get("/metrics")
async def get_metrics():
    """获取系统指标"""
    import psutil
    import time

    process = psutil.Process()

    boot_time = psutil.boot_time()
    uptime_seconds = time.time() - boot_time

    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')

    return {
        "uptime_seconds": int(uptime_seconds),
        "cpu": {
            "count": psutil.cpu_count(),
            "percent": psutil.cpu_percent(interval=0.1),
        },
        "memory": {
            "total_gb": round(memory.total / 1024**3, 2),
            "available_gb": round(memory.available / 1024**3, 2),
            "percent": memory.percent,
        },
        "disk": {
            "total_gb": round(disk.total / 1024**3, 2),
            "used_gb": round(disk.used / 1024**3, 2),
            "percent": round(disk.percent, 2),
        },
        "process": {
            "pid": process.pid,
            "threads": process.num_threads(),
            "open_files": len(process.open_files()),
            "connections": len(process.connections()),
        }
    }


# ============ 异常处理 ============

@app.exception_handler(BaseAPIException)
async def api_exception_handler(request: Request, exc: BaseAPIException):
    """API 异常处理"""
    return JSONResponse(
        status_code=exc.code,
        content=exc.to_dict()
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """通用异常处理"""
    logger.error(f"未处理的异常: {str(exc)}")
    content = {
        "code": 500,
        "message": "服务器内部错误",
    }
    if not _production:
        content["details"] = {"error": str(exc)}
    return JSONResponse(
        status_code=500,
        content=content,
    )


# ============ 启动入口 ============

if __name__ == "__main__":
    import uvicorn

    settings = get_settings()

    uvicorn.run(
        "app.main:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=settings.server.debug,
        loop="asyncio",
        http="auto",
        ws="auto",
        access_log=False,  # 禁用访问日志提升性能
    )
