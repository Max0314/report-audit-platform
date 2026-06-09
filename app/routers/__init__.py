# -*- coding: utf-8 -*-
"""
硬件研发部测试报告审核平台 - 路由模块
"""

from app.routers.review import router as review_router
from app.routers.analysis import router as analysis_router

__all__ = [
    "review_router",
    "analysis_router",
]
