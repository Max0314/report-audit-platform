# -*- coding: utf-8 -*-
"""
硬件研发部测试报告审核平台 - 分析路由
入口B：测数聚合智能分析系统 API

支持两种使用方式：
1. 旧接口（向后兼容）- 使用 analysis_service
2. 新架构（可扩展）- 使用 analysis_pipeline
"""

import io
import json
import secrets
import shutil
from typing import List, Optional
from pathlib import Path
from datetime import datetime
import asyncio

from fastapi import APIRouter, File, UploadFile, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core import (
    get_logger,
    get_settings,
    InvalidFileExtensionException,
    FileSizeExceededException,
    NoDataFoundException,
)
from app.services import analysis_service
from app.services.analysis_service_v2 import (
    AnalysisPipeline,
    AnalysisResult,
    AnalysisStage
)
from app.auth import current_user_key, require_task_owner
from app.object_store import ObjectStoreError
from app.storage import persist_upload, rollback_objects, safe_filename

logger = get_logger("analysis_router")
router = APIRouter(prefix="/api/v1/analysis", tags=["分析"])

# 架构切换：设置为 True 使用新架构
USE_NEW_ARCHITECTURE = True


# ============ 响应模型 ============


class AnalysisUploadResponse(BaseModel):
    """上传响应"""
    task_id: str
    files_count: int
    message: str


class AnalysisStatusResponse(BaseModel):
    """分析状态响应"""
    task_id: str
    status: str
    progress: int
    message: str


class AnalysisResultResponse(BaseModel):
    """分析结果响应"""
    task_id: str
    status: str
    total_records: int
    statistics: dict
    charts: dict


# ============ 任务存储 ============


analysis_tasks = {}


# ============ API 端点 ============


@router.post("/upload", response_model=AnalysisUploadResponse)
async def upload_reports(request: Request, files: List[UploadFile] = File(...)):
    """批量上传测试报告文件"""
    settings = get_settings()

    if not files or len(files) == 0:
        raise HTTPException(status_code=400, detail="请至少上传一个文件")

    # 生成任务 ID
    task_id = f"analysis_{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(6)}"

    # 验证并保存文件
    file_contents = []
    valid_filenames = []

    for file in files:
        # 验证文件类型
        filename = safe_filename(file.filename or "upload.xlsx")
        file_ext = Path(filename).suffix.lower()
        if file_ext not in settings.file_storage.allowed_extensions:
            logger.warning(f"跳过无效文件类型: {file_ext}")
            continue

        # 读取内容
        contents = await file.read()

        # 验证文件大小
        if len(contents) > settings.file_storage.max_file_size:
            logger.warning("跳过过大文件: task_id=%s", task_id)
            continue

        file_contents.append(contents)
        valid_filenames.append(filename)

    if len(file_contents) == 0:
        raise HTTPException(status_code=400, detail="没有有效的文件")

    # 先将所有原文件写入 OSS。任一失败就补偿删除本批已写对象，不启动分析。
    object_keys = []
    try:
        for index, (filename, contents) in enumerate(zip(valid_filenames, file_contents), start=1):
            object_keys.append(
                await persist_upload("analysis", task_id, filename, contents, index=index)
            )
    except ObjectStoreError as exc:
        await rollback_objects(object_keys)
        logger.error("分析原文件写入 OSS 失败: task_id=%s, error=%s", task_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail="文件持久化失败，请稍后重试") from exc

    # 保存到临时解析缓存
    temp_dir = Path(settings.file_storage.temp_dir) / task_id

    try:
        temp_dir.mkdir(parents=True, exist_ok=True)
        for index, (filename, contents) in enumerate(zip(valid_filenames, file_contents), start=1):
            file_path = temp_dir / f"{index:03d}-{filename}"
            with open(file_path, "wb") as f:
                f.write(contents)
    except OSError as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        await rollback_objects(object_keys)
        raise HTTPException(status_code=503, detail="本地解析缓存写入失败") from exc

    # 初始化任务
    analysis_tasks[task_id] = {
        "status": "pending",
        "files": valid_filenames,
        "file_contents": file_contents,
        "temp_dir": str(temp_dir),
        "object_keys": object_keys,
        "owner_key": current_user_key(request),
        "progress": 0,
        "result": None,
        "error": None,
        "architecture": "v2" if USE_NEW_ARCHITECTURE else "v1"
    }

    # 触发异步处理
    asyncio.create_task(process_analysis(task_id))

    logger.info(f"文件上传成功: {len(valid_filenames)} 个文件, 任务ID: {task_id}, 架构: {analysis_tasks[task_id]['architecture']}")

    return AnalysisUploadResponse(
        task_id=task_id,
        files_count=len(valid_filenames),
        message=f"成功上传 {len(valid_filenames)} 个文件，分析任务已启动"
    )


@router.get("/status/{task_id}", response_model=AnalysisStatusResponse)
async def get_analysis_status(request: Request, task_id: str):
    """获取分析任务状态"""
    if task_id not in analysis_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = analysis_tasks[task_id]
    require_task_owner(request, task)

    return AnalysisStatusResponse(
        task_id=task_id,
        status=task["status"],
        progress=task.get("progress", 0),
        message=task.get("message", "")
    )


