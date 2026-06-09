# -*- coding: utf-8 -*-
"""
硬件研发部测试报告审核平台 - 异常模块
自定义异常类，统一错误处理
"""

from typing import Any, Dict, Optional


class BaseAPIException(Exception):
    """基础异常类"""

    def __init__(
        self,
        message: str,
        code: int = 400,
        details: Optional[Dict[str, Any]] = None
    ):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details
        }


class FileException(BaseAPIException):
    """文件相关异常"""

    def __init__(self, message: str = "文件处理错误", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code=400, details=details)


class FileNotFoundException(FileException):
    """文件不存在异常"""

    def __init__(self, filename: str):
        super().__init__(
            message=f"文件不存在: {filename}",
            details={"filename": filename}
        )


class FileSizeExceededException(FileException):
    """文件大小超出限制异常"""

    def __init__(self, size: int, max_size: int):
        super().__init__(
            message=f"文件大小超出限制: {size} bytes > {max_size} bytes",
            details={"size": size, "max_size": max_size}
        )


class InvalidFileExtensionException(FileException):
    """无效文件扩展名异常"""

    def __init__(self, extension: str, allowed_extensions: list):
        super().__init__(
            message=f"不支持的文件格式: {extension}",
            details={"extension": extension, "allowed_extensions": allowed_extensions}
        )


class ExcelParseException(FileException):
    """Excel 解析异常"""

    def __init__(self, message: str, sheet_name: Optional[str] = None):
        details = {"sheet_name": sheet_name} if sheet_name else {}
        super().__init__(message=f"Excel 解析错误: {message}", details=details)


class ValidationException(BaseAPIException):
    """验证相关异常"""

    def __init__(self, message: str = "验证失败", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code=422, details=details)


class AIException(BaseAPIException):
    """AI 服务相关异常"""

    def __init__(self, message: str = "AI 服务调用失败", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code=500, details=details)


class AIServiceUnavailableException(AIException):
    """AI 服务不可用异常"""

    def __init__(self, message: str = "AI 服务暂时不可用"):
        super().__init__(message)


class AIResponseParseException(AIException):
    """AI 响应解析异常"""

    def __init__(self, message: str = "AI 响应解析失败"):
        super().__init__(message)


class AnalysisException(BaseAPIException):
    """数据分析相关异常"""

    def __init__(self, message: str = "数据分析失败", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code=500, details=details)


class NoDataFoundException(AnalysisException):
    """未找到数据异常"""

    def __init__(self, message: str = "未找到相关数据"):
        super().__init__(message)
