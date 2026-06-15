# -*- coding: utf-8 -*-
"""
硬件研发部测试报告审核平台 - 验证服务（直接读取版）
完全适配用户真实硬件测试报告格式

【核心修复】:
使用 openpyxl 直接读取 Excel，精确匹配列位置
而非依赖 pandas DataFrame 的自动列头检测

【已知列位置（1.1.2_Power stability Sheet）】
- E列(5) = criteria(min) - 最小标准
- F列(6) = criteria(typ) - 典型标准
- G列(7) = criteria(max) - 最大标准
- H列(8) = test data - 实测值
- K列(11) = Test Result - 判定结果
"""

import re
import io
import calendar
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union
from dateutil import parser as date_parser
from concurrent.futures import ThreadPoolExecutor, as_completed
import openpyxl
from openpyxl.worksheet.worksheet import Worksheet
import pandas as pd

from app.core import get_logger, get_settings, standard_config

logger = get_logger("validation_service")


class ValidationService:
    """混合验证服务"""

    def __init__(self):
        self.config = get_settings()
        self.rules = self.config.validation_rules

    # ==================== 项目基础信息提取 ====================

    def extract_project_info(self, sheets_data: Dict[str, Any]) -> Dict[str, Any]:
        """提取项目基础信息

        支持格式：
        1. Report Front Page Sheet：字段名在列B，值在列D
        2. Project Information Sheet：键值对格式
        3. 处理包含换行符的双语列名
        """
        project_info = {}

        for sheet_name, df in sheets_data.items():
            if df is None or df.empty:
                continue

            # 方法1：Report Front Page格式
            if "Report Front Page" in sheet_name or "Front Page" in sheet_name:
                result = self._extract_from_front_page(df)
                for k, v in result.items():
                    if k not in project_info or not project_info[k]:
                        project_info[k] = v

            # 方法2：Project Information格式
            if "Project Information" in sheet_name or "项目信息" in sheet_name:
                result = self._extract_from_project_info(df)
                for k, v in result.items():
                    if k not in project_info or not project_info[k]:
                        project_info[k] = v

        logger.info(f"项目基础信息提取完成: {sum(1 for v in project_info.values() if v)} 个字段有值")
        return project_info

    def _extract_from_front_page(self, df) -> Dict[str, str]:
        """从Report Front Page提取信息 - 增强版"""
        result = {}

        # 字段映射：每个字段可能在不同的列找到
        # 格式: (关键词列表, [值可能所在的列索引])
        field_rules = [
            ("产品型号", ["model no", "产品型号", "model"], [1, 3]),   # 列索引(0开始)
            ("项目编号", ["project no", "项目编号", "project"], [1, 3]),
            ("用例编号", ["test case no", "用例编号", "report no", "case"], [1, 3]),
            ("用例版本", ["test case ver", "用例版本", "ver.", "version"], [1, 3]),
            ("设计师", ["designer", "设计师", "engineer name"], [1, 3]),
            
            # 日期字段 - 在列6或7找到日期值
            ("测试日期", ["测试日期", "test date", "test-date"], [5, 7, 6, 8]),
            ("评审日期", ["评审日期", "review date", "review-date"], [5, 7, 6, 8]),
            ("签发日期", ["签发日期", "issue date", "issue-date"], [5, 7, 6, 8]),
            
            # 测试结论 - 在列6找到标签，列8是值
            ("测试结论", ["测试结论", "test conclusion", "overall result", "conclusion"], [5, 7, 6, 8]),
        ]

        # 首先，遍历每一行，收集所有单元格值
        for idx, row in df.iterrows():
            # 把整行的所有单元格合并成文本（用于关键词匹配）
            all_cells = []
            cell_values = {}
            for col_idx in range(len(row)):
                val = row.iloc[col_idx]
                cell_str = str(val).strip() if val is not None else ""
                cell_values[col_idx] = cell_str
                if cell_str:
                    all_cells.append(cell_str)
            
            combined_text = " ".join(all_cells).lower()

            # 检查每个字段规则
            for field_name, keywords, value_cols in field_rules:
                if field_name in result:
                    continue  # 已经找到这个字段了
                
                # 检查是否有关键词匹配
                keyword_found = False
                matched_keyword = None
                for keyword in keywords:
                    if keyword.lower() in combined_text:
                        keyword_found = True
                        matched_keyword = keyword.lower()
                        break
                
                if keyword_found:
                    # 尝试从指定的列中找值
                    for value_col in value_cols:
                        if value_col in cell_values and cell_values[value_col]:
                            cell_val = cell_values[value_col]
                            # 跳过 nan/None
                            if cell_val is None or (isinstance(cell_val, float) and pd.isna(cell_val)) or str(cell_val).lower() == "nan":
                                continue
                            # 确保值不是标签本身（避免把标签当值）
                            val_str = str(cell_val).strip()
                            if matched_keyword not in val_str.lower():
                                # 格式化日期（只保留年月日）
                                if "日期" in field_name:
                                    val_str = self._format_date(val_str)
                                result[field_name] = val_str
                                logger.info(f"  [Front Page] 找到 {field_name}: {result[field_name]}")
                                break

        return result

    def _extract_from_project_info(self, df) -> Dict[str, str]:
        """从Project Information Sheet提取信息 - 增强版"""
        result = {}

        field_rules = [
            ("产品型号", ["model no", "产品型号", "model"], [0, 1]),
            ("项目编号", ["project no", "项目编号", "project"], [0, 1]),
            ("用例编号", ["test case no", "用例编号", "report no", "case"], [0, 1]),
            ("用例版本", ["test case ver", "用例版本", "ver.", "version"], [0, 1]),
            ("设计师", ["designer", "设计师"], [0, 1]),
            ("测试结论", ["test conclusion", "测试结论", "overall result", "conclusion"], [0, 1]),
            ("测试日期", ["test date", "测试日期", "test-date"], [0, 1]),
            ("评审日期", ["review date", "评审日期", "review-date"], [0, 1]),
            ("签发日期", ["issue date", "签发日期", "issue-date"], [0, 1]),
        ]

        for idx, row in df.iterrows():
            # 收集整行信息
            all_cells = []
            cell_values = {}
            for col_idx in range(len(row)):
                val = row.iloc[col_idx]
                cell_str = str(val).strip() if val is not None else ""
                cell_values[col_idx] = cell_str
                if cell_str:
                    all_cells.append(cell_str)
            
            combined_text = " ".join(all_cells).lower()

            # 检查字段
            for field_name, keywords, value_cols in field_rules:
                if field_name in result:
                    continue
                
                keyword_found = False
                matched_keyword = None
                for keyword in keywords:
                    if keyword.lower() in combined_text:
                        keyword_found = True
                        matched_keyword = keyword.lower()
                        break
                
                if keyword_found:
                    # 尝试找到值
                    for value_col in value_cols:
                        if value_col in cell_values and cell_values[value_col]:
                            cell_val = cell_values[value_col]
                            # 跳过 nan/None
                            if cell_val is None or (isinstance(cell_val, float) and pd.isna(cell_val)) or str(cell_val).lower() == "nan":
                                continue
                            val_str = str(cell_val).strip()
                            if matched_keyword not in val_str.lower():
                                # 格式化日期（只保留年月日）
                                if "日期" in field_name:
                                    val_str = self._format_date(val_str)
                                result[field_name] = val_str
                                logger.info(f"  [Project Info] 找到 {field_name}: {result[field_name]}")
                                break

        return result

    def _format_date(self, date_str: str) -> str:
        """格式化日期，只保留年月日
        
        支持格式：
        - 2026-01-05 00:00:00 -> 2026-01-05
        - 1/5/2026 -> 2026-01-05
        - 2026/01/05 -> 2026-01-05
        """
        if not date_str or not isinstance(date_str, str):
            return date_str
        
        import re
        from datetime import datetime
        
        date_str = date_str.strip()
        
        # 尝试直接用 dateutil 解析
        try:
            from dateutil import parser
            dt = parser.parse(date_str, fuzzy=True)
            return dt.strftime("%Y-%m-%d")
        except:
            pass
        
        # 尝试常见的分隔符格式
        separators = ['-', '/', '年', '/']
        for sep in separators:
            if sep in date_str:
                # 尝试用 pandas 解析
                try:
                    import pandas as pd
                    dt = pd.to_datetime(date_str, errors='coerce')
                    if not pd.isna(dt):
                        return dt.strftime("%Y-%m-%d")
                except:
                    pass
                
                # 简单的正则提取
                parts = re.split(r'[-/ ]', date_str)
                if len(parts) >= 3:
                    # 尝试提取年、月、日
                    try:
                        # 找到看起来像年份的部分（四位数）
                        year = None
                        month = None
                        day = None
                        
                        for part in parts:
                            part = part.strip()
                            if not part:
                                continue
                            if len(part) == 4 and part.isdigit():
                                year = int(part)
                            elif len(part) <= 2 and part.isdigit() and month is None:
                                month = int(part)
                            elif len(part) <= 2 and part.isdigit() and day is None:
                                day = int(part)
                        
                        # 如果找到了年月日，尝试格式化
                        if year is not None and month is not None and day is not None:
                            return f"{year}-{month:02d}-{day:02d}"
                    except:
                        pass
        
        # 如果所有尝试都失败，返回原始值（但去除时间部分）
        if ' ' in date_str:
            return date_str.split(' ')[0]
        
        return date_str

    # ==================== 日期校验 ====================

    def validate_dates(self, dates: List[Dict[str, Any]]) -> Dict[str, Any]:
        """验证日期合法性"""
        invalid_dates = []

        for date_info in dates:
            value = date_info.get("value", "")
            is_valid, error_msg = self._validate_single_date(value)

            if not is_valid:
                invalid_dates.append({
                    "sheet": date_info.get("sheet", ""),
                    "row": date_info.get("row", 0),
                    "col": date_info.get("col", 0),
                    "value": value,
                    "error": error_msg
                })

        return {
            "total": len(dates),
            "valid": len(dates) - len(invalid_dates),
            "invalid": invalid_dates,
            "is_valid": len(invalid_dates) == 0
        }

    def _validate_single_date(self, date_str: str) -> Tuple[bool, str]:
        """验证单个日期"""
        if not date_str:
            return False, "日期为空"

        try:
            dt = date_parser.parse(date_str, fuzzy=True)

            if dt.year < 1900 or dt.year > 2100:
                return False, f"年份 {dt.year} 不在合理范围内 (1900-2100)"

            if dt.month < 1 or dt.month > 12:
                return False, f"月份 {dt.month} 无效"

            if dt.day < 1:
                return False, "日期必须大于0"

            last_day = calendar.monthrange(dt.year, dt.month)[1]
            if dt.day > last_day:
                return False, f"{dt.month}月最多{last_day}天，实际输入{dt.day}日"

            return True, ""

        except Exception as e:
            return False, f"无法解析日期: {str(e)}"

    # ==================== 关键信息一致性 ====================

    def validate_key_info_consistency(
        self,
        key_info: Dict[str, List[Dict[str, Any]]]
    ) -> Dict[str, Any]:
        """验证关键信息一致性"""
        inconsistencies = []
        key_fields = self.rules.key_info_fields

        for field in key_fields:
            if field not in key_info:
                continue

            field_values = key_info[field]
            if not field_values:
                continue

            unique_values = {}
            for item in field_values:
                value = item.get("value", "").strip()
                if value:
                    location = f"{item.get('sheet', '')}: 行{item.get('row', 0)}"
                    if value not in unique_values:
                        unique_values[value] = []
                    unique_values[value].append(location)

            if len(unique_values) > 1:
                values_list = list(unique_values.keys())
                inconsistencies.append({
                    "field": field,
                    "values": values_list,
                    "locations": [unique_values[v] for v in values_list],
                    "issue": f"存在 {len(unique_values)} 个不同的值"
                })

        return {
            "is_consistent": len(inconsistencies) == 0,
            "inconsistencies": inconsistencies
        }

    def validate_company_info(
        self,
        sheets_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """验证公司信息一致性

        只在封面页和项目信息页检查公司信息：
        - CORP.NAME: SICHUAN CHANGHONG NEONET TECHNOLOGIES CO., LTD
        - 公司名称：四川长虹新网科技有限责任公司
        - ADD: 35,East Mianxing Road,HighTech Park,Mianyang,Sichuan,China
        - 地址: 四川省绵阳市高新区绵兴东路35号
        - TEL： 0816-2410754

        Returns:
            公司信息一致性检查结果
        """
        STANDARD_COMPANY_INFO = {
            "corp_name_en": "SICHUAN CHANGHONG NEONET TECHNOLOGIES CO., LTD",
            "corp_name_cn": "四川长虹新网科技有限责任公司",
            "address_en": "35,East Mianxing Road,HighTech Park,Mianyang,Sichuan,China",
            "address_cn": "四川省绵阳市高新区绵兴东路35号",
            "tel": "0816-2410754"
        }

        # 只在封面页和项目信息页检查
        FRONT_PAGE_KEYWORDS = ["front page", "封面", "project information", "project info"]
        TEST_SHEET_PATTERNS = ["1.1", "1.2", "2.1", "2.2", "3.1", "4.1", "4.2", "test"]

        company_info_found = {}
        issues = []

        def is_front_page_sheet(sheet_name):
            """判断是否为封面页或项目信息页"""
            sheet_lower = sheet_name.lower()
            for keyword in FRONT_PAGE_KEYWORDS:
                if keyword in sheet_lower:
                    return True
            return False

        def is_test_sheet(sheet_name):
            """判断是否为测试数据页"""
            sheet_lower = sheet_name.lower()
            for pattern in TEST_SHEET_PATTERNS:
                if pattern in sheet_lower:
                    return True
            return False

        for sheet_name, df in sheets_data.items():
            if df is None or df.empty:
                continue

            # 只检查封面页和项目信息页，跳过测试数据页
            if not is_front_page_sheet(sheet_name) and is_test_sheet(sheet_name):
                continue

            logger.info(f"  检查Sheet '{sheet_name}' 的公司信息...")

            for idx, row in df.iterrows():
                for col_idx, value in enumerate(row):
                    if pd.isna(value):
                        continue

                    value_str = str(value).strip()
                    value_lower = value_str.lower()

                    # 公司名称检查 - 更精确的匹配
                    if value_str == "CORP.NAME" or value_str == "公司名称":
                        if col_idx + 1 < len(row):
                            next_value = row.iloc[col_idx + 1]
                            if pd.notna(next_value):
                                next_val = str(next_value).strip()
                                company_info_found.setdefault("corp_name", []).append({
                                    "sheet": sheet_name,
                                    "row": idx + 1,
                                    "col": col_idx + 2,
                                    "value": next_val,
                                    "expected": STANDARD_COMPANY_INFO["corp_name_en"] + " / " + STANDARD_COMPANY_INFO["corp_name_cn"]
                                })
                                logger.info(f"    找到公司名称: {next_val}")

                    # 地址检查 - 更精确的匹配
                    if value_str == "ADD" or value_str == "地址":
                        if col_idx + 1 < len(row):
                            next_value = row.iloc[col_idx + 1]
                            if pd.notna(next_value):
                                next_val = str(next_value).strip()
                                company_info_found.setdefault("address", []).append({
                                    "sheet": sheet_name,
                                    "row": idx + 1,
                                    "col": col_idx + 2,
                                    "value": next_val,
                                    "expected": STANDARD_COMPANY_INFO["address_en"] + " / " + STANDARD_COMPANY_INFO["address_cn"]
                                })
                                logger.info(f"    找到地址: {next_val}")

                    # 电话检查 - 更精确的匹配，只匹配 "TEL" 或 "电话"
                    if value_str == "TEL" or value_str == "电话":
                        if col_idx + 1 < len(row):
                            next_value = row.iloc[col_idx + 1]
                            if pd.notna(next_value):
                                next_val = str(next_value).strip()
                                company_info_found.setdefault("tel", []).append({
                                    "sheet": sheet_name,
                                    "row": idx + 1,
                                    "col": col_idx + 2,
                                    "value": next_val,
                                    "expected": STANDARD_COMPANY_INFO["tel"]
                                })
                                logger.info(f"    找到电话: {next_val}")

        for info_type, found_items in company_info_found.items():
            if not found_items:
                issues.append({
                    "type": "缺失",
                    "field": info_type,
                    "message": f"未找到{info_type}信息"
                })
                continue

            expected = STANDARD_COMPANY_INFO.get(info_type, STANDARD_COMPANY_INFO.get(info_type + "_en", ""))

            for item in found_items:
                value = item["value"]
                is_correct = False

                if info_type == "corp_name":
                    is_correct = (
                        value == STANDARD_COMPANY_INFO["corp_name_en"] or
                        value == STANDARD_COMPANY_INFO["corp_name_cn"]
                    )
                elif info_type == "address":
                    is_correct = (
                        value == STANDARD_COMPANY_INFO["address_en"] or
                        value == STANDARD_COMPANY_INFO["address_cn"]
                    )
                elif info_type == "tel":
                    is_correct = value == STANDARD_COMPANY_INFO["tel"]

                if not is_correct:
                    issues.append({
                        "type": "不一致",
                        "field": info_type,
                        "sheet": item["sheet"],
                        "row": item["row"],
                        "col": item["col"],
                        "actual": value,
                        "expected": item["expected"],
                        "message": f"{info_type}信息不一致：实际值'{value}'，期望值'{item['expected']}'"
                    })

        logger.info(f"公司信息一致性检查完成: 发现 {len(issues)} 个问题")

        return {
            "is_valid": len(issues) == 0,
            "company_info_found": company_info_found,
            "issues": issues,
            "standard_info": STANDARD_COMPANY_INFO,
            "summary": f"检查了 {len(company_info_found)} 类公司信息，发现 {len(issues)} 个问题"
        }

    # ==================== 测试值逻辑校验（直接读取版）====================

    def validate_test_value_logic(
        self,
        sheets_data: Dict[str, Any],
        workbook_path: Optional[str] = None,
        workbook_bytes: Optional[bytes] = None
    ) -> Dict[str, Any]:
        """【直接读取版】验证测试值与判定结果的逻辑一致性

        优先使用 openpyxl 直接读取 Excel，精确匹配列位置
        备用方案：使用 pandas DataFrame（但可能检测不完整）

        Args:
            sheets_data: Sheet数据字典
            workbook_path: Excel文件路径（可选）
            workbook_bytes: Excel文件字节流（可选）
        """
        issues = []
        processed_count = 0

        # 跳过的Sheet（无测试数据）
        skip_sheets = [
            "Report Front Page", "Project Information", "Test Summary Table",
            "Summary of Test Equipment", "Revision of Test Cases"
        ]

        # 优先使用 openpyxl 直接读取
        if workbook_path or workbook_bytes:
            issues, processed_count = self._validate_with_openpyxl(
                workbook_path, workbook_bytes, skip_sheets
            )
        else:
            # 备用：使用 pandas DataFrame
            logger.warning("未提供 Excel 文件路径，使用 pandas DataFrame 模式（可能检测不完整）")
            issues, processed_count = self._validate_with_dataframe(sheets_data, skip_sheets)

        logger.info(f"测试值逻辑校验完成: 处理 {processed_count} 条数据，发现 {len(issues)} 个问题")

        return {
            "is_valid": len(issues) == 0,
            "issues": issues,
            "summary": {
                "processed_count": processed_count,
                "issue_count": len(issues),
                "detection_mode": "openpyxl" if (workbook_path or workbook_bytes) else "dataframe"
            }
        }

    def _validate_with_openpyxl(
        self,
        workbook_path: Optional[str],
        workbook_bytes: Optional[bytes],
        skip_sheets: List[str]
    ) -> Tuple[List[Dict[str, Any]], int]:
        """使用 openpyxl 直接读取并校验【并行增强版】

        支持：
        1. 复合测试项（多组数据，如电压+纹波）
        2. 单边范围（只有min或只有max）
        3. 负数范围（如 [-20, 20]）
        4. 并行处理多个Sheet加速审核
        """
        issues = []
        processed_count = 0

        try:
            if workbook_path:
                wb = openpyxl.load_workbook(workbook_path, data_only=True)
            elif workbook_bytes:
                wb = openpyxl.load_workbook(io.BytesIO(workbook_bytes), data_only=True)
            else:
                return issues, processed_count

            logger.info(f"openpyxl 加载成功，共 {len(wb.sheetnames)} 个 Sheet")

            # 获取需要处理的Sheet列表
            sheets_to_process = []
            for sheet_name in wb.sheetnames:
                if any(skip in sheet_name for skip in skip_sheets):
                    continue
                sheets_to_process.append(sheet_name)

            wb.close()  # 先关闭，避免占用

            # 【性能优化】并行处理多个Sheet
            if len(sheets_to_process) > 2:
                # 多线程并行处理
                logger.info(f"使用并行处理 {len(sheets_to_process)} 个Sheet")
                results = self._validate_sheets_parallel(
                    workbook_path, workbook_bytes, sheets_to_process
                )
                for sheet_issues, sheet_count in results:
                    issues.extend(sheet_issues)
                    processed_count += sheet_count
            else:
                # Sheet数量少，直接串行处理
                for sheet_name in sheets_to_process:
                    sheet_issues, sheet_count = self._validate_single_sheet(
                        workbook_path, workbook_bytes, sheet_name
                    )
                    issues.extend(sheet_issues)
                    processed_count += sheet_count

        except Exception as e:
            logger.error(f"openpyxl 处理失败: {str(e)}")
            import traceback
            traceback.print_exc()

        return issues, processed_count

    def _validate_sheets_parallel(
        self,
        workbook_path: Optional[str],
        workbook_bytes: Optional[bytes],
        sheet_names: List[str]
    ) -> List[Tuple[List[Dict[str, Any]], int]]:
        """并行处理多个Sheet【性能优化】

        使用线程池并行处理多个Sheet，显著提升审核速度

        Args:
            workbook_path: Excel文件路径
            workbook_bytes: Excel文件字节流
            sheet_names: 需要处理的Sheet名称列表

        Returns:
            [(issues, count), ...] 每个Sheet的处理结果列表
        """
        results = []
        max_workers = min(8, len(sheet_names))  # 最多8个线程

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有任务
            future_to_sheet = {
                executor.submit(
                    self._validate_single_sheet,
                    workbook_path,
                    workbook_bytes,
                    sheet_name
                ): sheet_name
                for sheet_name in sheet_names
            }

            # 收集结果
            for future in as_completed(future_to_sheet):
                sheet_name = future_to_sheet[future]
                try:
                    sheet_issues, sheet_count = future.result()
                    results.append((sheet_issues, sheet_count))
                    logger.info(f"  [并行] Sheet '{sheet_name}' 处理完成: {sheet_count}条数据, {len(sheet_issues)}个问题")
                except Exception as e:
                    logger.error(f"  [并行] Sheet '{sheet_name}' 处理失败: {str(e)}")
                    results.append(([], 0))

        return results

    def _is_text_based_sheet(
        self,
        worksheet: Worksheet,
        col_mapping: Dict[str, Optional[int]],
        header_row_idx: int
    ) -> bool:
        """检查Sheet是否为文本描述格式（非数值格式）

        某些Sheet（如Power sequence）的测试数据列包含的是文本描述
        （如"要求：Delay time：...\\n实测：Tu1=0.2ms"），而非纯数值。
        这种Sheet无法进行数值校验，应跳过。

        Args:
            worksheet: openpyxl Worksheet对象
            col_mapping: 列映射字典
            header_row_idx: 表头行索引

        Returns:
            True 表示该Sheet为文本描述格式，应跳过数值校验
        """
        # 获取数据列
        data_col = col_mapping.get("data_col")
        if not data_col:
            return False

        # 采样表头后的前N行数据进行检查
        sample_rows = []
        max_sample = min(header_row_idx + 8, worksheet.max_row + 1)
        for row_idx in range(header_row_idx + 1, max_sample):
            cell = worksheet.cell(row=row_idx, column=data_col)
            val = cell.value
            if val is not None and str(val).strip():
                sample_rows.append(str(val).strip())

        if not sample_rows:
            return False

        # 检查采样数据是否为文本描述格式
        text_count = 0
        numeric_count = 0
        for val in sample_rows:
            # 文本描述特征：包含中文、换行符、文字描述关键词
            has_chinese = any('\u4e00' <= c <= '\u9fff' for c in val)
            has_newline = '\n' in val
            has_text_desc = any(kw in val for kw in ['要求', '实测', '标准', '说明',
                                                       '条件', '规范', '测试', '步骤'])

            if has_chinese or has_newline or has_text_desc:
                text_count += 1

            # 检查是否为纯数值（可被直接解析的数字）
            try:
                float(val.replace(',', '').strip())
                numeric_count += 1
            except (ValueError, TypeError):
                pass

        # 如果文本描述格式的样本占多数(>=50%)，判定为文本描述Sheet
        if text_count >= max(1, len(sample_rows) * 0.5):
            logger.debug(f"  [文本Sheet检测] {worksheet.title}: 采样{len(sample_rows)}行，"
                        f"{text_count}行含文本描述，判定为文本格式Sheet")
            return True

        # 如果数值样本很少(<30%)，也判定为文本描述Sheet
        if numeric_count < len(sample_rows) * 0.3:
            logger.debug(f"  [文本Sheet检测] {worksheet.title}: 采样{len(sample_rows)}行，"
                        f"仅{numeric_count}行可解析为数值，判定为文本格式Sheet")
            return True

        logger.debug(f"  [文本Sheet检测] {worksheet.title}: 采样{len(sample_rows)}行，"
                    f"数值{numeric_count}行/文本{text_count}行，判定为数值格式Sheet")
        return False

    def _validate_single_sheet(
        self,
        workbook_path: Optional[str],
        workbook_bytes: Optional[bytes],
        sheet_name: str
    ) -> Tuple[List[Dict[str, Any]], int]:
        """处理单个Sheet的校验【支持标准模板配置】

        Args:
            workbook_path: Excel文件路径
            workbook_bytes: Excel文件字节流
            sheet_name: Sheet名称

        Returns:
            (issues, processed_count) 问题列表和处理的数据条数
        """
        issues = []
        processed_count = 0

        try:
            if workbook_path:
                wb = openpyxl.load_workbook(workbook_path, data_only=True)
            elif workbook_bytes:
                wb = openpyxl.load_workbook(io.BytesIO(workbook_bytes), data_only=True)
            else:
                return issues, processed_count

            if sheet_name not in wb.sheetnames:
                wb.close()
                return issues, processed_count

            template_config = standard_config.get_config_for_sheet(sheet_name)
            logger.info(f"[线程] 正在处理Sheet: {sheet_name} (使用配置: {template_config.column_mapping.criteria_style})")

            worksheet = wb[sheet_name]
            header_row_idx = template_config.header_row if template_config else self._find_header_row_openpyxl(worksheet)

            if header_row_idx is None:
                logger.warning(f"  Sheet '{sheet_name}' 未找到数据表头，跳过")
                wb.close()
                return issues, processed_count

            logger.info(f"  数据表头位于第 {header_row_idx} 行")

            col_mapping = self._extract_column_mapping_openpyxl(worksheet, header_row_idx, sheet_name)

            col_mapping = self._apply_template_config(col_mapping, template_config)

            # 【检查】如果数据列包含文本描述而非数值，跳过此类Sheet
            # 如 Power sequence 等以文本描述为主的格式
            if self._is_text_based_sheet(worksheet, col_mapping, header_row_idx):
                logger.info(f"  Sheet '{sheet_name}' 数据列为文本描述格式，跳过数值校验")
                wb.close()
                return issues, processed_count

            # 遍历数据行
            for row_idx in range(header_row_idx + 1, worksheet.max_row + 1):
                # 获取测试项名称
                item_cell = worksheet.cell(row=row_idx, column=1)
                item_name = str(item_cell.value).strip() if item_cell.value else ""

                if not item_name:
                    for col_idx in range(1, 4):
                        cell_val = worksheet.cell(row=row_idx, column=col_idx).value
                        if cell_val:
                            item_name = str(cell_val).strip()
                            break

                if not item_name:
                    continue

                # 获取判定结果
                result_value = None
                if col_mapping.get("result_col"):
                    result_cell = worksheet.cell(row=row_idx, column=col_mapping["result_col"])
                    result_value = str(result_cell.value).strip() if result_cell.value else ""

                if not result_value:
                    continue

                is_pass = self._is_pass_result(result_value)
                is_fail = self._is_fail_result(result_value)

                if not is_pass and not is_fail:
                    continue

                # 处理复合测试项【传入sheet_name以启用策略路由】
                test_groups = self._extract_test_groups(worksheet, row_idx, col_mapping, sheet_name)

                if not test_groups:
                    continue

                processed_count += len(test_groups)

                # 【优化】复合测试项：先检查所有组，确定是否有超标组
                has_over_limit_group = False
                for group in test_groups:
                    data_value = group.get("data_value")
                    min_value = group.get("min_value")
                    max_value = group.get("max_value")
                    criteria_style = group.get("criteria_style", "standard")

                    if data_value is None:
                        continue

                    # 检查是否有组超标
                    if self._is_over_limit(data_value, min_value, max_value, criteria_style):
                        has_over_limit_group = True
                        break

                for group in test_groups:
                    data_value = group.get("data_value")
                    min_value = group.get("min_value")
                    max_value = group.get("max_value")
                    group_name = group.get("name", "")
                    criteria_style = group.get("criteria_style", "standard")

                    if data_value is None:
                        continue

                    # 【增强参数传递】处理不等式规则和硬件专有规则
                    extra_params = {}
                    if "inequality_operator" in group:
                        extra_params["inequality_operator"] = group.get("inequality_operator")
                        extra_params["inequality_threshold"] = group.get("inequality_threshold")
                    if "ilmt_top" in group:
                        extra_params["ilmt_top"] = group.get("ilmt_top")

                    issue = self._check_logic_enhanced(
                        sheet_name=sheet_name,
                        row=row_idx,
                        item_name=item_name,
                        data_value=data_value,
                        min_value=min_value,
                        max_value=max_value,
                        is_pass=is_pass,
                        is_fail=is_fail,
                        result_str=result_value,
                        group_name=group_name,
                        criteria_style=criteria_style,
                        has_over_limit_group=has_over_limit_group,
                        **extra_params
                    )

                    if issue:
                        issues.append(issue)

            wb.close()

        except Exception as e:
            logger.error(f"处理Sheet '{sheet_name}' 失败: {str(e)}")

        return issues, processed_count

    def _extract_test_groups(
        self,
        worksheet: Worksheet,
        row_idx: int,
        col_mapping: Dict[str, Optional[int]],
        sheet_name: str = ""
    ) -> List[Dict[str, Any]]:
        """提取一行中的所有测试组【策略模式重构】

        根据sheet名称和criteria_style路由到专门的提取方法

        【策略路由】：
        - "1.1.2" 或电压纹波混合表 -> _extract_voltage_ripple_groups
        - "1.1.4" 或电流表 -> _extract_current_ilmt_groups
        - "1.1.5" 或功耗表 -> _extract_power_groups
        - "1.2.4" 或其他时序表 -> _extract_timing_groups
        - deviation style -> _extract_deviation_groups
        - standard -> 默认处理
        """
        criteria_style = col_mapping.get("criteria_style", "standard")

        # 【策略路由】根据sheet名称确定提取策略
        sheet_lower = sheet_name.lower() if sheet_name else ""

        # Case 1: 1.1.2 Power Stability - 电压和纹波分离
        if "1.1.2" in sheet_lower or "stability" in sheet_lower:
            return self._extract_voltage_ripple_groups(worksheet, row_idx, col_mapping)

        # Case 2: 1.1.4 Current - ILmt-top规则
        if "1.1.4" in sheet_lower or ("current" in sheet_lower and "1.1" in sheet_lower):
            return self._extract_current_ilmt_groups(worksheet, row_idx, col_mapping)

        # Case 3: 1.1.5 Power - 不等式规则
        if "1.1.5" in sheet_lower or ("power" in sheet_lower and "1.1" in sheet_lower):
            return self._extract_power_groups(worksheet, row_idx, col_mapping)

        # Case 4: 1.2.4 I2S Timing - 时序max列
        if "1.2.4" in sheet_lower or "i2s" in sheet_lower:
            return self._extract_timing_groups(worksheet, row_idx, col_mapping)

        # 其他时序类Sheet
        if criteria_style == "timing":
            return self._extract_timing_groups(worksheet, row_idx, col_mapping)

        # 频偏类Sheet
        if criteria_style == "deviation":
            return self._extract_deviation_groups(worksheet, row_idx, col_mapping)

        # 【默认处理】标准Sheet
        return self._extract_standard_groups(worksheet, row_idx, col_mapping)

    def _extract_standard_groups(
        self,
        worksheet: Worksheet,
        row_idx: int,
        col_mapping: Dict[str, Optional[int]]
    ) -> List[Dict[str, Any]]:
        """【默认策略】标准测试组提取

        适用于大多数标准格式的Sheet
        """
        groups = []
        criteria_style = col_mapping.get("criteria_style", "standard")

        # 主测试组（第1组）
        primary_group = {
            "index": 1,
            "name": "",
            "criteria_style": criteria_style,
            "min_value": None,
            "typ_value": None,
            "max_value": None,
            "data_value": None,
        }

        # 提取主测试组的数值
        if col_mapping.get("data_col"):
            data_cell = worksheet.cell(row=row_idx, column=col_mapping["data_col"])
            primary_group["data_value"] = self._extract_number(data_cell.value)

        if col_mapping.get("min_col"):
            min_cell = worksheet.cell(row=row_idx, column=col_mapping["min_col"])
            primary_group["min_value"] = self._extract_number(min_cell.value)

        if col_mapping.get("max_col"):
            max_cell = worksheet.cell(row=row_idx, column=col_mapping["max_col"])
            primary_group["max_value"] = self._extract_number(max_cell.value)

        if col_mapping.get("typ_col"):
            typ_cell = worksheet.cell(row=row_idx, column=col_mapping["typ_col"])
            primary_group["typ_value"] = self._extract_number(typ_cell.value)

        groups.append(primary_group)

        # 检查是否有第2组测试数据（如纹波）
        if col_mapping.get("data_col"):
            primary_data_col = col_mapping["data_col"]

            # 查找第2组测试数据列
            for offset in [2, 3, 4]:
                secondary_data_col = primary_data_col + offset
                if secondary_data_col <= 14:
                    data_cell = worksheet.cell(row=row_idx, column=secondary_data_col)
                    data_value = self._extract_number(data_cell.value)

                    if data_value is not None:
                        secondary_group = {
                            "index": offset,
                            "name": "纹波" if offset == 2 else f"测试{offset}",
                            "criteria_style": "secondary",
                            "min_value": None,
                            "max_value": None,
                            "data_value": data_value,
                        }

                        # 查找第2组的criteria列
                        for crit_offset in [1, 2]:
                            crit_col = secondary_data_col - crit_offset
                            if crit_col > 0:
                                crit_cell = worksheet.cell(row=row_idx, column=crit_col)
                                crit_value = self._extract_number(crit_cell.value)
                                if crit_value is not None:
                                    header_row = row_idx - 1
                                    while header_row > 1:
                                        header_val = worksheet.cell(row=header_row, column=crit_col).value
                                        if header_val and "criteria" in str(header_val).lower():
                                            if "max" in str(header_val).lower():
                                                secondary_group["max_value"] = crit_value
                                            elif "min" in str(header_val).lower():
                                                secondary_group["min_value"] = crit_value
                                            break
                                        header_row -= 1

                        groups.append(secondary_group)

        return groups

    def _is_over_limit(
        self,
        data_value: Optional[float],
        min_value: Optional[float],
        max_value: Optional[float],
        criteria_style: str = "standard"
    ) -> bool:
        """检查测试值是否超出限制

        Args:
            data_value: 实测值
            min_value: 最小限制
            max_value: 最大限制
            criteria_style: 标准类型

        Returns:
            True if data_value exceeds the limit
        """
        if data_value is None:
            return False

        # 【修复】处理倒置范围（min > max 时自动交换）
        # 如时序中的 [497, 1] 实际含义是 max=497（时间需<497ms）
        if min_value is not None and max_value is not None and min_value > max_value:
            min_value, max_value = max_value, min_value
            logger.debug(f"  【修复】倒置范围已自动交换 -> [{min_value}, {max_value}]")

        # 范围判断
        if min_value is not None and max_value is not None:
            return data_value < min_value or data_value > max_value
        elif max_value is not None:
            return data_value > max_value
        elif min_value is not None:
            return data_value < min_value

        return False

    def _build_col_header_map(self, worksheet: Worksheet, max_scan_row: int = 12) -> Dict[int, str]:
        """构建列号→表头文本的映射，用于列内容识别

        扫描工作表前N行的所有单元格，将每列的文本聚合起来

        Args:
            worksheet: openpyxl Worksheet对象
            max_scan_row: 最大扫描行数

        Returns:
            {col_idx: "聚合后的表头文本小写"} 字典
        """
        col_headers: Dict[int, list] = {}
        for check_row in range(1, min(max_scan_row + 1, worksheet.max_row + 1)):
            for col_idx in range(1, min(16, worksheet.max_column + 1)):
                cell_val = worksheet.cell(row=check_row, column=col_idx).value
                if cell_val and str(cell_val).strip():
                    val_str = str(cell_val).strip().lower()
                    if col_idx not in col_headers:
                        col_headers[col_idx] = []
                    col_headers[col_idx].append(val_str)

        result = {}
        for col_idx, headers in col_headers.items():
            result[col_idx] = " ".join(headers)
        return result

    def _extract_voltage_ripple_groups(
        self,
        worksheet: Worksheet,
        row_idx: int,
        col_mapping: Dict[str, Optional[int]]
    ) -> List[Dict[str, Any]]:
        """提取电压和纹波分离的测试组【修复Case 1 + 纹波阈值修复】

        1.1.2 Power Stability表格结构：
        - 电压组：列5(min)=4.75, 列6(typ)=5, 列7(max)=5.25, 列8(data)=5.5
        - 纹波组：列9(criteria/max)=45, 列10(data)=55.6

        关键修复：
        1. 列9是纹波criteria，不能混淆为电压max
        2. 纹波criteria值可能<50mV（如20mV、45mV），不能硬编码阈值判断
        3. 改用表头文本检测判定纹波列
        """
        groups = []

        # 构建表头映射（用于列内容识别）
        col_headers = self._build_col_header_map(worksheet)

        def _col_has(col_idx: int, keyword: str) -> bool:
            """检查指定列的表头是否包含关键词"""
            if col_idx in col_headers:
                return keyword.lower() in col_headers[col_idx]
            return False

        # 主电压组
        voltage_group = {
            "index": 1,
            "name": "电压",
            "criteria_style": "standard",
            "min_value": None,
            "typ_value": None,
            "max_value": None,
            "data_value": None,
        }

        # 【关键修复】根据标准列位置提取电压组数值
        # 标准列：列5=min, 列6=typ, 列7=max, 列8=data
        min_col = col_mapping.get("min_col", 5)
        typ_col = col_mapping.get("typ_col", 6)
        max_col = col_mapping.get("max_col", 7)
        data_col = col_mapping.get("data_col", 8)

        # 提取min值（通常是列5）
        if col_mapping.get("min_col"):
            cell = worksheet.cell(row=row_idx, column=col_mapping["min_col"])
            voltage_group["min_value"] = self._extract_number(cell.value)

        # 提取typ值（通常是列6）
        if col_mapping.get("typ_col"):
            cell = worksheet.cell(row=row_idx, column=col_mapping["typ_col"])
            voltage_group["typ_value"] = self._extract_number(cell.value)

        # 【关键修复】提取电压max值
        # 电压组max应该在data_col之前1列，即 voltage_max_col = data_col - 1
        voltage_max_col = data_col - 1  # 通常是列7

        # 判断col_mapping["max_col"]列是否为纹波列（通过表头检测）
        mapped_max_col = col_mapping.get("max_col")
        is_max_col_ripple = mapped_max_col is not None and (
            _col_has(mapped_max_col, "ripple") or _col_has(mapped_max_col, "纹波")
        )

        if mapped_max_col and is_max_col_ripple:
            # max_col指向的是纹波criteria列，电压max从data_col-1读取
            cell = worksheet.cell(row=row_idx, column=voltage_max_col)
            voltage_group["max_value"] = self._extract_number(cell.value)
            logger.debug(f"  Row{row_idx}: 电压max使用列{voltage_max_col}={voltage_group['max_value']}（列{mapped_max_col}是纹波criteria）")
        elif mapped_max_col:
            # max_col是正常电压max列
            cell = worksheet.cell(row=row_idx, column=mapped_max_col)
            voltage_group["max_value"] = self._extract_number(cell.value)

        # 提取实测值
        if col_mapping.get("data_col"):
            cell = worksheet.cell(row=row_idx, column=col_mapping["data_col"])
            voltage_group["data_value"] = self._extract_number(cell.value)

        # 只有当电压组有数据时才添加
        if voltage_group["data_value"] is not None or voltage_group["max_value"] is not None:
            groups.append(voltage_group)

        # 纹波组 - 通过表头扫描查找纹波列
        # 策略：先找到含"ripple/纹波"关键词的criteria列，纹波data列即criteria列+1
        ripple_criteria_col = None
        ripple_data_col = None

        # 扫描列表头（跳过1-4列标签列），找纹波相关列
        for col_idx in range(5, 15):
            header = col_headers.get(col_idx, "")
            if ("ripple" in header or "纹波" in header):
                # 该列表头含纹波关键词，再看是否同时含criteria/标准关键词
                if "criteria" in header or "标准" in header:
                    ripple_criteria_col = col_idx
                    logger.debug(f"  Row: 识别纹波criteria列: 列{col_idx}")
                elif "test data" in header or "实测" in header or "测试数据" in header:
                    ripple_data_col = col_idx
                    logger.debug(f"  Row: 识别纹波data列: 列{col_idx}")

        # 如果只找到纹波criteria但没找到纹波data，data在criteria+1
        if ripple_criteria_col and not ripple_data_col:
            candidate_data_col = ripple_criteria_col + 1
            if candidate_data_col <= 14:
                # 验证data候选列表头是否像测试数据
                data_header = col_headers.get(candidate_data_col, "")
                if "test data" in data_header or "实测" in data_header or "测试数据" in data_header:
                    ripple_data_col = candidate_data_col
                    logger.info(f"  推断纹波data列: 列{ripple_data_col} (criteria列+1, 表头: {data_header[:80]})")

        # 提取纹波数据
        if ripple_data_col and ripple_data_col <= 14:
            ripple_data_cell = worksheet.cell(row=row_idx, column=ripple_data_col)
            ripple_data_value = self._extract_number(ripple_data_cell.value)

            if ripple_data_value is not None:
                ripple_group = {
                    "index": 2,
                    "name": "纹波",
                    "criteria_style": "ripple",
                    "min_value": None,
                    "max_value": None,
                    "data_value": ripple_data_value,
                }

                # 提取纹波criteria值
                if ripple_criteria_col:
                    crit_cell = worksheet.cell(row=row_idx, column=ripple_criteria_col)
                    crit_value = self._extract_number(crit_cell.value)
                    if crit_value is not None:
                        ripple_group["max_value"] = crit_value
                        logger.debug(f"  Row{row_idx}: 纹波criteria={crit_value} (列{ripple_criteria_col})")
                else:
                    # 回退：criteria列在data列之前
                    for crit_offset in [1, 2]:
                        crit_col = ripple_data_col - crit_offset
                        if crit_col > 0:
                            crit_cell = worksheet.cell(row=row_idx, column=crit_col)
                            crit_value = self._extract_number(crit_cell.value)
                            if crit_value is not None:
                                # 验证：criteria表头含ripple关键词
                                if _col_has(crit_col, "ripple") or _col_has(crit_col, "纹波"):
                                    ripple_group["max_value"] = crit_value
                                    break

                groups.append(ripple_group)
                logger.debug(f"  Row{row_idx}: 添加纹波组 data={ripple_data_value}, max={ripple_group['max_value']}")

        return groups

    def _extract_current_ilmt_groups(
        self,
        worksheet: Worksheet,
        row_idx: int,
        col_mapping: Dict[str, Optional[int]]
    ) -> List[Dict[str, Any]]:
        """提取电流组并支持ILmt-top规则【修复Case 2】

        1.1.4 Current表格结构：
        - 列4 = ILmt-top (限流阈值，如40mA)
        - 列5 = 实测电流值（如50mA）

        规则：Imax < ILmt-top，即实测值应该小于ILmt-top阈值
        如果实测值 >= ILmt-top，则判定为FAIL

        【修复】：
        1. ILmt-top值必须明显大于min/max范围值（差异>50%）
        2. 需要表头明确标记为ILmt或限流相关，否则使用标准范围判断
        3. 如果同时有标准范围和ILmt-top，优先使用标准范围
        """
        groups = []

        # 先提取min/max值，用于判断是否是ILmt-top测试
        min_val = None
        max_val = None
        data_val = None

        if col_mapping.get("data_col"):
            cell = worksheet.cell(row=row_idx, column=col_mapping["data_col"])
            data_val = self._extract_number(cell.value)

        if col_mapping.get("min_col"):
            cell = worksheet.cell(row=row_idx, column=col_mapping["min_col"])
            min_val = self._extract_number(cell.value)

        if col_mapping.get("max_col"):
            cell = worksheet.cell(row=row_idx, column=col_mapping["max_col"])
            max_val = self._extract_number(cell.value)

        # 判断是否应该使用ILmt-top规则
        # 条件：1）表头明确标记ILmt 2）ILmt-top值明显大于范围值（差异>50%）
        ilmt_top = None
        ilmt_top_col = None

        for col_idx in range(2, 8):
            if col_idx == col_mapping.get("data_col"):
                continue
            if col_idx == col_mapping.get("min_col"):
                continue
            if col_idx == col_mapping.get("max_col"):
                continue

            cell = worksheet.cell(row=row_idx, column=col_idx)
            val = self._extract_number(cell.value)

            if val is None:
                continue

            # 检查表头确认是否是ILmt-top
            header_row = max(1, row_idx - 5)
            header_val = None
            for h_row in range(header_row, row_idx):
                h = worksheet.cell(row=h_row, column=col_idx).value
                if h and "ilmt" in str(h).lower():
                    header_val = str(h)
                    break

            # ILmt-top识别条件：
            # 1. 表头明确标记ILmt/限流，或
            # 2. 值明显大于max_val（至少50%差异）
            is_likely_ilmt = False
            if header_val:
                is_likely_ilmt = True
            elif val > 10 and max_val and val > max_val * 1.5:
                # 值明显大于max范围（ILmt通常是保护限制，会比正常max大很多）
                is_likely_ilmt = True

            if is_likely_ilmt:
                ilmt_top = val
                ilmt_top_col = col_idx
                logger.debug(f"  Row{row_idx}: 识别到ILmt-top={val}（列{col_idx}，表头={header_val}）")
                break

        # 【关键修复】决定使用哪种判断逻辑
        # 如果有ILmt-top且ILmt-top > max范围，则使用ILmt-top规则
        # 否则使用标准范围判断
        if ilmt_top is not None and max_val is not None and ilmt_top > max_val:
            # 使用ILmt-top规则
            criteria_style = "current_ilmt"
        elif min_val is not None or max_val is not None:
            # 使用标准范围判断（待机电流等只有min限制的情况）
            criteria_style = "standard"
            ilmt_top = None  # 不使用ILmt-top
        else:
            # 没有任何范围信息，使用ILmt-top作为唯一判断
            criteria_style = "current_ilmt" if ilmt_top else "standard"

        # 主电流组
        current_group = {
            "index": 1,
            "name": "电流",
            "criteria_style": criteria_style,
            "min_value": min_val,
            "max_value": max_val,
            "data_value": data_val,
            "ilmt_top": ilmt_top,  # ILmt-top阈值
        }

        if criteria_style == "current_ilmt" and ilmt_top:
            logger.debug(f"  Row{row_idx}: 使用ILmt-top规则，ILmt-top={ilmt_top}, data={data_val}")
        else:
            logger.debug(f"  Row{row_idx}: 使用标准范围判断，min={min_val}, max={max_val}, data={data_val}")

        groups.append(current_group)
        return groups

    def _extract_power_groups(
        self,
        worksheet: Worksheet,
        row_idx: int,
        col_mapping: Dict[str, Optional[int]]
    ) -> List[Dict[str, Any]]:
        """提取功耗组并支持不等式规则【修复Case 3】

        1.1.5 Power表格结构：
        - 列4 = 描述，如"业务功耗 小于9.0W"
        - 列6 = criteria值（如9）
        - 列7 = 实测值（如8.5）

        规则：从描述中解析不等式"小于9.0W"，则标准为 < 9.0
        """
        groups = []

        # 查找描述列
        item_col = col_mapping.get("item_col", 1)
        desc = None

        for col_idx in range(item_col, min(item_col + 6, 15)):
            cell = worksheet.cell(row=row_idx, column=col_idx)
            val = cell.value
            if val and isinstance(val, str) and any(k in val for k in ["小于", "大于", "≤", "≥", "<", ">"]):
                desc = val
                break

        # 主功耗组
        power_group = {
            "index": 1,
            "name": "功耗",
            "criteria_style": "power",
            "min_value": None,
            "max_value": None,
            "data_value": None,
            "inequality_operator": None,
            "inequality_threshold": None,
        }

        # 解析描述中的不等式规则
        if desc:
            op, threshold = self._parse_inequality_from_desc(desc)
            if op and threshold:
                # 【修复】功耗标准通常是 "<" 或 "≤"，都使用 <= 判断
                # 这样 9.0 <= 9.0 边界情况可以正确处理
                if op == "<":
                    power_group["inequality_operator"] = "<="
                else:
                    power_group["inequality_operator"] = op
                power_group["inequality_threshold"] = threshold
                logger.info(f"  解析功耗不等式: '{desc}' -> ('{op}', {threshold})")

        # 提取实测值
        if col_mapping.get("data_col"):
            cell = worksheet.cell(row=row_idx, column=col_mapping["data_col"])
            power_group["data_value"] = self._extract_number(cell.value)

        # 提取标准值（功耗标准为上限，放入max_value）
        if col_mapping.get("min_col"):
            cell = worksheet.cell(row=row_idx, column=col_mapping["min_col"])
            criteria_val = self._extract_number(cell.value)
            # 【修复】功耗criteria是上限值（max），不是下限（min）
            # 只有当没有不等式规则时，才将criteria作为max_value
            if criteria_val is not None and not power_group.get("inequality_operator"):
                power_group["max_value"] = criteria_val
                power_group["min_value"] = None
                logger.debug(f"  【修复】功耗criteria {criteria_val} 作为上限(max)值")
            else:
                power_group["min_value"] = criteria_val

        if col_mapping.get("max_col"):
            cell = worksheet.cell(row=row_idx, column=col_mapping["max_col"])
            max_val = self._extract_number(cell.value)
            if max_val is not None and power_group.get("max_value") is None:
                power_group["max_value"] = max_val

        groups.append(power_group)
        return groups

    def _extract_timing_groups(
        self,
        worksheet: Worksheet,
        row_idx: int,
        col_mapping: Dict[str, Optional[int]]
    ) -> List[Dict[str, Any]]:
        """提取时序组，确保正确处理max列【修复Case 4】

        1.2.4 I2S Timing表格结构：
        - 列4 = min值（如1.44）
        - 列6 = max值（如1.8）注意：不是列5
        - 列8 = 实测值（如1.839）

        规则：min ≤ value ≤ max
        Case 4问题：1.839 > 1.8，应该FAIL

        【关键修复】：
        时序sheet的timing组内min/max列已经在col_mapping中正确设置
        应该使用col_mapping["max_col"]而不是criteria_col
        因为criteria_col通常指向timing组的min列
        """
        groups = []

        # 主时序组
        timing_group = {
            "index": 1,
            "name": "时序",
            "criteria_style": "timing",
            "min_value": None,
            "max_value": None,
            "data_value": None,
        }

        # 提取min值 - 使用col_mapping中timing组内识别的min_col
        if col_mapping.get("min_col"):
            cell = worksheet.cell(row=row_idx, column=col_mapping["min_col"])
            timing_group["min_value"] = self._extract_number(cell.value)

        # 【关键修复】正确提取max值
        # 对于timing组，必须使用col_mapping["max_col"]
        # 因为timing组的max列是独立的（如1.2.4中列6是max）
        # criteria_col可能指向timing组的min列，不能混淆
        if col_mapping.get("max_col"):
            cell = worksheet.cell(row=row_idx, column=col_mapping["max_col"])
            timing_group["max_value"] = self._extract_number(cell.value)
            logger.debug(f"  Row{row_idx} 时序max从列{col_mapping['max_col']}提取: {timing_group['max_value']}")

        # 提取实测值
        if col_mapping.get("data_col"):
            cell = worksheet.cell(row=row_idx, column=col_mapping["data_col"])
            timing_group["data_value"] = self._extract_number(cell.value)
            logger.debug(f"  Row{row_idx} 时序data从列{col_mapping['data_col']}提取: {timing_group['data_value']}")

        # 【调试日志】输出提取结果
        if timing_group['data_value'] is not None:
            logger.debug(f"  Row{row_idx} 时序组提取结果: min={timing_group['min_value']}, max={timing_group['max_value']}, data={timing_group['data_value']}")

        groups.append(timing_group)
        return groups

    def _extract_deviation_groups(
        self,
        worksheet: Worksheet,
        row_idx: int,
        col_mapping: Dict[str, Optional[int]]
    ) -> List[Dict[str, Any]]:
        """提取频偏组【保留原有功能】

        1.2.2 Clock Signal表格结构：
        - 列7/8 = min/max范围（如[-30, 30]）
        - 列6 = 实测值

        格式：范围字符串 "[-30, 30]"
        """
        groups = []

        # 主频偏组
        deviation_group = {
            "index": 1,
            "name": "频偏",
            "criteria_style": "deviation",
            "min_value": None,
            "max_value": None,
            "data_value": None,
        }

        # 从criteria列解析范围
        if col_mapping.get("criteria_col"):
            cell = worksheet.cell(row=row_idx, column=col_mapping["criteria_col"])
            min_val, max_val = self._parse_deviation_range(cell.value)
            deviation_group["min_value"] = min_val
            deviation_group["max_value"] = max_val
        else:
            # 回退：从min_col/max_col获取
            if col_mapping.get("min_col"):
                cell = worksheet.cell(row=row_idx, column=col_mapping["min_col"])
                deviation_group["min_value"] = self._extract_number(cell.value)
            if col_mapping.get("max_col"):
                cell = worksheet.cell(row=row_idx, column=col_mapping["max_col"])
                deviation_group["max_value"] = self._extract_number(cell.value)

        # 提取实测值
        if col_mapping.get("data_col"):
            cell = worksheet.cell(row=row_idx, column=col_mapping["data_col"])
            deviation_group["data_value"] = self._extract_number(cell.value)

        groups.append(deviation_group)
        return groups

    def _parse_deviation_range(self, value: Any) -> Tuple[Optional[float], Optional[float]]:
        """解析频偏范围格式

        频偏格式如 "[-30, 30]" 或 "[-20, 20]" ppm
        返回 (min, max) 元组

        Args:
            value: 单元格值，可能是字符串或数字

        Returns:
            (min_value, max_value) 元组
        """
        if value is None:
            return None, None

        # 如果是数字，直接使用
        if isinstance(value, (int, float)):
            return float(value), float(value)

        # 如果是字符串，尝试解析范围格式
        if isinstance(value, str):
            value = value.strip()

            # 尝试匹配 [-30, 30] 格式
            match = re.match(r'\[(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\]', value)
            if match:
                min_val = float(match.group(1))
                max_val = float(match.group(2))
                logger.info(f"  解析频偏范围: '{value}' -> [{min_val}, {max_val}]")
                return min_val, max_val

            # 尝试匹配 -30 格式（单个值）
            try:
                val = float(value)
                return val, val
            except ValueError:
                pass

        return None, None

    def _parse_inequality_from_desc(self, desc: str) -> Tuple[Optional[str], Optional[float]]:
        """从描述文本中解析不等式规则

        支持的不等式格式：
        - "小于9.0W" -> ("<", 9.0)
        - "≤9.0W" -> ("<=", 9.0)
        - "大于3.3V" -> (">", 3.3)
        - "≥3.3V" -> (">=", 3.3)
        - "<100mV" -> ("<", 100)
        - "<= 50" -> ("<=", 50)

        Args:
            desc: 描述文本

        Returns:
            (operator, threshold) 元组，如 ("<", 9.0) 或 (None, None)
        """
        if not desc or not isinstance(desc, str):
            return None, None

        desc = desc.strip()

        # 匹配各种不等式格式
        patterns = [
            # 中文格式
            (r'小于等于?\s*(\d+\.?\d*)', "<="),
            (r'小于\s*(\d+\.?\d*)', "<"),
            (r'大于等于?\s*(\d+\.?\d*)', ">="),
            (r'大于\s*(\d+\.?\d*)', ">"),
            # 英文/符号格式
            (r'≤\s*(\d+\.?\d*)', "<="),
            (r'<\s*(\d+\.?\d*)', "<"),
            (r'≥\s*(\d+\.?\d*)', ">="),
            (r'>\s*(\d+\.?\d*)', ">"),
        ]

        for pattern, op in patterns:
            match = re.search(pattern, desc)
            if match:
                try:
                    threshold = float(match.group(1))
                    logger.debug(f"  解析不等式: '{desc}' -> ('{op}', {threshold})")
                    return op, threshold
                except ValueError:
                    continue

        return None, None

    def _apply_inequality_rule(
        self,
        data_value: float,
        operator: str,
        threshold: float
    ) -> bool:
        """应用不等式规则判断测试值是否合规

        Args:
            data_value: 实测值
            operator: 操作符 ("<", "<=", ">", ">=")
            threshold: 阈值

        Returns:
            True if data_value satisfies the rule
        """
        if operator == "<":
            return data_value < threshold
        elif operator == "<=":
            return data_value <= threshold
        elif operator == ">":
            return data_value > threshold
        elif operator == ">=":
            return data_value >= threshold
        return False

    def _parse_timing_criteria(self, value: Any) -> Tuple[Optional[float], Optional[float]]:
        """解析时序标准格式

        时序格式可能是：
        - 单个值: "2.0ns" -> min=2.0, max=None
        - 范围: ">2.0" -> min=2.0, max=None
        - 范围: ">=1.2" -> min=1.2, max=None

        Args:
            value: 单元格值

        Returns:
            (min_value, max_value) 元组
        """
        if value is None:
            return None, None

        # 如果是数字，直接使用
        if isinstance(value, (int, float)):
            return float(value), None  # 时序通常只有下限

        # 如果是字符串，尝试解析
        if isinstance(value, str):
            value = value.strip()

            # 匹配 >=2.0 或 >2.0 格式
            match = re.match(r'[>=]+\s*(\d+\.?\d*)', value)
            if match:
                min_val = float(match.group(1))
                logger.info(f"  解析时序标准: '{value}' -> min={min_val}, max=None")
                return min_val, None

            # 尝试匹配 <=2.0 格式
            match = re.match(r'<=\s*(\d+\.?\d*)', value)
            if match:
                max_val = float(match.group(1))
                logger.info(f"  解析时序标准: '{value}' -> min=None, max={max_val}")
                return None, max_val

            # 尝试直接转换为数字
            try:
                val = float(value)
                return val, None
            except ValueError:
                pass

        return None, None

    def _find_header_row_openpyxl(self, worksheet: Worksheet) -> Optional[int]:
        """使用 openpyxl 查找数据表头行

        寻找包含 criteria(min), criteria(max), test data 等关键字的行
        """
        # 用户Excel的表头在第9-10行
        # 先检查前20行
        for row_idx in range(1, min(21, worksheet.max_row + 1)):
            row_cells = []
            for col_idx in range(1, 15):
                cell_val = worksheet.cell(row=row_idx, column=col_idx).value
                if cell_val:
                    row_cells.append(str(cell_val))

            if not row_cells:
                continue

            row_str = " ".join(row_cells)

            # 检查是否包含关键字
            keywords = [
                "criteria(min)", "criteria(max)", "criteria(typ)",
                "test data", "test result",
                "最小标准", "最大标准", "实测", "测试结果"
            ]

            for keyword in keywords:
                if keyword.lower() in row_str.lower():
                    logger.info(f"  找到表头行: 第{row_idx}行, 内容: {row_str[:100]}")
                    return row_idx

        return None

    def _extract_column_mapping_openpyxl(
        self,
        worksheet: Worksheet,
        header_row_idx: int,
        sheet_name: str = ""
    ) -> Dict[str, Optional[int]]:
        """使用 openpyxl 提取列位置映射

        这是核心：精确匹配列位置
        基于用户Excel的实际结构：
        - E列(5) = criteria(min)
        - F列(6) = criteria(typ)
        - G列(7) = criteria(max)
        - H列(8) = test data
        - K列(11) = Test Result

        【特殊Sheet处理】：
        - 1.2.2 Clock Signal: 双行表头，min在列7，max在列8，data在列6
        - 1.2.9 RGMII: Timings列包含标准值
        """
        mapping = {
            "item_col": 1,  # A列 - 测试项名称
            "min_col": None,
            "typ_col": None,
            "max_col": None,
            "data_col": None,
            "result_col": None,
            "criteria_style": "standard"  # standard | timing | deviation
        }

        # 【核心修复】先检查双行表头情况
        # 扫描header_row_idx和header_row_idx+1两行
        sub_headers = {}
        for check_row in [header_row_idx, header_row_idx + 1]:
            if check_row > worksheet.max_row:
                continue
            for col_idx in range(1, 15):
                cell_val = worksheet.cell(row=check_row, column=col_idx).value
                if cell_val:
                    val_str = str(cell_val).replace("\n", " ").lower()
                    if col_idx not in sub_headers:
                        sub_headers[col_idx] = []
                    sub_headers[col_idx].append(val_str)

        # 检查是否有"min"和"max"子表头（双行表头特征）
        has_min_header = False
        has_max_header = False
        has_timing_group = False
        timing_group_cols = {}  # 记录timing组内的min/typ/max列

        for col_idx, headers in sub_headers.items():
            combined_headers = " ".join(headers)

            # 检查是否是频率偏差类的min/max（必须包含deviation或frequency context）
            if "min" in combined_headers and ("deviation" in combined_headers or "频偏" in combined_headers):
                has_min_header = True
                mapping["min_col"] = col_idx
            if "max" in combined_headers and ("deviation" in combined_headers or "频偏" in combined_headers):
                has_max_header = True
                if mapping["max_col"] is None or mapping["max_col"] < col_idx:
                    mapping["max_col"] = col_idx

            # 检查是否是timing组的min/typ/max
            if "timing" in combined_headers or "时间" in combined_headers:
                has_timing_group = True
            # 记录min/typ/max列（用于timing组）
            for h in headers:
                h_lower = h.lower()
                if "min" == h_lower or "最小" in h_lower:
                    timing_group_cols["min"] = col_idx
                elif "typ" == h_lower or "typical" in h_lower or "典型" in h_lower:
                    timing_group_cols["typ"] = col_idx
                elif "max" == h_lower or "最大" in h_lower:
                    timing_group_cols["max"] = col_idx

        # 【特殊处理】频偏类Sheet - 检测min/max列
        if has_min_header and has_max_header:
            mapping["criteria_style"] = "deviation"
            # 找data列 - 精确查找"test data"或"实测"列
            for col_idx in range(1, 15):
                if col_idx in sub_headers:
                    for h in sub_headers[col_idx]:
                        if "test data" in h or "实测" in h:
                            mapping["data_col"] = col_idx
                            break
            # 找result列 - 精确查找"result"或"结果"列
            if mapping["result_col"] is None:
                for col_idx in range(1, 15):
                    if col_idx in sub_headers:
                        for h in sub_headers[col_idx]:
                            if "result" in h or "结果" in h:
                                mapping["result_col"] = col_idx
                                break

        # 【核心修复】检查是否有Timings组（1.2.9 RGMII时序）
        # 1.2.9结构：列4=timing(min), 列5=timing(typ), 列6=timing(max), 列8=test data, 列9=result
        if has_timing_group and timing_group_cols and not (has_min_header and has_max_header):
            mapping["criteria_style"] = "timing"
            if "min" in timing_group_cols:
                mapping["min_col"] = timing_group_cols["min"]
            if "typ" in timing_group_cols:
                mapping["typ_col"] = timing_group_cols["typ"]
            if "max" in timing_group_cols:
                mapping["max_col"] = timing_group_cols["max"]
            # 找data列和result列
            for col_idx in range(1, 15):
                if col_idx in sub_headers:
                    for h in sub_headers[col_idx]:
                        if "test data" in h or "实测" in h:
                            mapping["data_col"] = col_idx
                        if "result" in h or "结果" in h:
                            mapping["result_col"] = col_idx

        # 检查是否有 Timings 列（包含标准值）
        timing_col = None
        for col_idx, headers in sub_headers.items():
            for h in headers:
                if "timing" in h or "时间" in h:
                    timing_col = col_idx
                    break

        # 精确匹配已知列位置
        header_values = sub_headers  # 使用聚合后的header
        for col_idx, headers in header_values.items():
            for header_val in headers:
                header_lower = header_val.lower()

                # 最小值列 - 匹配多种格式
                if ("criteria(min)" in header_val or "最小标准" in header_val or
                    "标准(最小)" in header_val or "判定标准(最小)" in header_val or
                    ("最小" in header_val and "标准" in header_val)):
                    if mapping["min_col"] is None:
                        mapping["min_col"] = col_idx
                        logger.info(f"  找到最小值列: 列{col_idx} = '{header_val}'")

                # 典型值列 - 匹配多种格式
                if ("criteria(typ)" in header_val or "典型" in header_val or
                    "标准(典型)" in header_val or "typ" in header_lower):
                    if mapping["typ_col"] is None:
                        mapping["typ_col"] = col_idx

                # 最大值列 - 匹配多种格式
                if ("criteria(max)" in header_val or "最大标准" in header_val or
                    "标准(最大)" in header_val or "判定标准(最大)" in header_val or
                    ("最大" in header_val and "标准" in header_val)):
                    if mapping["max_col"] is None:
                        mapping["max_col"] = col_idx
                        logger.info(f"  找到最大值列: 列{col_idx} = '{header_val}'")

                # 测试数据列
                if ("test data" in header_val or "实测" in header_val or
                    "测试数据" in header_val or "测试值" in header_val):
                    if mapping["data_col"] is None:
                        mapping["data_col"] = col_idx
                        logger.info(f"  找到实测值列: 列{col_idx} = '{header_val}'")

                # 测试结果列 - 避免误匹配 "判定标准(最小)" 等
                if ("test result" in header_val or "测试结果" in header_val or
                    "判定结果" in header_val):
                    if mapping["result_col"] is None:
                        mapping["result_col"] = col_idx
                        logger.info(f"  找到判定结果列: 列{col_idx} = '{header_val}'")

        # 【核心修复】根据criteria_style设置正确的列映射
        if mapping["criteria_style"] == "deviation":
            # 频偏Sheet：使用已识别的列
            pass
        elif timing_col and mapping["criteria_style"] != "standard":
            # 时序Sheet：标准值在timing列
            mapping["criteria_col"] = timing_col
            mapping["criteria_style"] = "timing"
        else:
            # 标准Sheet：使用常规列映射
            # 如果精确匹配失败，使用列位置推断
            if mapping["data_col"] is None and mapping["max_col"] is not None:
                mapping["data_col"] = mapping["max_col"] + 1

            if mapping["result_col"] is None and mapping["data_col"] is not None:
                mapping["result_col"] = mapping["data_col"] + 3

            # 备用方案：直接使用已知位置
            if mapping["min_col"] is None:
                mapping["min_col"] = 5   # E列
            if mapping["max_col"] is None:
                mapping["max_col"] = 7   # G列
            if mapping["data_col"] is None:
                mapping["data_col"] = 8  # H列
            if mapping["result_col"] is None:
                mapping["result_col"] = 11  # K列

        logger.info(f"  最终列映射: min={mapping['min_col']}, max={mapping['max_col']}, data={mapping['data_col']}, result={mapping['result_col']}, style={mapping['criteria_style']}")

        return mapping

    def _apply_template_config(
        self,
        col_mapping: Dict[str, Optional[int]],
        template_config
    ) -> Dict[str, Optional[int]]:
        """应用标准模板配置

        Args:
            col_mapping: 当前列映射
            template_config: 模板配置

        Returns:
            合并后的列映射
        """
        if not template_config or not template_config.column_mapping:
            return col_mapping

        template_mapping = template_config.column_mapping

        final_mapping = col_mapping.copy()

        if template_mapping.min_col and col_mapping.get("min_col") is None:
            final_mapping["min_col"] = template_mapping.min_col
        if template_mapping.typ_col and col_mapping.get("typ_col") is None:
            final_mapping["typ_col"] = template_mapping.typ_col
        if template_mapping.max_col and col_mapping.get("max_col") is None:
            final_mapping["max_col"] = template_mapping.max_col
        if template_mapping.data_col and col_mapping.get("data_col") is None:
            final_mapping["data_col"] = template_mapping.data_col
        if template_mapping.result_col and col_mapping.get("result_col") is None:
            final_mapping["result_col"] = template_mapping.result_col
        if template_mapping.item_col and col_mapping.get("item_col") is None:
            final_mapping["item_col"] = template_mapping.item_col
        if template_mapping.criteria_style:
            final_mapping["criteria_style"] = template_mapping.criteria_style
        if template_mapping.criteria_col:
            final_mapping["criteria_col"] = template_mapping.criteria_col

        return final_mapping

    def _validate_with_dataframe(
        self,
        sheets_data: Dict[str, Any],
        skip_sheets: List[str]
    ) -> Tuple[List[Dict[str, Any]], int]:
        """使用 pandas DataFrame 校验（备用方案）"""
        issues = []
        processed_count = 0

        for sheet_name, df in sheets_data.items():
            if df is None or df.empty:
                continue

            if any(skip in sheet_name for skip in skip_sheets):
                continue

            logger.info(f"正在处理Sheet (DataFrame模式): {sheet_name}")

            # 查找数据表头行
            header_row_idx = self._find_header_row(df)

            if header_row_idx is None:
                continue

            # 提取列位置映射
            col_mapping = self._extract_column_mapping(df, header_row_idx)

            if not col_mapping.get("data_col") or not col_mapping.get("result_col"):
                continue

            # 遍历数据行
            df_reset = df.reset_index(drop=True)
            for idx in range(header_row_idx + 1, len(df_reset)):
                row = df_reset.iloc[idx]

                # 获取测试项名称
                item_name = ""
                if col_mapping.get("item_col") is not None:
                    item_val = row.iloc[col_mapping["item_col"]]
                    if item_val and str(item_val).strip():
                        item_name = str(item_val).strip()

                # 获取测试数据值
                data_col_idx = col_mapping["data_col"]
                data_raw = row.iloc[data_col_idx] if data_col_idx < len(row) else None
                data_value = self._extract_number(data_raw)

                # 获取最小值和最大值
                min_value = None
                max_value = None

                if col_mapping.get("min_col") is not None:
                    min_col_idx = col_mapping["min_col"]
                    min_raw = row.iloc[min_col_idx] if min_col_idx < len(row) else None
                    min_value = self._extract_number(min_raw)

                if col_mapping.get("max_col") is not None:
                    max_col_idx = col_mapping["max_col"]
                    max_raw = row.iloc[max_col_idx] if max_col_idx < len(row) else None
                    max_value = self._extract_number(max_raw)

                # 获取判定结果
                result_col_idx = col_mapping["result_col"]
                result_raw = row.iloc[result_col_idx] if result_col_idx < len(row) else None
                if result_raw is None:
                    continue

                result_str = str(result_raw).strip()

                if not result_str:
                    continue

                processed_count += 1

                is_pass = self._is_pass_result(result_str)
                is_fail = self._is_fail_result(result_str)

                if not is_pass and not is_fail:
                    continue

                issue = self._check_logic(
                    sheet_name=sheet_name,
                    row=idx + 1,
                    item_name=item_name,
                    data_value=data_value,
                    min_value=min_value,
                    max_value=max_value,
                    is_pass=is_pass,
                    is_fail=is_fail,
                    result_str=result_str
                )

                if issue:
                    issues.append(issue)

        return issues, processed_count

    # ==================== DataFrame 模式辅助方法 ====================

    def _find_header_row(self, df) -> Optional[int]:
        """查找数据表头行（DataFrame模式）"""
        df_reset = df.reset_index(drop=True)

        patterns = [
            "criteria(min)", "criteria(max)", "test data",
            "criteria", "最小标准", "最大标准", "实测",
            "test result", "测试结果", "判定结果"
        ]

        for idx in range(min(20, len(df_reset))):
            row = df_reset.iloc[idx]
            row_str = " ".join([str(v).replace("\n", " ") for v in row.values if v])

            for pattern in patterns:
                if pattern.lower() in row_str.lower():
                    return idx

        return None

    def _extract_column_mapping(self, df, header_row_idx: int) -> Dict[str, Optional[int]]:
        """提取列位置映射（DataFrame模式）"""
        mapping = {
            "item_col": None,
            "min_col": None,
            "max_col": None,
            "typ_col": None,
            "data_col": None,
            "result_col": None
        }

        header_row = df.iloc[header_row_idx]

        for col_idx, header_val in enumerate(header_row):
            if header_val is None:
                continue

            header_clean = str(header_val).replace("\n", " ").replace("\r", " ").lower()

            # 测试项列
            if any(k in header_clean for k in ["parts", "部件", "net", "网络", "test points", "测试点"]):
                if mapping["item_col"] is None:
                    mapping["item_col"] = col_idx

            # 最小值列
            if any(k in header_clean for k in ["criteria(min)", "criteria (min)", "最小", "min"]):
                if mapping["min_col"] is None:
                    mapping["min_col"] = col_idx

            # 最大值列
            if any(k in header_clean for k in ["criteria(max)", "criteria (max)", "最大", "max"]):
                if mapping["max_col"] is None:
                    mapping["max_col"] = col_idx

            # 典型值列
            if any(k in header_clean for k in ["criteria(typ)", "criteria (typ)", "典型", "typ"]):
                if mapping["typ_col"] is None:
                    mapping["typ_col"] = col_idx

            # 测试数据列
            if any(k in header_clean for k in ["test data", "实测", "测试数据", "测量"]):
                if mapping["data_col"] is None:
                    mapping["data_col"] = col_idx

            # 测试结果列
            if any(k in header_clean for k in ["test result", "测试结果", "判定结果", "result"]):
                if mapping["result_col"] is None:
                    mapping["result_col"] = col_idx

        # 备用方案
        if mapping["result_col"] is None:
            header_row = df.iloc[header_row_idx]
            for col_idx in range(len(header_row) - 1, -1, -1):
                val = header_row.iloc[col_idx]
                if val and ("result" in str(val).lower() or "结果" in str(val)):
                    mapping["result_col"] = col_idx
                    break

        return mapping

    # ==================== 核心校验算法 ====================

    def _extract_number(self, value: Any) -> Optional[float]:
        """从值中提取数值 - 优化版"""
        if value is None:
            return None

        if isinstance(value, (int, float)):
            if not (value != value):  # 排除 NaN
                return float(value)
            return None

        if isinstance(value, str):
            value = value.strip()

            if not value:
                return None

            try:
                return float(value)
            except ValueError:
                pass

            patterns = [
                r'[-+]?\d*\.?\d+\s*[VAmkMHzKHz℃度%]*',
                r'[-+]?\d*\.?\d+',
            ]

            for pattern in patterns:
                match = re.search(pattern, value)
                if match:
                    num_str = re.sub(r'[^\d.\-+]', '', match.group())
                    if num_str:
                        try:
                            return float(num_str)
                        except ValueError:
                            continue

        return None

    def _is_pass_result(self, result_str: str) -> bool:
        """判断是否为合格结果"""
        pass_patterns = ["PASS", "OK", "合格", "通过", "Y", "YES", "TRUE", "P", "PF"]
        result_upper = result_str.upper().strip()

        for pattern in pass_patterns:
            if pattern.upper() in result_upper:
                return True

        return False

    def _is_fail_result(self, result_str: str) -> bool:
        """判断是否为不合格结果"""
        fail_patterns = ["FAIL", "NG", "FAILED", "不合格", "不通过", "N", "NO", "FALSE", "ERROR", "F"]
        result_upper = result_str.upper().strip()

        for pattern in fail_patterns:
            if pattern.upper() in result_upper:
                return True

        return False

    def _check_logic(
        self,
        sheet_name: str,
        row: int,
        item_name: str,
        data_value: Optional[float],
        min_value: Optional[float],
        max_value: Optional[float],
        is_pass: bool,
        is_fail: bool,
        result_str: str
    ) -> Optional[Dict[str, Any]]:
        """检查测试逻辑 - 核心校验算法"""
        # 测试值为空
        if data_value is None:
            if is_pass or is_fail:
                return {
                    "sheet": sheet_name,
                    "row": row,
                    "item": item_name or "未知测试项",
                    "test_value": "空",
                    "min_value": str(min_value) if min_value is not None else "无",
                    "max_value": str(max_value) if max_value is not None else "无",
                    "result": result_str,
                    "issue_type": "测试值为空",
                    "issue": f"测试值为空，但判定为「{result_str}」"
                }
            return None

        # 没有标准值，无法校验
        if min_value is None and max_value is None:
            return None

        # 既有最小值又有最大值 - 最完整的校验
        if min_value is not None and max_value is not None:
            if min_value <= data_value <= max_value:
                if is_fail:
                    return {
                        "sheet": sheet_name,
                        "row": row,
                        "item": item_name or "未知测试项",
                        "test_value": data_value,
                        "min_value": min_value,
                        "max_value": max_value,
                        "result": result_str,
                        "issue_type": "在范围内但判定不合格",
                        "issue": f"测试值 {data_value} 在标准范围 [{min_value}, {max_value}] 内，但判定为「{result_str}」"
                    }
            else:
                if is_pass:
                    range_desc = ""
                    if data_value < min_value:
                        range_desc = f"低于最小值 {min_value}"
                    else:
                        range_desc = f"超过最大值 {max_value}"

                    return {
                        "sheet": sheet_name,
                        "row": row,
                        "item": item_name or "未知测试项",
                        "test_value": data_value,
                        "min_value": min_value,
                        "max_value": max_value,
                        "result": result_str,
                        "issue_type": "超出范围但判定合格",
                        "issue": f"测试值 {data_value} {range_desc}，但判定为「{result_str}」"
                    }

        # 只有最大值的情况（无下限）
        elif max_value is not None:
            if data_value > max_value:
                if is_pass:
                    return {
                        "sheet": sheet_name,
                        "row": row,
                        "item": item_name or "未知测试项",
                        "test_value": data_value,
                        "min_value": "无下限",
                        "max_value": max_value,
                        "result": result_str,
                        "issue_type": "超最大值但判定合格",
                        "issue": f"测试值 {data_value} 超过最大值 {max_value}，但判定为「{result_str}」"
                    }
            elif is_fail:
                return {
                    "sheet": sheet_name,
                    "row": row,
                    "item": item_name or "未知测试项",
                    "test_value": data_value,
                    "min_value": "无下限",
                    "max_value": max_value,
                    "result": result_str,
                    "issue_type": "在范围内但判定不合格",
                    "issue": f"测试值 {data_value} ≤ 最大值 {max_value}，但判定为「{result_str}」"
                }

        # 只有最小值的情况（无上限）
        elif min_value is not None:
            if data_value < min_value:
                if is_pass:
                    return {
                        "sheet": sheet_name,
                        "row": row,
                        "item": item_name or "未知测试项",
                        "test_value": data_value,
                        "min_value": min_value,
                        "max_value": "无上限",
                        "result": result_str,
                        "issue_type": "低于最小值但判定合格",
                        "issue": f"测试值 {data_value} 低于最小值 {min_value}，但判定为「{result_str}」"
                    }
            elif is_fail:
                return {
                    "sheet": sheet_name,
                    "row": row,
                    "item": item_name or "未知测试项",
                    "test_value": data_value,
                    "min_value": min_value,
                    "max_value": "无上限",
                    "result": result_str,
                    "issue_type": "在范围内但判定不合格",
                    "issue": f"测试值 {data_value} ≥ 最小值 {min_value}，但判定为「{result_str}」"
                }

        return None

    def _check_logic_enhanced(
        self,
        sheet_name: str,
        row: int,
        item_name: str,
        data_value: Optional[float],
        min_value: Optional[float],
        max_value: Optional[float],
        is_pass: bool,
        is_fail: bool,
        result_str: str,
        group_name: str = "",
        criteria_style: str = "standard",
        inequality_operator: Optional[str] = None,
        inequality_threshold: Optional[float] = None,
        ilmt_top: Optional[float] = None,
        has_over_limit_group: bool = False
    ) -> Optional[Dict[str, Any]]:
        """【增强版】检查测试逻辑 - 核心校验算法【支持策略模式】

        支持多种criteria_style：
        - standard: 标准范围校验 [min, max]
        - ripple: 纹波校验 (只有max)
        - timing: 时序校验 (只有min或只有max)
        - deviation: 频偏校验 (范围如[-30, 30])
        - power: 功耗不等式校验 (< 9.0W)
        - current_ilmt: 电流ILmt-top规则 (Imax < ILmt-top)

        Args:
            group_name: 测试组名称
            criteria_style: 标准类型
            inequality_operator: 不等式操作符 (<, <=, >, >=)
            inequality_threshold: 不等式阈值
            ilmt_top: ILmt-top阈值
            has_over_limit_group: 是否有其他组超标（复合测试项优化）
        """
        # 测试值为空
        if data_value is None:
            return None

        # 【优化】复合测试项：如果有其他组超标，则该组正常但整体Fail不算问题
        # 例如：电压正常，但纹波超标导致整体Fail
        if has_over_limit_group and is_fail:
            # 检查该组数据是否在范围内
            is_in_range = True
            if min_value is not None and max_value is not None:
                is_in_range = (min_value <= data_value <= max_value)
            elif max_value is not None:
                is_in_range = (data_value <= max_value)
            elif min_value is not None:
                is_in_range = (data_value >= min_value)

            if is_in_range:
                # 该组数据在范围内，且有其他组超标，不报问题
                logger.debug(f"  复合测试项：该组({group_name})正常，有其他组超标，整体Fail不算问题")
                return None

        # 构建测试项描述
        item_desc = f"{item_name}"
        if group_name:
            item_desc = f"{item_name}（{group_name}）"

        # ==================== 特殊规则校验 ====================

        # 【Case 3】功耗不等式规则
        if criteria_style == "power" and inequality_operator and inequality_threshold:
            return self._check_inequality_logic(
                sheet_name, row, item_desc, data_value,
                inequality_operator, inequality_threshold,
                is_pass, is_fail, result_str
            )

        # 【Case 2】电流ILmt-top规则
        if criteria_style == "current_ilmt" and ilmt_top:
            return self._check_ilmt_top_logic(
                sheet_name, row, item_desc, data_value,
                ilmt_top, is_pass, is_fail, result_str
            )

        # 【Case 4】时序校验（优先使用max_value）
        if criteria_style == "timing":
            # 时序通常只有一边限制
            if max_value is not None:
                # 只有max限制
                in_range = (data_value <= max_value)
                if in_range:
                    if is_fail:
                        return {
                            "sheet": sheet_name, "row": row, "item": item_desc,
                            "test_value": data_value, "min_value": "无下限",
                            "max_value": max_value, "result": result_str,
                            "issue_type": "在范围内但判定不合格",
                            "issue": f"测试值 {data_value} ≤ 最大值 {max_value}，但判定为「{result_str}」"
                        }
                else:
                    if is_pass:
                        return {
                            "sheet": sheet_name, "row": row, "item": item_desc,
                            "test_value": data_value, "min_value": "无下限",
                            "max_value": max_value, "result": result_str,
                            "issue_type": "超最大值但判定合格",
                            "issue": f"测试值 {data_value} > 最大值 {max_value}，但判定为「{result_str}」"
                        }
            elif min_value is not None:
                # 只有min限制
                in_range = (data_value >= min_value)
                if in_range:
                    if is_fail:
                        return {
                            "sheet": sheet_name, "row": row, "item": item_desc,
                            "test_value": data_value, "min_value": min_value,
                            "max_value": "无上限", "result": result_str,
                            "issue_type": "在范围内但判定不合格",
                            "issue": f"测试值 {data_value} ≥ 最小值 {min_value}，但判定为「{result_str}」"
                        }
                else:
                    if is_pass:
                        return {
                            "sheet": sheet_name, "row": row, "item": item_desc,
                            "test_value": data_value, "min_value": min_value,
                            "max_value": "无上限", "result": result_str,
                            "issue_type": "低于最小值但判定合格",
                            "issue": f"测试值 {data_value} < 最小值 {min_value}，但判定为「{result_str}」"
                        }
            return None

        # 【Case 1】纹波校验（只有max）
        if criteria_style == "ripple" and max_value is not None:
            in_range = (data_value <= max_value)
            if in_range:
                if is_fail:
                    return {
                        "sheet": sheet_name, "row": row, "item": item_desc,
                        "test_value": data_value, "min_value": "无下限",
                        "max_value": max_value, "result": result_str,
                        "issue_type": "在范围内但判定不合格",
                        "issue": f"纹波 {data_value} ≤ 限制 {max_value}，但判定为「{result_str}」"
                    }
            else:
                if is_pass:
                    return {
                        "sheet": sheet_name, "row": row, "item": item_desc,
                        "test_value": data_value, "min_value": "无下限",
                        "max_value": max_value, "result": result_str,
                        "issue_type": "超限制但判定合格",
                        "issue": f"纹波 {data_value} > 限制 {max_value}，但判定为「{result_str}」"
                    }
            return None

        # ==================== 标准范围校验 ====================

        # 没有标准值，无法校验
        if min_value is None and max_value is None:
            return None

        # 情况1：既有最小值又有最大值（完整范围）
        if min_value is not None and max_value is not None:
            # 【修复】处理倒置范围：当最小值 > 最大值时自动交换
            actual_min = min(min_value, max_value)
            actual_max = max(min_value, max_value)
            in_range = (actual_min <= data_value <= actual_max)

            if in_range:
                if is_fail:
                    return {
                        "sheet": sheet_name,
                        "row": row,
                        "item": item_desc,
                        "test_value": data_value,
                        "min_value": min_value,
                        "max_value": max_value,
                        "result": result_str,
                        "issue_type": "在范围内但判定不合格",
                        "issue": f"测试值 {data_value} 在标准范围 [{actual_min}, {actual_max}] 内，但判定为「{result_str}」"
                    }
            else:
                if is_pass:
                    if data_value < actual_min:
                        range_desc_specific = f"低于最小值 {actual_min}"
                    else:
                        range_desc_specific = f"超过最大值 {actual_max}"

                    return {
                        "sheet": sheet_name,
                        "row": row,
                        "item": item_desc,
                        "test_value": data_value,
                        "min_value": min_value,
                        "max_value": max_value,
                        "result": result_str,
                        "issue_type": "超出范围但判定合格",
                        "issue": f"测试值 {data_value} {range_desc_specific}，但判定为「{result_str}」"
                    }

        # 情况2：只有最大值（无下限，如纹波 < 100mV）
        elif max_value is not None and min_value is None:
            in_range = (data_value <= max_value)

            if in_range:
                if is_fail:
                    return {
                        "sheet": sheet_name,
                        "row": row,
                        "item": item_desc,
                        "test_value": data_value,
                        "min_value": "无下限",
                        "max_value": max_value,
                        "result": result_str,
                        "issue_type": "在范围内但判定不合格",
                        "issue": f"测试值 {data_value} ≤ 最大值 {max_value}，但判定为「{result_str}」"
                    }
            else:
                if is_pass:
                    return {
                        "sheet": sheet_name,
                        "row": row,
                        "item": item_desc,
                        "test_value": data_value,
                        "min_value": "无下限",
                        "max_value": max_value,
                        "result": result_str,
                        "issue_type": "超最大值但判定合格",
                        "issue": f"测试值 {data_value} > 最大值 {max_value}，但判定为「{result_str}」"
                    }

        # 情况3：只有最小值（无上限，如电压 >= 3.3V）
        elif min_value is not None and max_value is None:
            in_range = (data_value >= min_value)

            if in_range:
                if is_fail:
                    return {
                        "sheet": sheet_name,
                        "row": row,
                        "item": item_desc,
                        "test_value": data_value,
                        "min_value": min_value,
                        "max_value": "无上限",
                        "result": result_str,
                        "issue_type": "在范围内但判定不合格",
                        "issue": f"测试值 {data_value} ≥ 最小值 {min_value}，但判定为「{result_str}」"
                    }
            else:
                if is_pass:
                    return {
                        "sheet": sheet_name,
                        "row": row,
                        "item": item_desc,
                        "test_value": data_value,
                        "min_value": min_value,
                        "max_value": "无上限",
                        "result": result_str,
                        "issue_type": "低于最小值但判定合格",
                        "issue": f"测试值 {data_value} < 最小值 {min_value}，但判定为「{result_str}」"
                    }

        return None

    def _check_inequality_logic(
        self,
        sheet_name: str,
        row: int,
        item_desc: str,
        data_value: float,
        operator: str,
        threshold: float,
        is_pass: bool,
        is_fail: bool,
        result_str: str
    ) -> Optional[Dict[str, Any]]:
        """【Case 3】功耗不等式规则校验

        根据描述中的不等式规则校验测试值
        例如："业务功耗 小于9.0W" -> 规则: value < 9.0
        """
        # 操作符描述映射
        op_desc = {
            "<": "小于",
            "<=": "小于等于",
            ">": "大于",
            ">=": "大于等于"
        }
        op_symbol = op_desc.get(operator, operator)

        # 应用不等式规则
        in_range = self._apply_inequality_rule(data_value, operator, threshold)

        if in_range:
            # 值满足不等式，判定应该是PASS
            if is_fail:
                return {
                    "sheet": sheet_name,
                    "row": row,
                    "item": item_desc,
                    "test_value": data_value,
                    "criteria": f"{op_symbol} {threshold}",
                    "result": result_str,
                    "issue_type": "满足不等式但判定不合格",
                    "issue": f"测试值 {data_value} {op_symbol} {threshold}，但判定为「{result_str}」"
                }
        else:
            # 值不满足不等式，判定应该是FAIL
            if is_pass:
                return {
                    "sheet": sheet_name,
                    "row": row,
                    "item": item_desc,
                    "test_value": data_value,
                    "criteria": f"{op_symbol} {threshold}",
                    "result": result_str,
                    "issue_type": "不满足不等式但判定合格",
                    "issue": f"测试值 {data_value} 不满足 {op_symbol} {threshold}，但判定为「{result_str}」"
                }

        return None

    def _check_ilmt_top_logic(
        self,
        sheet_name: str,
        row: int,
        item_desc: str,
        data_value: float,
        ilmt_top: float,
        is_pass: bool,
        is_fail: bool,
        result_str: str
    ) -> Optional[Dict[str, Any]]:
        """【Case 2】电流ILmt-top规则校验

        规则：Imax < ILmt-top，即实测电流值应该小于ILmt-top阈值
        如果实测值 >= ILmt-top，则判定为FAIL
        """
        # 电流小于ILmt-top才是合规
        in_range = (data_value < ilmt_top)

        if in_range:
            # 实测值 < ILmt-top，判定应该是PASS
            if is_fail:
                return {
                    "sheet": sheet_name,
                    "row": row,
                    "item": item_desc,
                    "test_value": data_value,
                    "criteria": f"< ILmt-top({ilmt_top})",
                    "result": result_str,
                    "issue_type": "电流合规但判定不合格",
                    "issue": f"测试电流 {data_value}mA < ILmt-top({ilmt_top}mA)，但判定为「{result_str}」"
                }
        else:
            # 实测值 >= ILmt-top，超出限制
            if is_pass:
                return {
                    "sheet": sheet_name,
                    "row": row,
                    "item": item_desc,
                    "test_value": data_value,
                    "criteria": f"< ILmt-top({ilmt_top})",
                    "result": result_str,
                    "issue_type": "电流超限但判定合格",
                    "issue": f"测试电流 {data_value}mA ≥ ILmt-top({ilmt_top}mA)，超出限制但判定为「{result_str}」"
                }

        return None

    def _get_range_description(
        self,
        min_value: Optional[float],
        max_value: Optional[float]
    ) -> str:
        """获取标准范围描述"""
        if min_value is not None and max_value is not None:
            return f"[{min_value}, {max_value}]"
        elif max_value is not None:
            return f"≤ {max_value}"
        elif min_value is not None:
            return f"≥ {min_value}"
        else:
            return "未知"

    # ==================== 测试项目完整性 ====================

    def extract_test_items(self, sheets_data: Dict[str, Any]) -> Dict[str, Any]:
        """提取测试项目"""
        extracted_items = []

        for sheet_name, df in sheets_data.items():
            if "Test Summary" in sheet_name:
                for idx, row in df.iterrows():
                    if idx < 2:
                        continue
                    if len(row) > 2 and row.iloc[2]:
                        item_name = str(row.iloc[2]).strip()
                        if item_name and item_name not in ["nan", "None"]:
                            extracted_items.append({
                                "name": item_name.replace("\n", " "),
                                "row": idx + 1,
                                "sheet": sheet_name
                            })

        return {
            "total": len(extracted_items),
            "items": extracted_items
        }

    def validate_test_completeness(
        self,
        sheets_data: Dict[str, Any],
        ai_classification: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """验证测试项目完整性"""
        enable_check = getattr(self.rules, 'enable_completeness_check', False)
        standard_items = self.rules.standard_test_items

        if not enable_check or not standard_items:
            extracted = self.extract_test_items(sheets_data)
            return {
                "is_complete": True,
                "extracted_items_count": extracted["total"],
                "message": "测试项完整性检查已禁用或未配置标准测试项",
                "test_items": extracted["items"][:20] if extracted["items"] else [],
                "note": "如需启用完整性检查，请在配置文件中设置 standard_test_items"
            }

        found_items = set()

        for sheet_name, df in sheets_data.items():
            if "Test Summary" in sheet_name:
                for idx, row in df.iterrows():
                    if idx < 2:
                        continue
                    if len(row) > 2 and row.iloc[2]:
                        item_name = str(row.iloc[2]).strip()
                        if item_name and item_name not in ["nan", "None"]:
                            found_items.add(item_name)

        missing_items = []

        for category, items in standard_items.items():
            for item in items:
                found = False
                for found_item in found_items:
                    if item in found_item or found_item in item:
                        found = True
                        break

                if not found:
                    missing_items.append({
                        "category": category,
                        "item": item,
                        "status": "未找到"
                    })

        return {
            "is_complete": len(missing_items) == 0,
            "found_items": list(found_items),
            "missing_items": missing_items,
            "message": f"发现 {len(found_items)} 个测试项，{len(missing_items)} 个缺失"
        }


# 创建验证服务实例
validation_service = ValidationService()
