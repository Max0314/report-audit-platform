# -*- coding: utf-8 -*-
"""
硬件研发部测试报告审核平台 - 审核路由
入口A：智能报告审核与校验系统 API
"""

import io
import json
import time
import asyncio
import concurrent.futures
import secrets
from typing import List, Optional
from pathlib import Path
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, File, UploadFile, Form, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.core import (
    get_logger,
    get_settings,
    FileException,
    InvalidFileExtensionException,
    FileSizeExceededException,
    AIException,
)
from app.services import (
    ExcelParser,
    AIChecker,
    validation_service,
)
from app.auth import current_user_key, require_task_owner
from app.object_store import ObjectStoreError
from app.storage import persist_upload, rollback_objects, safe_filename

logger = get_logger("review_router")
router = APIRouter(prefix="/api/v1/review", tags=["审核"])

# 并发控制：最多同时处理 2 个审核任务
MAX_CONCURRENT_TASKS = 2
review_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
# 线程池用于 CPU 密集型操作
thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_TASKS)
# 当前活跃任务计数
_active_tasks = 0
_active_tasks_lock = asyncio.Lock()


# ============ 响应模型 ============

class ReviewUploadResponse(BaseModel):
    """上传响应"""
    task_id: str
    filename: str
    message: str


class ReviewStatusResponse(BaseModel):
    """审核状态响应"""
    task_id: str
    status: str
    progress: int
    message: str


class ReviewResultResponse(BaseModel):
    """审核结果响应"""
    task_id: str
    filename: str
    status: str
    summary: dict
    details: dict


class ReviewErrorItem(BaseModel):
    """错误项"""
    type: str
    sheet: str
    location: str
    original: str
    suggestion: str
    reason: str


# ============ 任务存储 ============

from threading import Lock
from collections import OrderedDict
import time

class ThreadSafeTaskStore:
    """线程安全的任务存储"""

    def __init__(self, max_tasks: int = 100, ttl_hours: int = 24):
        self._tasks = OrderedDict()
        self._lock = Lock()
        self._max_tasks = max_tasks
        self._ttl_hours = ttl_hours

    def create_task(self, task_id: str, task_data: dict) -> bool:
        """创建新任务"""
        with self._lock:
            self._cleanup_expired()

            if len(self._tasks) >= self._max_tasks:
                oldest_key = next(iter(self._tasks))
                del self._tasks[oldest_key]

            task_data['created_at'] = time.time()
            task_data['last_access'] = time.time()
            self._tasks[task_id] = task_data
            return True

    def get_task(self, task_id: str) -> Optional[dict]:
        """获取任务"""
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]['last_access'] = time.time()
                return self._tasks[task_id]
            return None

    def update_task(self, task_id: str, **kwargs) -> bool:
        """更新任务"""
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].update(kwargs)
                self._tasks[task_id]['last_access'] = time.time()

                # 添加调试日志
                progress = kwargs.get('progress')
                message = kwargs.get('message', '')
                status = kwargs.get('status', '')
                logger.info(f"[任务更新] task_id={task_id}, status={status}, progress={progress}%, message={message}")
                
                return True
            return False

    def delete_task(self, task_id: str) -> bool:
        """删除任务"""
        with self._lock:
            if task_id in self._tasks:
                del self._tasks[task_id]
                return True
            return False

    def _cleanup_expired(self):
        """清理过期任务"""
        current_time = time.time()
        expired_keys = []

        for task_id, task in self._tasks.items():
            age_hours = (current_time - task.get('created_at', current_time)) / 3600
            if age_hours > self._ttl_hours:
                expired_keys.append(task_id)

        for key in expired_keys:
            del self._tasks[key]

        if expired_keys:
            logger.info(f"清理了 {len(expired_keys)} 个过期任务")

    def cleanup_all(self):
        """清理所有过期任务"""
        with self._lock:
            self._cleanup_expired()

    def get_stats(self) -> dict:
        """获取统计信息"""
        with self._lock:
            self._cleanup_expired()
            return {
                'total_tasks': len(self._tasks),
                'max_tasks': self._max_tasks,
                'ttl_hours': self._ttl_hours
            }


