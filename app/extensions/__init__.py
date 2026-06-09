# 扩展模块
# 本目录存放自定义扩展代码

# 示例：如何添加自定义扩展
#
# 1. 在本目录下创建文件，例如 my_aggregator.py
#
# 2. 在 app/routers/analysis.py 中导入并使用
#
# from app.extensions.my_aggregator import MyAggregator
#
# 在 pipeline 配置处添加：
# pipeline.set_aggregator(MyAggregator())
#
