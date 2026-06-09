# -*- coding: utf-8 -*-
"""
硬件研发部测试报告审核平台 - 日志模块
统一日志管理，支持文件和控制台输出
"""

import logging
import sys
from pathlib import Path
from typing import Optional


class Logger:
    """日志管理器"""

    _instances: dict = {}

    def __init__(self, name: str, log_dir: str = "./logs"):
        self.name = name
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG)

        # 避免重复添加 handler
        if self.logger.handlers:
            return

        # 控制台 Handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)

        # 文件 Handler
        file_handler = logging.FileHandler(
            self.log_dir / f"{name}.log",
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        self.logger.addHandler(file_handler)

    @classmethod
    def get_logger(cls, name: str, log_dir: str = "./logs") -> logging.Logger:
        """获取日志实例"""
        if name not in cls._instances:
            cls._instances[name] = Logger(name, log_dir)
        return cls._instances[name].logger


# 导出便捷函数
def get_logger(name: str) -> logging.Logger:
    """获取日志实例的便捷函数"""
    return Logger.get_logger(name)