review_tasks = ThreadSafeTaskStore(max_tasks=100, ttl_hours=24)


@router.get("/queue")
async def get_queue_info():
    """获取队列信息"""
    global _active_tasks
    return {
        "active_tasks": _active_tasks,
        "max_concurrent": MAX_CONCURRENT_TASKS,
        "queue_size": max(0, _active_tasks - MAX_CONCURRENT_TASKS) if _active_tasks > MAX_CONCURRENT_TASKS else 0
    }


# ============ API 端点 ============

@router.post("/upload", response_model=ReviewUploadResponse)
async def upload_report(request: Request, file: UploadFile = File(...)):
    """上传测试报告文件"""
    settings = get_settings()

    # 验证文件类型
    filename = safe_filename(file.filename or "upload.xlsx")
    file_ext = Path(filename).suffix.lower()
    if file_ext not in settings.file_storage.allowed_extensions:
        raise InvalidFileExtensionException(
            file_ext,
            settings.file_storage.allowed_extensions
        )

    # 验证文件大小
    contents = await file.read()
    if len(contents) > settings.file_storage.max_file_size:
        raise FileSizeExceededException(
            len(contents),
            settings.file_storage.max_file_size
        )

    # 生成任务 ID
    task_id = f"review_{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(6)}"

    # OSS 是生产持久层；必须在本地解析和后台任务启动前写入成功。
    try:
        object_key = await persist_upload("review", task_id, filename, contents)
    except ObjectStoreError as exc:
        logger.error("审核原文件写入 OSS 失败: task_id=%s, error=%s", task_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail="文件持久化失败，请稍后重试") from exc

    # 本地文件只作为解析缓存。
    file_path = Path(settings.file_storage.upload_dir) / f"{task_id}_{filename}"

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "wb") as f:
            f.write(contents)
    except OSError as exc:
        await rollback_objects([object_key])
        raise HTTPException(status_code=503, detail="本地解析缓存写入失败") from exc

    # 初始化任务
    review_tasks.create_task(task_id, {
        "status": "processing",
        "filename": filename,
        "file_path": str(file_path),
        "object_key": object_key,
        "owner_key": current_user_key(request),
        "progress": 5,
        "message": "任务已创建，正在准备解析...",
        "result": None,
        "error": None
    })

    # 触发异步处理（带并发控制）
    asyncio.create_task(run_review_with_semaphore(task_id))

    # 判断当前负载
    queue_message = "审核任务已启动"
    if _active_tasks >= MAX_CONCURRENT_TASKS:
        queue_message = f"系统繁忙，您的任务已加入队列（当前有 {_active_tasks} 个任务在排队）"

    logger.info("审核文件已持久化并创建任务: task_id=%s", task_id)

    return ReviewUploadResponse(
        task_id=task_id,
        filename=filename,
        message=queue_message
    )


@router.get("/status/{task_id}", response_model=ReviewStatusResponse)
async def get_review_status(request: Request, task_id: str):
    """获取审核任务状态"""
    task = review_tasks.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    require_task_owner(request, task)

    return ReviewStatusResponse(
        task_id=task_id,
        status=task["status"],
        progress=task.get("progress", 0),
        message=task.get("message", "")
    )


@router.get("/result/{task_id}")
async def get_review_result(request: Request, task_id: str):
    """获取审核结果"""
    task = review_tasks.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    require_task_owner(request, task)

    if task["status"] == "processing":
        return JSONResponse(
            status_code=202,
            content={
                "message": "审核进行中",
                "progress": task.get("progress", 0)
            }
        )

    if task["status"] == "error":
        return JSONResponse(
            status_code=500,
            content={
                "message": "审核失败",
                "error": task.get("error", "未知错误")
            }
        )

    return task.get("result", {})


@router.post("/check-ai-available")
async def check_ai_available():
    """检查 AI 服务是否可用"""
    ai_checker = AIChecker()
    return {
        "available": ai_checker.is_available(),
        "message": "AI 服务可用" if ai_checker.is_available() else "请配置 DeepSeek API Key"
    }


