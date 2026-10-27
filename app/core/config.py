# -*- coding: utf-8 -*-
"""
硬件研发部测试报告审核平台 - 配置文件
核心配置加载器，支持 YAML 格式配置文件
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional
from dotenv import load_dotenv

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


# 加载环境变量
load_dotenv()


class ServerSettings(BaseSettings):
    """服务器配置"""
    host: str = Field(default="0.0.0.0", description="服务器主机")
    port: int = Field(default=8000, description="服务器端口")
    env: str = Field(default="development", description="运行环境")
    debug: bool = Field(default=True, description="调试模式")

    class Config:
        env_prefix = "SERVER_"


class AIServiceSettings(BaseSettings):
    """AI 服务配置"""
    provider: str = Field(
        default="neoflow",
        description="AI 服务提供商 (neoflow/deepseek/dashscope)",
    )
    api_key: str = Field(default="", description="API Key")
    base_url: str = Field(default="https://neoflow.neo-net.com/api/v1", description="API 基础 URL")
    model: str = Field(default="deepseek/deepseek-v4-pro", description="模型名称")
    temperature: float = Field(default=0.1, description="温度参数")
    max_tokens: int = Field(default=4000, description="最大 Token 数")
    timeout: int = Field(default=120, description="超时时间（秒）")
    enable_thinking: bool = Field(default=False, description="是否启用深度思考模式 (仅某些模型支持)")

    class Config:
        env_prefix = "AI_"


class FileStorageSettings(BaseSettings):
    """文件存储配置"""
    upload_dir: str = Field(default="./data/uploads", description="上传目录")
    temp_dir: str = Field(default="./data/temp", description="临时目录")
    archive_dir: str = Field(default="./data/archive", description="归档目录")
    max_file_size: int = Field(default=100 * 1024 * 1024, description="最大文件大小（字节）")
    allowed_extensions: list = Field(default=[".xlsx", ".xls", ".xlsm"], description="允许的文件扩展名")


class ObjectStorageSettings(BaseSettings):
    """业务文件持久化配置（生产环境必须为 OSS）。"""
    backend: str = Field(
        default="local",
        validation_alias="STORAGE_BACKEND",
        description="local / oss",
    )
    endpoint: str = Field(default="", description="OSS endpoint")
    bucket: str = Field(default="", description="OSS bucket")
    access_key_id: str = Field(default="", description="OSS AccessKey ID")
    access_key_secret: str = Field(default="", description="OSS AccessKey Secret")
    prefix: str = Field(default="", description="本服务独占的 OSS key 前缀")

    class Config:
        env_prefix = "OSS_"


class HardwareAuthSettings(BaseSettings):
    """复用硬件门户会话的配置。"""
    enabled: bool = Field(default=False, description="是否启用硬件门户会话校验")
    internal_url: str = Field(default="", description="htmlsystm 容器内 URL")
    internal_secret: str = Field(default="", description="内部会话校验密钥")
    login_url: str = Field(default="/neo_hardware/login", description="未登录页面跳转地址")
    public_prefix: str = Field(
        default="/neo_hardware/report-audit",
        description="报告审核平台的外部路径前缀",
    )
    timeout_seconds: float = Field(default=10.0, description="会话校验超时")
    cache_ttl_seconds: float = Field(default=10.0, description="成功校验缓存时间")

    class Config:
        env_prefix = "HARDWARE_AUTH_"


class CqcLibrarySettings(BaseSettings):
    """认证库独立授权；仅使用门户数据库用户 ID，不继承门户角色。"""
    admin_user_ids: str = Field(default="", description="逗号分隔的门户用户 ID；空值拒绝所有管理访问")

    @field_validator("admin_user_ids")
    @classmethod
    def validate_admin_user_ids(cls, value):
        if not value.strip():
            return ""
        ids = [item.strip() for item in value.split(",")]
        if any(not item.isascii() or not item.isdecimal() or int(item) <= 0 for item in ids):
            raise ValueError("CQC_LIBRARY_ADMIN_USER_IDS 必须为逗号分隔的正整数用户 ID")
        return ",".join(sorted({str(int(item)) for item in ids}, key=int))

    class Config:
        env_prefix = "CQC_LIBRARY_"


class ValidationRulesSettings(BaseSettings):
    """验证规则配置"""
    # 标准测试项目清单（为空时跳过完整性检查）
    standard_test_items: Dict[str, list] = Field(
        default_factory=lambda: {},
        description="标准测试项目清单"
    )

    # 是否启用测试项完整性检查
    enable_completeness_check: bool = Field(
        default=False,
        description="是否启用测试项完整性检查"
    )

    # 关键信息字段（用于一致性校验）
    # 注意：测试标准/测试日期 不同测试项有不同的值是正常的，不应视为不一致
    key_info_fields: list = Field(
        default_factory=lambda: ["产品型号", "版本号", "项目编号", "测试人"],
        description="关键信息字段列表（应全文档一致的字段）"
    )

    # 日期格式
    date_formats: list = Field(
        default_factory=lambda: ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"],
        description="支持的日期格式"
    )

    # 测试值判定规则
    test_value_rules: Dict[str, Dict] = Field(
        default_factory=lambda: {
            "电压": {"min": 0, "max": 50, "unit": "V"},
            "电流": {"min": 0, "max": 10, "unit": "A"},
            "功耗": {"min": 0, "max": 100, "unit": "W"},
            "温度": {"min": -40, "max": 85, "unit": "℃"},
            "频率": {"min": 0, "max": 10000, "unit": "MHz"}
        },
        description="测试值判定规则"
    )


class AnalysisSettings(BaseSettings):
    """分析模块配置"""
    # 统计指标
    statistics: list = Field(
        default_factory=lambda: ["max", "min", "mean", "std", "median"],
        description="统计指标列表"
    )

    # 图表类型
    chart_types: list = Field(
        default_factory=lambda: ["histogram", "scatter", "line", "box"],
        description="支持的图表类型"
    )

    # 聚合字段
    aggregation_fields: list = Field(
        default_factory=lambda: ["测试项目", "测试项", "测试小项", "测试项名称"],
        description="用于数据聚合的字段名"
    )

    # 数值字段
    value_fields: list = Field(
        default_factory=lambda: ["测试值", "实测值", "数值", "结果值", "测量值"],
        description="用于统计的数值字段名"
    )


class Settings(BaseSettings):
    """全局配置"""
    server: ServerSettings = Field(default_factory=ServerSettings)
    ai_service: AIServiceSettings = Field(default_factory=AIServiceSettings)
    file_storage: FileStorageSettings = Field(default_factory=FileStorageSettings)
    object_storage: ObjectStorageSettings = Field(default_factory=ObjectStorageSettings)
    hardware_auth: HardwareAuthSettings = Field(default_factory=HardwareAuthSettings)
    cqc_library: CqcLibrarySettings = Field(default_factory=CqcLibrarySettings)
    validation_rules: ValidationRulesSettings = Field(default_factory=ValidationRulesSettings)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


# 全局配置实例
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """获取配置单例"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def load_config_from_yaml(config_path: str) -> Dict[str, Any]:
    """从 YAML 文件加载配置"""
    config_file = Path(config_path)
    if not config_file.exists():
        # 返回默认配置
        return {}

    with open(config_file, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def init_directories():
    """初始化目录结构"""
    settings = get_settings()

    directories = [
        settings.file_storage.upload_dir,
        settings.file_storage.temp_dir,
        settings.file_storage.archive_dir,
    ]

    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)
