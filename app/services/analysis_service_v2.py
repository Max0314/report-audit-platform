# -*- coding: utf-8 -*-
"""
硬件研发部测试报告审核平台 - 分析服务（可扩展版）
实现测数聚合与统计分析功能，支持模块化扩展：

【架构说明】
├── DataAggregator       数据聚合层
│   ├── BaseAggregator  聚合基类
│   ├── SheetAggregator 按Sheet聚合
│   └── ItemAggregator  按测试项聚合（默认）
│
├── StatisticCalculator  统计计算层
│   ├── BaseCalculator   计算器基类
│   ├── BasicStats       基础统计（均值、标准差、最值）
│   └── AdvancedStats    高级统计（预留）
│
├── ChartGenerator       图表生成层
│   ├── BaseChart        图表基类
│   ├── HistogramChart   直方图
│   ├── ScatterChart     散点图
│   ├── BoxPlotChart     箱线图
│   └── TrendChart       趋势图
│
└── AnalysisPipeline     分析流水线
    └── 组装并执行完整流程

【扩展指南】
1. 新聚合策略 → 继承 BaseAggregator
2. 新统计指标 → 继承 BaseCalculator
3. 新图表类型 → 继承 BaseChart
4. 新分析流程 → 继承 AnalysisPipeline 或使用 PipelineBuilder

更多详情请参考 docs/EXTENSION_GUIDE.md
"""

import json
import io
from typing import Any, Dict, List, Optional, Callable, Type
from pathlib import Path
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto

import pandas as pd
import numpy as np

from app.core import get_logger, get_settings, AnalysisException, NoDataFoundException
from app.services.excel_parser import ExcelParser

logger = get_logger("analysis_service")

# ============ 数据模型 ============


class AnalysisStage(Enum):
    """分析阶段"""
    AGGREGATION = auto()
    STATISTICS = auto()
    VISUALIZATION = auto()
    COMPLETED = auto()


@dataclass
class AnalysisResult:
    """分析结果容器"""
    task_id: str
    status: str
    summary: Dict[str, Any] = field(default_factory=dict)
    statistics: Dict[str, Any] = field(default_factory=dict)
    charts: Dict[str, Any] = field(default_factory=dict)
    raw_data: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ============ 数据聚合层 ============


class BaseAggregator(ABC):
    """数据聚合基类"""

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}

    @abstractmethod
    def aggregate(
        self,
        files: List[bytes],
        filenames: List[str]
    ) -> Dict[str, Any]:
        """执行聚合操作"""
        pass

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


class ItemAggregator(BaseAggregator):
    """按测试项聚合（默认策略）"""

    def aggregate(
        self,
        files: List[bytes],
        filenames: List[str]
    ) -> Dict[str, Any]:
        """按测试项聚合数据"""
        all_data = []
        aggregation_fields = self.config.get("aggregation_fields", [])
        value_fields = self.config.get("value_fields", [])

        for file_bytes, filename in zip(files, filenames):
            try:
                parser = ExcelParser()
                parser.load_from_bytes(file_bytes, filename)

                sheets_data = parser.read_all_sheets()

                for sheet_name, df in sheets_data.items():
                    if df is None or df.empty:
                        continue

                    columns = df.columns.tolist()
                    agg_col = self._find_best_match(columns, aggregation_fields)
                    value_col = self._find_best_match(columns, value_fields)

                    if value_col is None:
                        continue

                    for idx, row in df.iterrows():
                        value = row.get(value_col)

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
                logger.info(f"[ItemAggregator] 处理文件 {filename}，提取数据 {len(all_data)} 条")

            except Exception as e:
                logger.error(f"[ItemAggregator] 处理文件 {filename} 失败: {str(e)}")
                continue

        if not all_data:
            raise NoDataFoundException("未从文件中提取到有效数据")

        aggregated = {}
        for record in all_data:
            item_name = record.get("test_item", "未知测试项")
            if item_name not in aggregated:
                aggregated[item_name] = []
            aggregated[item_name].append(record["value"])

        return {
            "total_records": len(all_data),
            "files_processed": len(filenames),
            "aggregated_data": aggregated,
            "raw_records": all_data
        }


class SheetAggregator(BaseAggregator):
    """按Sheet聚合（预留策略）"""

    def aggregate(
        self,
        files: List[bytes],
        filenames: List[str]
    ) -> Dict[str, Any]:
        """按Sheet聚合数据（预留实现）"""
        logger.warning("[SheetAggregator] 按Sheet聚合功能正在开发中...")
        return {"aggregated_data": {}}


# ============ 统计计算层 ============


class BaseCalculator(ABC):
    """统计计算器基类"""

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}

    @abstractmethod
    def calculate(
        self,
        aggregated_data: Dict[str, List[float]]
    ) -> Dict[str, Any]:
        """执行统计计算"""
        pass


