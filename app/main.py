# -*- coding: utf-8 -*-
"""
硬件研发部测试报告审核平台 - 主应用
FastAPI 后端入口
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.core import get_logger, get_settings, init_directories, BaseAPIException
from app.routers import review_router, analysis_router
from app.middleware import RateLimiter

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
    logger.info(f"配置加载完成，服务器端口: {settings.server.port}")

    yield

    # 关闭时执行
    logger.info("应用关闭中...")


# 创建 FastAPI 应用
app = FastAPI(
    title="硬件研发部测试报告审核平台",
    description="智能测试报告审核与数据分析系统",
    version="1.0.0",
    lifespan=lifespan
)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RateLimiter, requests_per_minute=60, burst_size=10)

app.add_middleware(GZipMiddleware, minimum_size=500)


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


# ============ 页面路由 ============

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """首页 - 入口选择"""
    return templates.TemplateResponse(
        "index.html",
        {"request": request}
    )


@app.get("/review", response_class=HTMLResponse)
async def review_page(request: Request):
    """入口A - 智能报告审核与校验系统"""
    return templates.TemplateResponse(
        "review.html",
        {"request": request}
    )


@app.get("/analysis", response_class=HTMLResponse)
async def analysis_page(request: Request):
    """入口B - 测数聚合智能分析系统"""
    return templates.TemplateResponse(
        "analysis.html",
        {"request": request}
    )


@app.get("/test-progress", response_class=HTMLResponse)
async def test_progress_page():
    """测试进度条页面"""
    with open("templates/test_progress.html", "r", encoding="utf-8") as f:
        return f.read()


# ============ API 路由 ============

app.include_router(review_router)
app.include_router(analysis_router)


# ============ Favicon（防止浏览器请求favicon导致延迟）============

@app.get("/favicon.ico")
async def favicon():
    """返回空响应防止404延迟"""
    return Response(content=b"", media_type="image/x-icon")


# ============ 健康检查 ============

@app.get("/health")
async def health_check():
    """健康检查"""
    import psutil
    import gc

    process = psutil.Process()
    memory_info = process.memory_info()

    gc.collect()
    memory_data = {
        "rss_mb": round(memory_info.rss / 1024 / 1024, 2),
        "vms_mb": round(memory_info.vms / 1024 / 1024, 2),
    }

    cpu_percent = process.cpu_percent(interval=0.1)

    return {
        "status": "healthy",
        "message": "服务运行正常",
        "system": {
            "cpu_percent": round(cpu_percent, 2),
            "memory": memory_data,
            "threads": process.num_threads(),
        },
        "gc": {
            "collections": gc.get_count(),
        }
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
    return JSONResponse(
        status_code=500,
        content={
            "code": 500,
            "message": "服务器内部错误",
            "details": {"error": str(exc)}
        }
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
