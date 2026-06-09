# -*- coding: utf-8 -*-
"""
硬件研发部测试报告审核平台 - 分析服务
实现测数聚合与统计分析功能：
- 批量数据聚合
- 统计指标计算
- 可视化图表生成
"""

import json
import io
from typing import Any, Dict, List, Optional
from pathlib import Path

import pandas as pd
import numpy as np

from app.core import get_logger, get_settings, AnalysisException, NoDataFoundException
from app.services.excel_parser import ExcelParser

logger = get_logger("analysis_service")


class AnalysisService:
    """分析服务

    提供数据分析和可视化功能：
    1. 批量数据聚合
    2. 统计指标计算
    3. 图表生成
    """

    def __init__(self):
        self.config = get_settings()
        self.analysis_config = self.config.analysis

    def aggregate_data(
        self,
        files: List[bytes],
        filenames: List[str]
    ) -> Dict[str, Any]:
        """聚合多个测试报告的数据

        Args:
            files: Excel 文件字节列表
            filenames: 文件名列表

        Returns:
            聚合后的数据
        """
        all_data = []
        aggregation_fields = self.analysis_config.aggregation_fields
        value_fields = self.analysis_config.value_fields

        for file_bytes, filename in zip(files, filenames):
            try:
                parser = ExcelParser()
                parser.load_from_bytes(file_bytes, filename)

                # 读取所有 Sheet
                sheets_data = parser.read_all_sheets()

                for sheet_name, df in sheets_data.items():
                    if df is None or df.empty:
                        continue

                    # 查找聚合字段和数值字段
                    columns = df.columns.tolist()

                    agg_col = self._find_best_match(columns, aggregation_fields)
                    value_col = self._find_best_match(columns, value_fields)

                    if value_col is None:
                        continue

                    # 提取数据
                    for idx, row in df.iterrows():
                        value = row.get(value_col)

                        # 转换为数值
                        try:
                            if isinstance(value, str):
                                numeric_match = self._extract_number(value)
                                if numeric_match:
                                    value = float(numeric_match)
                                else:
                                    continue
                            else:
                                value = float(value)
                        except (ValueError, TypeError):
                            continue

                        # 获取测试项名称
                        item_name = "未知测试项"
                        if agg_col:
                            item_name = str(row.get(agg_col, "未知测试项")).strip()
                            if not item_name or item_name == "nan":
                                item_name = "未知测试项"

                        all_data.append({
                            "file": filename,
                            "sheet": sheet_name,
                            "row": idx + 1,
                            "test_item": item_name,
                            "value": value
                        })

                parser.close()
                logger.info(f"处理文件 {filename}，提取数据 {len(all_data)} 条")

            except Exception as e:
                logger.error(f"处理文件 {filename} 失败: {str(e)}")
                continue

        if not all_data:
            raise NoDataFoundException("未从文件中提取到有效数据")

        # 聚合数据
        aggregated = self._aggregate_by_item(all_data)

        return {
            "total_records": len(all_data),
            "files_processed": len(filenames),
            "aggregated_data": aggregated
        }

    def _aggregate_by_item(
        self,
        data: List[Dict[str, Any]]
    ) -> Dict[str, List[float]]:
        """按测试项聚合数据"""
        aggregated = {}

        for record in data:
            item_name = record.get("test_item", "未知测试项")

            if item_name not in aggregated:
                aggregated[item_name] = []

            aggregated[item_name].append(record["value"])

        return aggregated

    def calculate_statistics(
        self,
        aggregated_data: Dict[str, List[float]]
    ) -> Dict[str, Any]:
        """计算统计指标

        Args:
            aggregated_data: 聚合后的数据

        Returns:
            统计结果
        """
        statistics_fields = self.analysis_config.statistics
        results = {}

        for item_name, values in aggregated_data.items():
            if not values or len(values) == 0:
                continue

            values_array = np.array(values)

            item_stats = {}

            # 计算各项统计指标
            if "max" in statistics_fields:
                item_stats["max"] = float(np.max(values_array))

            if "min" in statistics_fields:
                item_stats["min"] = float(np.min(values_array))

            if "mean" in statistics_fields:
                item_stats["mean"] = float(np.mean(values_array))

            if "std" in statistics_fields:
                item_stats["std"] = float(np.std(values_array))

            if "median" in statistics_fields:
                item_stats["median"] = float(np.median(values_array))

            # 样本数
            item_stats["count"] = len(values)

            results[item_name] = item_stats

        return results

    def generate_charts(
        self,
        aggregated_data: Dict[str, List[float]],
        statistics: Dict[str, Any],
        chart_types: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """生成可视化图表

        使用 Plotly 生成交互式图表

        Args:
            aggregated_data: 聚合数据
            statistics: 统计结果
            chart_types: 图表类型列表

        Returns:
            图表配置 JSON
        """
        if chart_types is None:
            chart_types = ["histogram", "scatter", "box"]

        charts = {}

        # 尝试导入 plotly
        try:
            import plotly.graph_objects as go
            from plotly.subplots import make_subplots
        except ImportError:
            logger.warning("Plotly 未安装，返回空图表")
            return charts

        # 1. 直方图（正态分布）
        if "histogram" in chart_types:
            charts["histogram"] = self._create_histogram(aggregated_data)

        # 2. 散点图（离散点图）
        if "scatter" in chart_types:
            charts["scatter"] = self._create_scatter(aggregated_data)

        # 3. 箱线图
        if "box" in chart_types:
            charts["box"] = self._create_boxplot(aggregated_data)

        # 4. 趋势图
        charts["trend"] = self._create_trend_chart(aggregated_data)

        logger.info(f"生成图表 {len(charts)} 个")

        return charts

    def _create_histogram(self, data: Dict[str, List[float]]) -> Dict[str, Any]:
        """创建直方图（正态分布图）"""
        try:
            import plotly.graph_objects as go

            fig = go.Figure()

            for item_name, values in data.items():
                if len(values) < 2:
                    continue

                fig.add_trace(go.Histogram(
                    x=values,
                    name=item_name,
                    opacity=0.75,
                    histnorm='probability'
                ))

            fig.update_layout(
                title="数据分布直方图",
                xaxis_title="测试值",
                yaxis_title="概率",
                barmode='overlay',
                template="plotly_white",
                height=500
            )

            return json.loads(fig.to_json())

        except Exception as e:
            logger.error(f"创建直方图失败: {str(e)}")
            return {}

    def _create_scatter(self, data: Dict[str, List[float]]) -> Dict[str, Any]:
        """创建散点图"""
        try:
            import plotly.graph_objects as go

            fig = go.Figure()

            for idx, (item_name, values) in enumerate(data.items()):
                if len(values) < 1:
                    continue

                x_values = list(range(1, len(values) + 1))

                fig.add_trace(go.Scatter(
                    x=x_values,
                    y=values,
                    mode='markers',
                    name=item_name,
                    marker=dict(size=10, opacity=0.7)
                ))

            fig.update_layout(
                title="数据离散点图",
                xaxis_title="样本序号",
                yaxis_title="测试值",
                template="plotly_white",
                height=500
            )

            return json.loads(fig.to_json())

        except Exception as e:
            logger.error(f"创建散点图失败: {str(e)}")
            return {}

    def _create_boxplot(self, data: Dict[str, List[float]]) -> Dict[str, Any]:
        """创建箱线图"""
        try:
            import plotly.graph_objects as go

            fig = go.Figure()

            box_data = []
            item_names = []

            for item_name, values in data.items():
                if len(values) < 1:
                    continue
                box_data.append(values)
                item_names.append(item_name)

            fig.add_trace(go.Box(
                y=box_data,
                name=item_names,
                boxpoints='all',
                jitter=0.3,
                pointpos=-1.8
            ))

            fig.update_layout(
                title="数据箱线图",
                yaxis_title="测试值",
                template="plotly_white",
                height=500
            )

            return json.loads(fig.to_json())

        except Exception as e:
            logger.error(f"创建箱线图失败: {str(e)}")
            return {}

    def _create_trend_chart(self, data: Dict[str, List[float]]) -> Dict[str, Any]:
        """创建趋势图"""
        try:
            import plotly.graph_objects as go

            fig = go.Figure()

            # 为每个测试项创建趋势线
            for item_name, values in data.items():
                if len(values) < 2:
                    continue

                # 计算移动平均
                window = min(5, len(values))
                moving_avg = pd.Series(values).rolling(window=window, min_periods=1).mean().tolist()

                fig.add_trace(go.Scatter(
                    y=moving_avg,
                    mode='lines+markers',
                    name=f"{item_name} (趋势)"
                ))

                fig.add_trace(go.Scatter(
                    y=list(values),
                    mode='markers',
                    name=item_name,
                    marker=dict(size=6, opacity=0.5)
                ))

            fig.update_layout(
                title="数据趋势图",
                xaxis_title="样本序号",
                yaxis_title="测试值",
                template="plotly_white",
                height=500
            )

            return json.loads(fig.to_json())

        except Exception as e:
            logger.error(f"创建趋势图失败: {str(e)}")
            return {}

    def _find_best_match(
        self,
        columns: List[str],
        keywords: List[str]
    ) -> Optional[str]:
        """查找最佳匹配的列"""
        for col in columns:
            if col is None:
                continue
            col_str = str(col).strip()
            for keyword in keywords:
                if keyword in col_str:
                    return col
        return None

    def _extract_number(self, text: str) -> Optional[str]:
        """从文本中提取数字"""
        import re
        match = re.search(r'[-+]?\d*\.?\d+', text)
        if match:
            return match.group()
        return None


# 创建分析服务实例
analysis_service = AnalysisService()