@router.get("/export/{task_id}")
async def export_review_report(request: Request, task_id: str):
    """导出审核报告"""
    from app.services import export_service

    task = review_tasks.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    require_task_owner(request, task)

    if task["status"] != "completed":
        raise HTTPException(status_code=400, detail="任务未完成，无法导出报告")

    result = task.get("result", {})
    if not result:
        raise HTTPException(status_code=400, detail="没有审核结果可导出")

    try:
        excel_bytes = export_service.generate_review_report(
            result_data=result,
            filename=task["filename"]
        )

        output = io.BytesIO(excel_bytes)
        output.seek(0)

        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": "attachment; filename=review-report.xlsx; filename*=UTF-8''"
                + quote(f"审核报告_{Path(task['filename']).stem}.xlsx", safe="")
            }
        )

    except Exception as e:
        logger.error(f"导出报告失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"导出报告失败: {str(e)}")


async def run_review_with_semaphore(task_id: str):
    """带并发控制的审核任务运行器"""
    global _active_tasks
    
    async with _active_tasks_lock:
        _active_tasks += 1
        logger.info(f"[并发] 活跃任务数: {_active_tasks}/{MAX_CONCURRENT_TASKS}")
    
    async with review_semaphore:
        try:
            await process_review(task_id)
        finally:
            async with _active_tasks_lock:
                _active_tasks -= 1
                logger.info(f"[并发] 活跃任务数: {_active_tasks}/{MAX_CONCURRENT_TASKS}")


async def run_cpu_bound(func, *args, **kwargs):
    """在线程池中运行CPU密集型任务"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(thread_pool, lambda: func(*args, **kwargs))


async def process_review(task_id: str):
    """处理审核任务"""
    task = review_tasks.get_task(task_id)
    if not task:
        logger.error(f"任务不存在: {task_id}")
        return

    # 辅助函数：更新进度并添加延时
    async def update_progress_slow(progress, message, delay=0.5):
        review_tasks.update_task(task_id, progress=progress, message=message)
        await asyncio.sleep(delay)

    review_tasks.update_task(task_id,
        status="processing",
        progress=5,
        message="开始解析文件"
    )
    await asyncio.sleep(0.3)

    parser = None
    try:
        # 步骤1: 文件解析（CPU密集型）
        await update_progress_slow(10, "正在解析Excel文件...")
        parser = ExcelParser()
        await run_cpu_bound(parser.load_from_file, task["file_path"])
        sheet_names = parser.get_sheet_names()

        await update_progress_slow(15, f"解析完成，共 {len(sheet_names)} 个 Sheet")

        # 步骤2: 数据提取（CPU密集型）
        await update_progress_slow(20, "正在提取数据...")
        text_content = await run_cpu_bound(parser.extract_text_content)
        key_info = await run_cpu_bound(parser.extract_key_info)
        dates = await run_cpu_bound(parser.extract_dates)
        sheets_data = await run_cpu_bound(parser.read_all_sheets)

        await update_progress_slow(25, "数据提取完成")

        # 步骤3: 项目基础信息（CPU密集型）
        await update_progress_slow(30, "正在提取项目基础信息...")
        project_info = await run_cpu_bound(validation_service.extract_project_info, sheets_data)

        # 步骤4: 日期校验
        await update_progress_slow(35, "正在验证日期...")
        date_validation = await run_cpu_bound(validation_service.validate_dates, dates)

        await update_progress_slow(40, "日期验证完成")

        # 步骤5: 关键信息一致性校验
        await update_progress_slow(45, "正在验证关键信息一致性...")
        consistency_validation = await run_cpu_bound(validation_service.validate_key_info_consistency, key_info)

        # 步骤6: 公司信息一致性校验
        await update_progress_slow(50, "正在验证公司信息一致性...")
        company_info_validation = await run_cpu_bound(validation_service.validate_company_info, sheets_data)

        # 步骤7: 测试值逻辑校验（最重CPU操作）
        await update_progress_slow(55, "正在验证测试值逻辑...")
        value_logic_validation = await run_cpu_bound(
            validation_service.validate_test_value_logic,
            sheets_data,
            workbook_path=task["file_path"]
        )

        # 步骤8: 提取测试项
        await update_progress_slow(65, "正在提取测试项...")
        test_items_result = await run_cpu_bound(validation_service.extract_test_items, sheets_data)

        # 步骤9: AI智能分类（网络I/O，但也在线程池执行避免阻塞）
        ai_classification = {
            "available": False,
            "categories": {},
            "summary": "AI 分类未启用"
        }

        ai_checker = AIChecker()
        if ai_checker.is_available() and test_items_result.get("items"):
            await update_progress_slow(70, "正在进行 AI 智能分类...")

            try:
                ai_classification = await run_cpu_bound(
                    ai_checker.classify_test_items,
                    test_items_result["items"],
                    product_type=None
                )
                ai_classification["available"] = True
            except AIException as e:
                logger.warning(f"AI 分类失败: {str(e)}")

        # 步骤10: 测试完整性校验
        await update_progress_slow(75, "正在验证测试完整性...")
        completeness_validation = await run_cpu_bound(
            validation_service.validate_test_completeness,
            sheets_data,
            ai_classification=ai_classification
        )

        completeness_validation["ai_classification"] = ai_classification

        # 步骤11: AI错别字检查
        ai_result = {
            "available": False,
            "issues": [],
            "summary": "AI 检查未启用"
        }

        if ai_checker.is_available():
            await update_progress_slow(85, "正在进行 AI 错别字检查...")

            try:
                ai_result = await run_cpu_bound(ai_checker.check_text, text_content)
                ai_result["available"] = True
            except AIException as e:
                ai_result["error"] = str(e)
                logger.error(f"AI 检查失败: {str(e)}")
        else:
            logger.warning("AI 服务未配置，跳过错别字检查")

        # 完成
        await update_progress_slow(95, "正在生成结果...")

        total_issues = (
            len(date_validation.get("invalid", [])) +
            len(consistency_validation.get("inconsistencies", [])) +
            len(company_info_validation.get("issues", [])) +
            len(value_logic_validation.get("issues", [])) +
            len(completeness_validation.get("missing_items", [])) +
            len(ai_result.get("issues", []))
        )

        extracted_items_count = test_items_result.get("total", 0)
        ai_categories_count = len(ai_classification.get("categories", {}))

        result = {
            "task_id": task_id,
            "filename": task["filename"],
            "status": "completed",
            "project_info": project_info,
            "summary": {
                "total_sheets": len(sheet_names),
                "total_issues": total_issues,
                "date_issues": len(date_validation.get("invalid", [])),
                "consistency_issues": len(consistency_validation.get("inconsistencies", [])),
                "company_info_issues": len(company_info_validation.get("issues", [])),
                "logic_issues": len(value_logic_validation.get("issues", [])),
                "completeness_issues": len(completeness_validation.get("missing_items", [])),
                "ai_issues": len(ai_result.get("issues", [])),
                "extracted_test_items": extracted_items_count,
                "ai_classified_categories": ai_categories_count
            },
            "details": {
                "date_validation": date_validation,
                "consistency_validation": consistency_validation,
                "company_info_validation": company_info_validation,
                "value_logic_validation": value_logic_validation,
                "completeness_validation": completeness_validation,
                "ai_result": ai_result,
                "test_items_extraction": test_items_result,
                "ai_classification": ai_classification
            }
        }

        await update_progress_slow(100, "审核完成", 0.3)
        review_tasks.update_task(task_id,
            status="completed",
            result=result,
            progress=100,
            message="审核完成"
        )

        logger.info(f"审核任务完成: {task_id}, 发现 {total_issues} 个问题")

    except Exception as e:
        logger.error(f"审核任务失败: {task_id}, 错误: {str(e)}")
        import traceback
        traceback.print_exc()
        review_tasks.update_task(task_id,
            status="error",
            error=str(e),
            message="审核失败"
        )
    finally:
        if parser is not None:
            try:
                parser.close()
            except Exception:
                pass
        # OSS 是唯一持久副本；任务处理结束后本地上传仅是可删除缓存。
        try:
            Path(task["file_path"]).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("本地解析缓存清理失败: task_id=%s, error=%s", task_id, type(exc).__name__)


# ============ 辅助函数 ============

def create_error_response(code: int, message: str):
    """创建错误响应"""
    return JSONResponse(
        status_code=code,
        content={"message": message}
    )