class BasicStatsCalculator(BaseCalculator):
    """基础统计计算器"""

    def calculate(
        self,
        aggregated_data: Dict[str, List[float]]
    ) -> Dict[str, Any]:
        """计算基础统计指标：max, min, mean, std, median, count"""
        statistics_fields = self.config.get("statistics_fields", [])
        results = {}

        for item_name, values in aggregated_data.items():
            if not values or len(values) == 0:
                continue

            values_array = np.array(values)
            item_stats = {}

            if "max" in statistics_fields or not statistics_fields:
                item_stats["max"] = float(np.max(values_array))
            if "min" in statistics_fields or not statistics_fields:
                item_stats["min"] = float(np.min(values_array))
            if "mean" in statistics_fields or not statistics_fields:
                item_stats["mean"] = float(np.mean(values_array))
            if "std" in statistics_fields or not statistics_fields:
                item_stats["std"] = float(np.std(values_array))
            if "median" in statistics_fields or not statistics_fields:
                item_stats["median"] = float(np.median(values_array))

            item_stats["count"] = len(values)
            results[item_name] = item_stats

        logger.info(f"[BasicStatsCalculator] 计算完成 {len(results)} 个测试项")
        return results


class AdvancedStatsCalculator(BaseCalculator):
    """高级统计计算器（预留）"""

    def calculate(
        self,
        aggregated_data: Dict[str, List[float]]
    ) -> Dict[str, Any]:
        """计算高级统计指标（预留）"""
        logger.warning("[AdvancedStatsCalculator] 高级统计功能正在开发中...")
        return {}


# ============ 图表生成层 ============


