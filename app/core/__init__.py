# -*- coding: utf-8 -*-
"""
硬件研发部测试报告审核平台 - 核心模块
"""

from app.core.config import get_settings, init_directories
from app.core.logger import get_logger
from app.core.exceptions import (
    BaseAPIException,
    FileException,
    FileNotFoundException,
    FileSizeExceededException,
    InvalidFileExtensionException,
    ExcelParseException,
    ValidationException,
    AIException,
    AIServiceUnavailableException,
    AIResponseParseException,
    AnalysisException,
    NoDataFoundException,
)
from app.core.template_config import standard_config, StandardTemplateConfig

__version__ = "1.2.0"

__all__ = [
    "get_settings",
    "init_directories",
    "get_logger",
    "BaseAPIException",
    "FileNotFoundException",
    "FileException",
    "FileSizeExceededException",
    "InvalidFileExtensionException",
    "ExcelParseException",
    "ValidationException",
    "AIException",
    "AIServiceUnavailableException",
    "AIResponseParseException",
    "AnalysisException",
    "NoDataFoundException",
    "standard_config",
    "StandardTemplateConfig",
]


def init_directories():
    """初始化必要的目录"""
    from app.core.config import init_directories as _init
    _init()
