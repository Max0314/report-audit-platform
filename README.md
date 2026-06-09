---
AIGC:
    ContentProducer: Minimax Agent AI
    ContentPropagator: Minimax Agent AI
    Label: AIGC
    ProduceID: "00000000000000000000000000000000"
    PropagateID: "00000000000000000000000000000000"
    ReservedCode1: 3045022100f6ce47b789972a583231a76be955c7ed2d3aef323b8b7d0dcece7b9f43abdaab02201fd8d7f6403c014f791edf0d7b40b3e889d9a4626261f73a6995225416fc5b0c
    ReservedCode2: 304402200b6a26f93a7149d83e4d058a6d59c9c6654561c1669d4933186fb28816fb00ba0220242dd479c795cebd1abf07062f20bc9a1060f2a6a1a73a9910b32cac7d2ea541
---

# 硬件研发部测试报告审核平台
# Hardware R&D Test Report Audit Platform

## 项目简介

本平台是专门为硬件研发部设计的测试报告智能审核与数据分析系统，采用前后端分离架构，支持本地部署，确保研发数据安全。

## 核心特性

- **双入口设计**：智能报告审核 / 测数聚合分析
- **AI 能力集成**：DeepSeek API 实现错别字与语义检测
- **高度可拓展**：模块化设计、配置与代码分离
- **本地部署**：数据纯本地处理，保护隐私

## 技术栈

- 后端：Python 3.10+ / FastAPI
- 前端：HTML5 / CSS3 / Vanilla JavaScript (ES6+)
- Excel处理：pandas / openpyxl
- 数据可视化：Plotly.js
- AI服务：DeepSeek API

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 DeepSeek API
# 编辑 config/settings.yaml 设置 API Key

# 3. 启动服务
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 4. 访问平台
# 浏览器打开 http://localhost:8000
```

## 目录结构

```
├── app/                    # 后端应用
│   ├── core/              # 核心组件
│   ├── services/          # 业务服务
│   ├── routers/           # 路由
│   └── schemas/          # 数据模型
├── static/                # 前端静态资源
│   ├── css/               # 样式文件
│   ├── js/                # JavaScript模块
│   └── lib/               # 第三方库
├── templates/             # HTML模板
├── config/                # 配置文件
├── data/                  # 数据目录
└── requirements.txt      # Python依赖
```

## 扩展指南

详见 `docs/` 目录下的扩展指南文档。

## License

MIT License
