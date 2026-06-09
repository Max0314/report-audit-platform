# -*- coding: utf-8 -*-
"""
硬件研发部测试报告审核平台 - AI 检查服务
集成 DeepSeek API 实现错别字和语义检测
"""

import json
import re
from typing import Any, Dict, List, Optional
from openai import OpenAI

from app.core import get_logger, get_settings, AIException, AIServiceUnavailableException

logger = get_logger("ai_checker")


class AIChecker:
    """AI 检查器

    支持 DeepSeek 和阿里云百炼 API 实现文本检查功能：
    - 错别字检测
    - 专业术语检查
    - 语义一致性检查
    """

    def __init__(self):
        settings = get_settings()
        self.config = settings.ai_service
        self.client: Optional[OpenAI] = None

        # 初始化 OpenAI 客户端
        if self.config.api_key:
            self.client = OpenAI(
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                timeout=self.config.timeout
            )
            logger.info(f"AI 检查器初始化成功 - Provider: {self.config.provider}")
        else:
            logger.warning("未配置 API Key，AI 检查功能将不可用")

    def is_available(self) -> bool:
        """检查 AI 服务是否可用"""
        return self.client is not None

    def check_text(
        self,
        text_content: Dict[str, List[Dict[str, Any]]],
        check_types: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """检查文本内容 - 性能优化版

        Args:
            text_content: 文本内容字典 {sheet_name: [cells]}
            check_types: 检查类型列表 ["spell", "punctuation", "terminology"]

        Returns:
            检查结果
        """
        if not self.is_available():
            raise AIServiceUnavailableException("AI 服务未配置，请先设置 DeepSeek API Key")

        if check_types is None:
            check_types = ["spell", "terminology"]

        text_data = []
        for sheet_name, cells in text_content.items():
            for cell in cells:
                content = cell["value"]
                if len(content) < 3:
                    continue
                if content.strip() in ["Pass", "Fail", "PASS", "FAIL"]:
                    continue
                if any(k in content for k in ["CORP.NAME", "公司名称", "ADD", "地址", "TEL", "电话"]):
                    continue
                text_data.append({
                    "sheet": sheet_name,
                    "row": cell["row"],
                    "col": cell["col"],
                    "coordinate": cell.get("coordinate", ""),
                    "content": content
                })

        if not text_data:
            return {
                "total_checked": 0,
                "issues": [],
                "summary": "未找到需要检查的文本内容"
            }

        max_items = 50
        check_data = text_data[:max_items]

        prompt = self._build_prompt(check_data, check_types)

        try:
            # 构建API调用参数
            api_kwargs = {
                "model": self.config.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "你是硬件测试报告审核助手。只检查明显的错别字和专业术语错误。忽略空格、标点符号等格式问题。技术术语(V、A、W、mV等)不要误判。"
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens
            }
            
            # 如果启用深度思考，添加额外参数
            if self.config.enable_thinking:
                api_kwargs["extra_body"] = {"enable_thinking": True}
            
            response = self.client.chat.completions.create(**api_kwargs)

            result = response.choices[0].message.content
            parsed_result = self._parse_result(result)

            logger.info(f"AI 检查完成，检查文本数: {len(check_data)}, 发现问题数: {len(parsed_result.get('issues', []))}")

            return {
                "total_checked": len(check_data),
                "issues": parsed_result.get("issues", []),
                "summary": parsed_result.get("summary", "")
            }

        except Exception as e:
            logger.error(f"AI 检查失败: {str(e)}")
            raise AIException(f"AI 检查调用失败: {str(e)}")

    def _build_prompt(
        self,
        text_data: List[Dict[str, Any]],
        check_types: List[str]
    ) -> str:
        """构建检查提示词 - 优化版"""
        max_items = 100
        check_data = text_data[:max_items]

        prompt_parts = [
            "请检查以下硬件测试报告中的文本内容：\n"
        ]

        check_type_desc = {
            "spell": "错别字",
            "punctuation": "标点符号",
            "terminology": "专业术语"
        }

        types_str = "、".join([check_type_desc.get(t, t) for t in check_types])
        prompt_parts.append(f"检查类型：{types_str}\n\n")

        prompt_parts.append("待检查内容：\n")
        prompt_parts.append("```\n")

        for idx, item in enumerate(check_data):
            prompt_parts.append(
                f"{idx + 1}. [Sheet: {item['sheet']}, 位置: {item['coordinate']}] {item['content']}\n"
            )

        prompt_parts.append("```\n\n")

        prompt_parts.append(
            "请以 JSON 格式输出检查结果，格式如下：\n"
            "```json\n"
            "{\n"
            '  "issues": [\n'
            '    {"type": "错别字/术语错误", "sheet": "Sheet名", "coordinate": "A1", "original": "原文", "suggestion": "修正建议", "reason": "原因说明"},\n'
            '    ...\n'
            '  ],\n'
            '  "summary": "总体检查总结"\n'
            "}\n"
            "```\n\n"
            "重要规则：\n"
            "1. 只检查明显的错别字（如'测试'写成'侧试'）\n"
            "2. 只检查专业术语错误（如'电压'写成'电圧'）\n"
            "3. 忽略以下问题：空格、标点符号、大小写、格式问题\n"
            "4. 技术术语（V、A、W、mV、MHz、kHz等）不要误判\n"
            "5. 型号名称、参数值不要误判\n"
            "6. 如果没有明显错误，返回空数组\n"
            "7. 只返回JSON，不要其他文字"
        )

        return "".join(prompt_parts)

    def _parse_result(self, raw_result: str) -> Dict[str, Any]:
        """解析 AI 返回的结果"""
        issues = []

        try:
            # 尝试提取 JSON 部分
            json_match = re.search(r'\{[\s\S]*\}', raw_result)
            if json_match:
                json_str = json_match.group()
                result = json.loads(json_str)
                return result
        except json.JSONDecodeError as e:
            logger.warning(f"JSON 解析失败，尝试手动解析: {str(e)}")

        # 手动解析结果
        lines = raw_result.split('\n')
        current_issue = {}

        for line in lines:
            line = line.strip()

            # 尝试匹配问题条目
            if '[' in line and ']' in line:
                # 可能是问题描述行
                pass

        return {
            "issues": issues,
            "summary": raw_result[:500] if len(raw_result) > 500 else raw_result
        }

    def check_consistency(
        self,
        key_info: Dict[str, List[Dict[str, Any]]]
    ) -> Dict[str, Any]:
        """检查关键信息一致性

        Args:
            key_info: 关键信息字典

        Returns:
            一致性检查结果
        """
        if not self.is_available():
            raise AIServiceUnavailableException("AI 服务未配置")

        # 准备关键信息
        info_summary = []
        for key, values in key_info.items():
            if values:
                info_summary.append(f"{key}: {', '.join([v['value'] for v in values])}")

        if not info_summary:
            return {
                "is_consistent": True,
                "issues": [],
                "summary": "未找到足够的关键信息进行一致性检查"
            }

        prompt = f"""请检查以下硬件测试报告中关键信息的一致性：

{chr(10).join(info_summary)}

请检查：
1. 不同 Sheet 之间的同一字段是否一致
2. 是否存在矛盾或冲突的信息
3. 必要字段是否缺失

请以 JSON 格式输出：
```json
{{
  "is_consistent": true/false,
  "issues": [
    {{"type": "不一致", "field": "字段名", "values": ["值1", "值2"], "suggestion": "建议"}}
  ],
  "summary": "总结"
}}
```"""

        try:
            api_kwargs = {
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": "你是一个专业的硬件测试报告审核助手。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.1,
                "max_tokens": 2000
            }
            
            if self.config.enable_thinking:
                api_kwargs["extra_body"] = {"enable_thinking": True}
            
            response = self.client.chat.completions.create(**api_kwargs)

            result = response.choices[0].message.content

            # 解析 JSON
            try:
                json_match = re.search(r'\{[\s\S]*\}', result)
                if json_match:
                    return json.loads(json_match.group())
            except:
                pass

            return {
                "is_consistent": True,
                "issues": [],
                "summary": result[:500]
            }

        except Exception as e:
            logger.error(f"一致性检查失败: {str(e)}")
            raise AIException(f"一致性检查失败: {str(e)}")

    # ==================== 测试项目智能分类（AI处理）====================

    def classify_test_items(
        self,
        test_items: List[Dict[str, Any]],
        product_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """AI 智能分类测试项目（混合模式核心功能）

        这是混合模式的第二步：AI 将测试项智能分类到标准类别

        Args:
            test_items: Python 提取的测试项列表
            product_type: 产品类型（如 CAMERA, ROUTER, IOT, STB, IPTV）

        Returns:
            分类结果，包含分类后的类别统计
        """
        if not self.is_available():
            raise AIServiceUnavailableException("AI 服务未配置，无法进行智能分类")

        if not test_items:
            return {
                "total": 0,
                "classified": {},
                "unclassified": [],
                "summary": "未提供测试项"
            }

        # 限制数量，防止超时
        max_items = 100
        check_items = test_items[:max_items]

        # 构建标准类别（基于实际测试报告结构）
        standard_categories = [
            "1. Baseband Signal（基带信号）",
            "2. Hardware Functions & Performance（硬件功能与性能）",
            "3. Whole-machine Reliability（整机可靠性）",
            "4. EMC（电磁兼容）",
            "5. Test Equipment Summary（测试设备汇总）",
            "6. Test Case Revision（测试用例修订）"
        ]

        # 产品类型说明
        product_desc = ""
        if product_type:
            product_desc = f"\n产品类型：{product_type}"

        prompt = f"""你是一个硬件测试报告分析专家。请将以下测试项目智能分类到标准类别中。

{product_desc}

标准类别：
{chr(10).join(standard_categories)}

待分类的测试项目：
"""

        for idx, item in enumerate(check_items):
            prompt += f"{idx + 1}. {item.get('name', '')}\n"

        prompt += """

请以 JSON 格式返回分类结果：
```json
{
  "total": 测试项总数,
  "categories": {
    "类别名称": {
      "count": 该类别下的测试项数量,
      "items": ["测试项1", "测试项2"]
    }
  },
  "unclassified": ["无法分类的测试项"],
  "summary": "分类总结，包括发现的关键信息"
}
```

注意：
1. 只返回 JSON，不要有其他文字
2. 如果测试项是"测试设备汇总"或"测试用例修订"类别，应该归入相应标准类别
3. 提供有意义的总结"""

        try:
            api_kwargs = {
                "model": self.config.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "你是一个专业的硬件测试报告分析助手，擅长将测试项目智能分类到标准类别。"
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.1,
                "max_tokens": 2000
            }
            
            if self.config.enable_thinking:
                api_kwargs["extra_body"] = {"enable_thinking": True}
            
            response = self.client.chat.completions.create(**api_kwargs)

            result = response.choices[0].message.content

            # 解析 JSON
            try:
                json_match = re.search(r'\{[\s\S]*\}', result)
                if json_match:
                    parsed = json.loads(json_match.group())
                    logger.info(f"AI 智能分类完成，共 {len(check_items)} 项，分为 {len(parsed.get('categories', {}))} 个类别")
                    return parsed
            except json.JSONDecodeError as e:
                logger.warning(f"JSON 解析失败: {e}")

            return {
                "total": len(check_items),
                "categories": {},
                "unclassified": [item.get('name') for item in check_items],
                "summary": "AI 解析失败，返回原始数据",
                "raw_result": result[:500]
            }

        except Exception as e:
            logger.error(f"AI 智能分类失败: {str(e)}")
            raise AIException(f"AI 智能分类失败: {str(e)}")