@router.get("/result/{task_id}")
async def get_analysis_result(request: Request, task_id: str):
    """获取分析结果"""
    if task_id not in analysis_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = analysis_tasks[task_id]
    require_task_owner(request, task)

    if task["status"] == "processing":
        return JSONResponse(
            status_code=202,
            content={
                "message": "分析进行中",
                "progress": task.get("progress", 0)
            }
        )

    if task["status"] == "error":
        return JSONResponse(
            status_code=500,
            content={
                "message": "分析失败",
                "error": task.get("error", "未知错误")
            }
        )

    return task.get("result", {})


@router.get("/dashboard/meta")
async def get_dashboard_meta():
    """获取仪表盘元数据（支持扩展的）"""
    return {
        "available_aggregation_fields": [
            "测试项目",
            "测试项",
            "测试小项",
            "测试项名称"
        ],
        "available_statistics": [
            "max",
            "min",
            "mean",
            "std",
            "median"
        ],
        "available_chart_types": [
            "histogram",
            "scatter",
            "box",
            "trend"
        ],
        "architecture": "v2" if USE_NEW_ARCHITECTURE else "v1",
        "extensions": {
            "aggregators": ["item", "sheet"],  # 预留扩展
            "calculators": ["basic", "advanced"],  # 预留扩展
            "charts": ["histogram", "scatter", "box", "trend"]
        }
    }


async def process_analysis(task_id: str):
    """处理分析任务"""
    task = analysis_tasks[task_id]
    task["status"] = "processing"

    try:
        if USE_NEW_ARCHITECTURE:
            # ============ 新架构处理 ============
            logger.info(f"[任务 {task_id}] 使用新架构处理")

            task["progress"] = 10
            task["message"] = "正在初始化分析流水线..."

            # 创建流水线
            pipeline = AnalysisPipeline()

            # 【扩展点】在这里可以自定义流水线
            # 例如：
            # from my_extensions import MyAggregator
            # pipeline.set_aggregator(MyAggregator())

            # 添加进度钩子
            def update_progress_stage(stage_name):
                def hook(data):
                    if stage_name == "AGGREGATION":
                        task["progress"] = 30
                        task["message"] = "数据聚合完成"
                    elif stage_name == "STATISTICS":
                        task["progress"] = 60
                        task["message"] = "统计计算完成"
                    elif stage_name == "VISUALIZATION":
                        task["progress"] = 90
                        task["message"] = "图表生成完成"
                    elif stage_name == "COMPLETED":
                        task["progress"] = 100
                    return data

                return hook

            pipeline.add_hook(AnalysisStage.AGGREGATION, update_progress_stage("AGGREGATION"))
            pipeline.add_hook(AnalysisStage.STATISTICS, update_progress_stage("STATISTICS"))
            pipeline.add_hook(AnalysisStage.VISUALIZATION, update_progress_stage("VISUALIZATION"))
            pipeline.add_hook(AnalysisStage.COMPLETED, update_progress_stage("COMPLETED"))

            # 执行分析
            result = pipeline.run(
                task["file_contents"],
                task["files"],
                task_id
            )

            # 构建返回结果
            task_result = {
                "task_id": result.task_id,
                "status": result.status,
                "summary": result.summary,
                "statistics": result.statistics,
                "charts": result.charts,
                "architecture": "v2"
            }

            task["status"] = "completed"
            task["result"] = task_result
            task["message"] = "分析完成"

            logger.info(f"[任务 {task_id}] 新架构处理完成")

        else:
            # ============ 旧架构处理（向后兼容） ============
            logger.info(f"[任务 {task_id}] 使用旧架构处理")

            # 1. 数据聚合
            task["progress"] = 20
            task["message"] = "正在聚合数据..."

            aggregated_data = analysis_service.aggregate_data(
                task["file_contents"],
                task["files"]
            )

            # 2. 计算统计指标
            task["progress"] = 50
            task["message"] = "正在计算统计指标..."

            statistics = analysis_service.calculate_statistics(
                aggregated_data["aggregated_data"]
            )

            # 3. 生成图表
            task["progress"] = 70
            task["message"] = "正在生成可视化图表..."

            charts = analysis_service.generate_charts(
                aggregated_data["aggregated_data"],
                statistics
            )

            # 4. 生成结果
            task["progress"] = 100

            task_result = {
                "task_id": task_id,
                "status": "completed",
                "summary": {
                    "total_records": aggregated_data["total_records"],
                    "files_processed": aggregated_data["files_processed"],
                    "unique_test_items": len(statistics)
                },
                "statistics": statistics,
                "charts": charts,
                "architecture": "v1"
            }

            task["status"] = "completed"
            task["result"] = task_result
            task["message"] = "分析完成"

            logger.info(f"[任务 {task_id}] 旧架构处理完成，记录数: {aggregated_data['total_records']}")

    except NoDataFoundException as e:
        logger.error(f"[任务 {task_id}] 分析失败: {str(e)}")
        task["status"] = "error"
        task["error"] = str(e)
        task["message"] = "未找到有效数据"

    except Exception as e:
        logger.error(f"[任务 {task_id}] 分析失败: {str(e)}")
        task["status"] = "error"
        task["error"] = str(e)
        task["message"] = "分析失败"

    finally:
        # 原文件已持久化至 OSS，释放内存和本地临时解析缓存。
        task.pop("file_contents", None)
        shutil.rmtree(task.get("temp_dir", ""), ignore_errors=True)
