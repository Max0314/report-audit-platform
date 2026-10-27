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

- **四项顶部入口**：智能报告审核 / 测数聚合分析 / CQC单报告审核 / CQC 双报告对比；原入口直接进入智能审核，所有功能页可返回硬件门户
- **CQC 认证库**：独立授权账号从单报告审核页进入“认证库管理”，上传指定版式 Excel 按认证号增量新增/修改，未提及的记录保留，支持历史完整库回滚；库内未匹配项仅提示。授权使用 `CQC_LIBRARY_ADMIN_USER_IDS`，不继承或修改门户管理员权限
- **AI 能力集成**：沿用 NeoFlow 的 DeepSeek V4 Pro，用于原有 Excel 智能审核；两个 CQC 功能仅进行规则检查，不调用模型
- **高度可拓展**：模块化设计、配置与代码分离
- **私有持久化**：生产原文件写通到阿里云 OSS，本地卷只作解析缓存
- **统一登录**：复用硬件门户的 `session_id` 会话和 CSRF 防护

## 技术栈

- 后端：Python 3.10+ / FastAPI
- 前端：HTML5 / CSS3 / Vanilla JavaScript (ES6+)
- Excel处理：pandas / openpyxl
- 数据可视化：Plotly.js
- AI服务：DeepSeek API

## 快速开始

```bash
# 1. 创建配置
cp .env.example .env
# 本地开发可设置 STORAGE_BACKEND=local、HARDWARE_AUTH_ENABLED=false

# 2. 安装依赖
pip install -r requirements.txt

# 3. 本地启动服务
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 4. 访问平台
# 浏览器打开 http://localhost:8000
```

NeoFlow 生产部署：

```bash
cp .env.example .env
# 填写 AI、OSS、硬件门户内部会话校验配置，并确保权限为 600
chmod 600 .env
bash scripts/deploy.sh
curl -fsS http://127.0.0.1:39023/health
```

生产入口为 `/neo_hardware/report-audit/`。Compose 仍发布
`127.0.0.1:39023` 供本机探活，但门户流量通过共享 Docker 网络进入服务，
TLS 继续由 NeoFlow 平台 Nginx 统一终止。

NeoFlow 的迁移、数据备份、切流、回滚和旧目录删除条件见
[`docs/neoflow-migration.md`](docs/neoflow-migration.md)。

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
├── scripts/               # 部署与历史文件迁移工具
├── data/                  # 本地解析缓存（不作为生产持久层）
├── docker-compose.yml     # 独立 Compose 入口
└── requirements.txt      # Python依赖
```

## 扩展指南

详见 `docs/` 目录下的扩展指南文档。

## License

MIT License
