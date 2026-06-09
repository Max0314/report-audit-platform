# -*- coding: utf-8 -*-
"""
示例扩展：添加CPK计算能力
演示如何添加新的统计指标和图表
"""

import numpy as np
import json

from app.services.analysis_service_v2 import (
    BaseCalculator,
    BaseChart
)


class CPKCalculator(BaseCalculator):
    """CPK制程能力指数计算器

    CPK = min(USL-μ, μ-LSL) / (3σ)
    """

    def __init__(self, config=None):
        super().__init__(config)
        self.usl = config.get("usl", None)  # 上规格限
        self.lsl = config.get("lsl", None)  # 下规格限

    def calculate(self, aggregated_data):
        results = {}

        for item_name, values in aggregated_data.items():
            if not values or len(values) < 10:
                continue

            values_array = np.array(values)
            mean = np.mean(values_array)
            std = np.std(values_array)

            item_stats = {
                "mean": float(mean),
                "std": float(std),
                "count": len(values)
            }

            # 计算CPK
            if self.usl is not None and self.lsl is not None:
                cpu = (self.usl - mean) / (3 * std) if std > 0 else 0
                cpl = (mean - self.lsl) / (3 * std) if std > 0 else 0
                cpk = min(cpu, cpl)
                item_stats["cpk"] = float(cpk)
                item_stats["cpu"] = float(cpu)
                item_stats["cpl"] = float(cpl)

            results[item_name] = item_stats

        return results


class ControlChart(BaseChart):
    """SPC控制图"""

    def generate(self, aggregated_data, statistics):
        try:
            import plotly.graph_objects as go

            fig = go.Figure()

            for item_name, values in aggregated_data.items():
                if item_name not in statistics:
                    continue

                stats = statistics[item_name]

                # 绘制数据点
                fig.add_trace(go.Scatter(
                    y=values,
                    mode='lines+markers',
                    name=item_name
                ))

                # 绘制控制限
                if "cpu" in stats and "cpl" in stats:
                    mean_val = stats.get("mean", 0)
                    std_val = stats.get("std", 1)
                    ucl = mean_val + 3 * std_val
                    lcl = mean_val - 3 * std_val

                    fig.add_hline(y=ucl, line_dash="dash", line_color="red",
                                  annotation_text=f"UCL={ucl:.2f}")
                    fig.add_hline(y=lcl, line_dash="dash", line_color="red",
                                  annotation_text=f"LCL={lcl:.2f}")
                    fig.add_hline(y=mean_val, line_dash="solid", line_color="green",
                                  annotation_text=f"CL={mean_val:.2f}")

            fig.update_layout(
                title="SPC控制图",
                yaxis_title="测试值",
                template="plotly_white",
                height=500
            )

            return json.loads(fig.to_json())

        except Exception as e:
            import logging
            logger = logging.getLogger("ControlChart")
            logger.error(f"生成失败: {str(e)}")
            return None


# ================== 使用示例 ==================
#
# 在 app/routers/analysis.py 中使用：
#
# from app.extensions.example_cpk import CPKCalculator, ControlChart
#
# pipeline = AnalysisPipeline()
# pipeline.set_calculator(CPKCalculator({"usl": 10, "lsl": 0}))
# pipeline.add_chart(ControlChart())
#