class BaseChart(ABC):
    """图表基类"""

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}

    @abstractmethod
    def generate(
        self,
        aggregated_data: Dict[str, List[float]],
        statistics: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """生成图表"""
        pass


class HistogramChart(BaseChart):
    """直方图"""

    def generate(
        self,
        aggregated_data: Dict[str, List[float]],
        statistics: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        try:
            import plotly.graph_objects as go
            fig = go.Figure()

            for item_name, values in aggregated_data.items():
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
            logger.error(f"[HistogramChart] 生成失败: {str(e)}")
            return None


class ScatterChart(BaseChart):
    """散点图"""

    def generate(
        self,
        aggregated_data: Dict[str, List[float]],
        statistics: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        try:
            import plotly.graph_objects as go
            fig = go.Figure()

            for idx, (item_name, values) in enumerate(aggregated_data.items()):
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
            logger.error(f"[ScatterChart] 生成失败: {str(e)}")
            return None


class BoxPlotChart(BaseChart):
    """箱线图"""

    def generate(
        self,
        aggregated_data: Dict[str, List[float]],
        statistics: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        try:
            import plotly.graph_objects as go
            fig = go.Figure()

            for item_name, values in aggregated_data.items():
                if len(values) < 1:
                    continue
                fig.add_trace(go.Box(
                    y=values,
                    name=item_name,
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
            logger.error(f"[BoxPlotChart] 生成失败: {str(e)}")
            return None


class TrendChart(BaseChart):
    """趋势图"""

    def generate(
        self,
        aggregated_data: Dict[str, List[float]],
        statistics: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        try:
            import plotly.graph_objects as go
            fig = go.Figure()

            for item_name, values in aggregated_data.items():
                if len(values) < 2:
                    continue

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
            logger.error(f"[TrendChart] 生成失败: {str(e)}")
            return None


# ============ 分析流水线 ============


class AnalysisPipeline:
    """分析流水线"""

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.settings = get_settings()
        self.analysis_config = self.settings.analysis

        self.aggregator: Optional[BaseAggregator] = None
        self.calculator: Optional[BaseCalculator] = None
        self.charts: List[BaseChart] = []
        self.hooks: Dict[AnalysisStage, List[Callable]] = {}

        self._init_default_pipeline()

    def _init_default_pipeline(self):
        """初始化默认流水线"""
        agg_config = {
            "aggregation_fields": self.analysis_config.aggregation_fields,
            "value_fields": self.analysis_config.value_fields
        }
        self.aggregator = ItemAggregator(agg_config)

        stat_config = {
            "statistics_fields": self.analysis_config.statistics
        }
        self.calculator = BasicStatsCalculator(stat_config)

        self.charts = [
            HistogramChart(),
            ScatterChart(),
            BoxPlotChart(),
            TrendChart()
        ]

    def set_aggregator(self, aggregator: BaseAggregator) -> 'AnalysisPipeline':
        """设置数据聚合器"""
        self.aggregator = aggregator
        return self

    def set_calculator(self, calculator: BaseCalculator) -> 'AnalysisPipeline':
        """设置统计计算器"""
        self.calculator = calculator
        return self

    def add_chart(self, chart: BaseChart) -> 'AnalysisPipeline':
        """添加图表生成器"""
        self.charts.append(chart)
        return self

    def clear_charts(self) -> 'AnalysisPipeline':
        """清空图表生成器"""
        self.charts = []
        return self

    def add_hook(self, stage: AnalysisStage, hook: Callable) -> 'AnalysisPipeline':
        """添加分析阶段钩子"""
        if stage not in self.hooks:
            self.hooks[stage] = []
        self.hooks[stage].append(hook)
        return self

    def _run_hooks(self, stage: AnalysisStage, data: Any) -> Any:
        """运行指定阶段的钩子"""
        if stage not in self.hooks:
            return data
        for hook in self.hooks[stage]:
            try:
                data = hook(data)
            except Exception as e:
                logger.warning(f"钩子执行失败: {str(e)}")
        return data

    def run(
        self,
        files: List[bytes],
        filenames: List[str],
        task_id: Optional[str] = None
    ) -> AnalysisResult:
        """执行完整分析流水线"""
        if not self.aggregator:
            raise ValueError("未设置数据聚合器")
        if not self.calculator:
            raise ValueError("未设置统计计算器")

        result = AnalysisResult(
            task_id=task_id or f"analysis_{id(self)}",
            status="processing"
        )

        try:
            # 阶段1: 数据聚合
            logger.info(f"[Pipeline] 开始数据聚合...")
            agg_result = self.aggregator.aggregate(files, filenames)
            agg_result = self._run_hooks(AnalysisStage.AGGREGATION, agg_result)

            result.raw_data = agg_result["raw_records"]
            result.summary["total_records"] = agg_result["total_records"]
            result.summary["files_processed"] = agg_result["files_processed"]

            # 阶段2: 统计计算
            logger.info(f"[Pipeline] 开始统计计算...")
            stats = self.calculator.calculate(agg_result["aggregated_data"])
            stats = self._run_hooks(AnalysisStage.STATISTICS, stats)

            result.statistics = stats
            result.summary["unique_test_items"] = len(stats)

            # 阶段3: 图表生成
            logger.info(f"[Pipeline] 开始图表生成...")
            charts = {}
            for chart in self.charts:
                chart_name = chart.__class__.__name__.lower().replace("chart", "")
                chart_data = chart.generate(agg_result["aggregated_data"], stats)
                if chart_data:
                    charts[chart_name] = chart_data
            charts = self._run_hooks(AnalysisStage.VISUALIZATION, charts)
            result.charts = charts

            # 完成
            result.status = "completed"
            result = self._run_hooks(AnalysisStage.COMPLETED, result)

            logger.info(f"[Pipeline] 分析完成: {result.task_id}")
            return result

        except Exception as e:
            result.status = "error"
            result.metadata["error"] = str(e)
            logger.error(f"[Pipeline] 分析失败: {str(e)}")
            raise


# ============ 工厂类（向后兼容） ============


class AnalysisService:
    """分析服务（向后兼容接口）"""

    def __init__(self):
        self.config = get_settings()
        self.analysis_config = self.config.analysis
        self.pipeline = AnalysisPipeline()

    def aggregate_data(
        self,
        files: List[bytes],
        filenames: List[str]
    ) -> Dict[str, Any]:
        """[旧接口] 聚合数据"""
        return self.pipeline.aggregator.aggregate(files, filenames)

    def calculate_statistics(
        self,
        aggregated_data: Dict[str, List[float]]
    ) -> Dict[str, Any]:
        """[旧接口] 计算统计"""
        return self.pipeline.calculator.calculate(aggregated_data)

    def generate_charts(
        self,
        aggregated_data: Dict[str, List[float]],
        statistics: Dict[str, Any],
        chart_types: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """[旧接口] 生成图表"""
        charts = {}
        for chart in self.pipeline.charts:
            chart_name = chart.__class__.__name__.lower().replace("chart", "")
            if chart_types and chart_name not in chart_types:
                continue
            chart_data = chart.generate(aggregated_data, statistics)
            if chart_data:
                charts[chart_name] = chart_data
        return charts


# 创建默认实例
analysis_service = AnalysisService()
analysis_pipeline = AnalysisPipeline()

# ============ 扩展注册（预留） ============

EXTENSION_REGISTRY = {
    "aggregators": {},
    "calculators": {},
    "charts": {}
}


def register_extension(type_: str, name: str, cls: Type):
    """注册扩展"""
    if type_ not in EXTENSION_REGISTRY:
        EXTENSION_REGISTRY[type_] = {}
    EXTENSION_REGISTRY[type_][name] = cls
    logger.info(f"注册扩展: {type_}.{name}")


def get_extension(type_: str, name: str):
    """获取扩展"""
    if type_ not in EXTENSION_REGISTRY:
        return None
    return EXTENSION_REGISTRY[type_].get(name)
