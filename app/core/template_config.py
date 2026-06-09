# -*- coding: utf-8 -*-
"""
硬件研发部测试报告审核平台 - 标准模板配置
定义标准报告格式的列结构，提高识别准确率
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field


@dataclass
class ColumnMapping:
    """列映射配置"""
    min_col: Optional[int] = None
    typ_col: Optional[int] = None
    max_col: Optional[int] = None
    data_col: Optional[int] = None
    result_col: Optional[int] = None
    item_col: Optional[int] = None
    criteria_style: str = "standard"
    criteria_col: Optional[int] = None


@dataclass
class SheetConfig:
    """Sheet配置"""
    name_patterns: List[str] = field(default_factory=list)
    column_mapping: ColumnMapping = field(default_factory=ColumnMapping)
    header_row: int = 10
    data_start_row: int = 11
    validation_rules: Dict[str, Any] = field(default_factory=dict)


class StandardTemplateConfig:
    """标准模板配置管理器"""

    SHEET_CONFIGS = {
        "power_stability": SheetConfig(
            name_patterns=["1.1.2", "power stability", "power_stability"],
            column_mapping=ColumnMapping(
                min_col=5,
                typ_col=6,
                max_col=7,
                data_col=8,
                result_col=11,
                item_col=1,
                criteria_style="standard"
            ),
            header_row=10,
            data_start_row=11,
            validation_rules={
                "ripple": {
                    "min_col": 9,
                    "max_col": 9,
                    "data_col": 10
                }
            }
        ),

        "current_test": SheetConfig(
            name_patterns=["1.1.4", "current", "current test"],
            column_mapping=ColumnMapping(
                min_col=None,
                typ_col=None,
                max_col=None,
                data_col=5,
                result_col=6,
                item_col=1,
                criteria_style="current_ilmt",
                criteria_col=4
            ),
            header_row=11,
            data_start_row=12,
            validation_rules={
                "ilmt_column": 4,
                "data_column": 5,
                "rule": "Imax < ILmt-top"
            }
        ),

        "power_consumption": SheetConfig(
            name_patterns=["1.1.5", "power consumption", "power test", "power"],
            column_mapping=ColumnMapping(
                min_col=None,
                typ_col=None,
                max_col=None,
                data_col=7,
                result_col=9,
                item_col=2,
                criteria_style="power",
                criteria_col=6
            ),
            header_row=7,
            data_start_row=8,
            validation_rules={
                "criteria_column": 6,
                "data_column": 7,
                "unit_column": 8,
                "expected_column": 4
            }
        ),

        "i2s_timing": SheetConfig(
            name_patterns=["1.2.4", "i2s", "timing"],
            column_mapping=ColumnMapping(
                min_col=4,
                typ_col=5,
                max_col=6,
                data_col=8,
                result_col=9,
                item_col=1,
                criteria_style="timing"
            ),
            header_row=10,
            data_start_row=11,
            validation_rules={
                "min_col": 4,
                "typ_col": 5,
                "max_col": 6,
                "data_col": 8
            }
        ),

        "clock_signal": SheetConfig(
            name_patterns=["1.2.2", "clock signal", "frequency deviation"],
            column_mapping=ColumnMapping(
                min_col=7,
                typ_col=None,
                max_col=8,
                data_col=6,
                result_col=9,
                item_col=1,
                criteria_style="deviation"
            ),
            header_row=10,
            data_start_row=11,
            validation_rules={
                "frequency_value_col": 4,
                "test_frequency_col": 5,
                "deviation_value_col": 6,
                "min_requirement_col": 7,
                "max_requirement_col": 8,
                "result_col": 9
            }
        ),

        "timing_test": SheetConfig(
            name_patterns=["timing", "time"],
            column_mapping=ColumnMapping(
                min_col=None,
                typ_col=None,
                max_col=None,
                data_col=None,
                result_col=8,
                item_col=1,
                criteria_style="standard"
            ),
            header_row=8,
            data_start_row=9
        ),

        "default": SheetConfig(
            name_patterns=["*"],
            column_mapping=ColumnMapping(
                min_col=5,
                typ_col=6,
                max_col=7,
                data_col=8,
                result_col=11,
                item_col=1,
                criteria_style="standard"
            ),
            header_row=9,
            data_start_row=10
        )
    }

    @classmethod
    def get_config_for_sheet(cls, sheet_name: str) -> SheetConfig:
        """根据Sheet名称获取配置"""
        sheet_name_lower = sheet_name.lower()

        for config_name, config in cls.SHEET_CONFIGS.items():
            if config_name == "default":
                continue

            for pattern in config.name_patterns:
                if pattern.lower() in sheet_name_lower:
                    return config

        return cls.SHEET_CONFIGS["default"]

    @classmethod
    def get_all_configs(cls) -> Dict[str, SheetConfig]:
        """获取所有配置"""
        return cls.SHEET_CONFIGS

    @classmethod
    def get_standard_columns(cls) -> Dict[str, List[int]]:
        """获取标准列位置映射"""
        return {
            "A": 1,
            "B": 2,
            "C": 3,
            "D": 4,
            "E": 5,
            "F": 6,
            "G": 7,
            "H": 8,
            "I": 9,
            "J": 10,
            "K": 11,
            "L": 12,
            "M": 13,
            "N": 14,
            "O": 15,
        }


# 全局配置实例
standard_config = StandardTemplateConfig()
