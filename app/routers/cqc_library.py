"""认证库管理，仅管理员上传、查看版本及回滚。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.auth import require_library_admin
from app.services import cqc_library as library

router = APIRouter(prefix="/api/v1/cqc-library", tags=["CQC 认证库"])
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cqc-library")
_busy = False


async def execute(function, *args):
    try:
        return await asyncio.get_running_loop().run_in_executor(_executor, partial(function, *args))
    except library.LibraryConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception:
        raise HTTPException(503, "认证库存储暂时不可用，请刷新检查当前版本后重试") from None


@router.get("")
async def list_versions(request: Request):
    await require_library_admin(request)
    return await execute(library.catalog)


@router.post("/upload")
async def upload(request: Request, file: UploadFile = File(...), expected_revision: int = Form(..., ge=0)):
    global _busy
    try:
        actor = await require_library_admin(request)
        if _busy:
            raise HTTPException(429, "认证库正在更新，请稍后重试")
        if not (file.filename or "").lower().endswith(".xlsx"):
            raise HTTPException(400, "仅支持 .xlsx 格式")
        _busy = True
        try:
            contents = await file.read(5 * 1024 * 1024 + 1)
            if len(contents) > 5 * 1024 * 1024:
                raise HTTPException(413, "Excel 不能超过 5 MB")
            return await execute(library.publish, contents, file.filename, actor, expected_revision)
        finally:
            _busy = False
    finally:
        await file.close()


class Activation(BaseModel):
    version: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_revision: int = Field(ge=0)


@router.post("/activate")
async def activate(request: Request, body: Activation):
    actor = await require_library_admin(request)
    return await execute(library.activate, body.version, actor, body.expected_revision)
