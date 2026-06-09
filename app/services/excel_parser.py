# -*- coding: utf-8 -*-
"""
硬件研发部测试报告审核平台 - Excel 解析服务
支持多 Sheet Excel 文件解析，提供统一的数据访问接口
"""

import io
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import openpyxl
import pandas as pd
from openpyxl.cell.cell import Cell
from openpyxl.worksheet.worksheet import Worksheet

from app.core import get_logger, ExcelParseException

logger = get_logger("excel_parser")


class ExcelParser:
    """Excel 文件解析器

    提供统一的接口解析 Excel 文件，支持：
    - 多 Sheet 读取
    - 数据提取
    - 文本内容扫描
    - 关键信息提取
    """

    def __init__(self, file_path: Optional[str] = None):
        self.file_path = file_path
        self.workbook: Optional[openpyxl.Workbook] = None
        self.sheets_data: Dict[str, pd.DataFrame] = {}
        self.raw_sheets: Dict[str, Worksheet] = {}

    def load_from_file(self, file_path: str) -> "ExcelParser":
        """从文件加载 Excel"""
        try:
            self.file_path = file_path
            self.workbook = openpyxl.load_workbook(file_path, data_only=True)
            logger.info(f"成功加载 Excel 文件: {file_path}, Sheet 数量: {len(self.workbook.sheetnames)}")
            return self
        except Exception as e:
            logger.error(f"加载 Excel 文件失败: {str(e)}")
            raise ExcelParseException(f"无法读取 Excel 文件: {str(e)}")

    def load_from_bytes(self, content: bytes, filename: str = "uploaded.xlsx") -> "ExcelParser":
        """从字节流加载 Excel"""
        try:
            self.workbook = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
            logger.info(f"成功从字节流加载 Excel: {filename}, Sheet 数量: {len(self.workbook.sheetnames)}")
            return self
        except Exception as e:
            logger.error(f"从字节流加载 Excel 失败: {str(e)}")
            raise ExcelParseException(f"无法读取 Excel 内容: {str(e)}")

    def get_sheet_names(self) -> List[str]:
        """获取所有 Sheet 名称"""
        if not self.workbook:
            return []
        return self.workbook.sheetnames

    def read_sheet(self, sheet_name: str, use_pandas: bool = True) -> Union[pd.DataFrame, Worksheet]:
        """读取指定 Sheet

        Args:
            sheet_name: Sheet 名称
            use_pandas: 是否使用 pandas DataFrame 格式返回

        Returns:
            DataFrame 或 Worksheet 对象
        """
        if not self.workbook:
            raise ExcelParseException("未加载 Excel 文件")

        if sheet_name not in self.workbook.sheetnames:
            raise ExcelParseException(f"Sheet 不存在: {sheet_name}", sheet_name=sheet_name)

        if use_pandas:
            if sheet_name not in self.sheets_data:
                # Use pandas with the worksheet values directly
                worksheet = self.workbook[sheet_name]
                data = list(worksheet.values)
                # Get the dimensions
                rows = worksheet.max_row
                cols = worksheet.max_column
                # Create DataFrame from values, first row as header
                if rows > 0:
                    self.sheets_data[sheet_name] = pd.DataFrame(
                        data[1:] if len(data) > 1 else [],
                        columns=data[0] if len(data) > 0 else None
                    )
                else:
                    self.sheets_data[sheet_name] = pd.DataFrame()
            return self.sheets_data[sheet_name]
        else:
            if sheet_name not in self.raw_sheets:
                self.raw_sheets[sheet_name] = self.workbook[sheet_name]
            return self.raw_sheets[sheet_name]

    def read_all_sheets(self) -> Dict[str, pd.DataFrame]:
        """读取所有 Sheet"""
        if not self.workbook:
            raise ExcelParseException("未加载 Excel 文件")

        for sheet_name in self.workbook.sheetnames:
            if sheet_name not in self.sheets_data:
                self.read_sheet(sheet_name)

        return self.sheets_data

    def extract_text_content(self, max_cells: int = 5000) -> Dict[str, List[Dict[str, Any]]]:
        """提取所有 Sheet 的文本内容

        用于 AI 错别字检测

        Args:
            max_cells: 最大提取单元格数，防止大文件超时

        Returns:
            Dict[sheet_name, List[cell_info]]
        """
        text_content = {}

        for sheet_name in self.get_sheet_names():
            sheet = self.read_sheet(sheet_name, use_pandas=False)
            cells = []
            cell_count = 0

            for row in sheet.iter_rows():
                for cell in row:
                    if cell.value and isinstance(cell.value, str) and cell.value.strip():
                        cells.append({
                            "row": cell.row,
                            "col": cell.column,
                            "value": cell.value,
                            "coordinate": cell.coordinate
                        })
                        cell_count += 1
                        if cell_count >= max_cells:
                            break
                if cell_count >= max_cells:
                    break

            text_content[sheet_name] = cells
            logger.info(f"Sheet '{sheet_name}' 提取文本单元格: {len(cells)}")

        return text_content

    def extract_key_info(self) -> Dict[str, Any]:
        """提取关键信息

        提取产品型号、版本号、项目编号等关键字段
        """
        key_info = {
            "产品型号": [],
            "版本号": [],
            "项目编号": [],
            "测试标准": [],
            "测试日期": [],
            "测试人": []
        }

        for sheet_name in self.get_sheet_names():
            df = self.read_sheet(sheet_name)

            # 遍历前 20 行，查找关键字段
            for idx, row in df.head(20).iterrows():
                for col_idx, value in enumerate(row):
                    if pd.isna(value):
                        continue

                    value_str = str(value).strip()

                    # 匹配关键字段
                    for key in key_info.keys():
                        if key in value_str:
                            # 尝试获取同一行的下一列作为值
                            if col_idx + 1 < len(row):
                                next_value = row.iloc[col_idx + 1]
                                if pd.notna(next_value):
                                    key_info[key].append({
                                        "sheet": sheet_name,
                                        "row": idx + 1,
                                        "value": str(next_value)
                                    })

        # 去除重复
        for key in key_info:
            seen = set()
            unique_info = []
            for item in key_info[key]:
                if item["value"] not in seen:
                    seen.add(item["value"])
                    unique_info.append(item)
            key_info[key] = unique_info

        return key_info

    def extract_test_items(self) -> Dict[str, List[Dict[str, Any]]]:
        """提取测试项目

        识别测试项、测试值、判定结果等
        """
        test_items = {}

        for sheet_name in self.get_sheet_names():
            df = self.read_sheet(sheet_name)

            items = []

            # 查找可能的测试项列
            for idx, row in df.iterrows():
                row_data = {}
                has_data = False

                for col_idx, value in enumerate(row):
                    if pd.notna(value):
                        row_data[f"col_{col_idx}"] = value
                        has_data = True

                if has_data and len(row_data) > 0:
                    items.append({
                        "row": idx + 1,
                        "data": row_data
                    })

            test_items[sheet_name] = items

        return test_items

    def extract_dates(self) -> List[Dict[str, Any]]:
        """提取所有日期

        Returns:
            日期信息列表
        """
        from datetime import datetime
        import re

        dates = []
        date_patterns = [
            r'\d{4}-\d{1,2}-\d{1,2}',
            r'\d{4}/\d{1,2}/\d{1,2}',
            r'\d{4}\.\d{1,2}\.\d{1,2}',
            r'\d{4}年\d{1,2}月\d{1,2}日'
        ]

        for sheet_name in self.get_sheet_names():
            df = self.read_sheet(sheet_name)

            for idx, row in df.iterrows():
                for col_idx, value in enumerate(row):
                    if pd.isna(value):
                        continue

                    # 检查是否为日期
                    if isinstance(value, datetime):
                        dates.append({
                            "sheet": sheet_name,
                            "row": idx + 1,
                            "col": col_idx + 1,
                            "value": value.strftime("%Y-%m-%d"),
                            "raw_value": str(value)
                        })
                    elif isinstance(value, str):
                        # 尝试匹配日期格式
                        for pattern in date_patterns:
                            if re.match(pattern, value.strip()):
                                dates.append({
                                    "sheet": sheet_name,
                                    "row": idx + 1,
                                    "col": col_idx + 1,
                                    "value": value.strip(),
                                    "raw_value": value.strip()
                                })
                                break

        return dates

    def extract_numeric_data(self) -> Dict[str, List[Dict[str, Any]]]:
        """提取数值型数据

        用于测试值校验和数据分析
        """
        numeric_data = {}

        for sheet_name in self.get_sheet_names():
            df = self.read_sheet(sheet_name)

            # 尝试将所有列转为数值
            numeric_rows = []

            for idx, row in df.iterrows():
                row_dict = row.to_dict()
                # 过滤掉全为 NaN 的行
                if any(pd.notna(v) for v in row_dict.values()):
                    numeric_rows.append({
                        "row": idx + 1,
                        "data": row_dict
                    })

            numeric_data[sheet_name] = numeric_rows

        return numeric_data

    def get_sheet_summary(self) -> Dict[str, Any]:
        """获取 Sheet 摘要信息"""
        summary = {
            "total_sheets": len(self.workbook.sheetnames),
            "sheets": []
        }

        for sheet_name in self.get_sheet_names():
            df = self.read_sheet(sheet_name)
            summary["sheets"].append({
                "name": sheet_name,
                "rows": len(df),
                "columns": len(df.columns)
            })

        return summary

    def close(self):
        """关闭工作簿"""
        if self.workbook:
            self.workbook.close()
            self.workbook = None
