# AGENTS.md

本仓库是硬件研发部测试报告审核平台的独立代码准源。

## 项目边界

- FastAPI 同时提供页面、静态资源和 `/api/v1/*` 接口。
- 上传、临时文件和归档位于 `data/`；它们可能包含真实测试报告，禁止进入 Git、日志样例或测试夹具。
- `.env`、API Key、生产日志、备份和运行数据禁止提交。
- 本仓库不包含 `agent-runtime-service`；不要再依赖父仓库 `NeoFlowAgentRuntime` 的启动脚本或环境文件。

## NeoFlow 部署约束

- 生产端口只绑定回环地址，TLS 由平台 Nginx 终止。
- 当前 Compose 中的本地卷仅用于迁移预演和工作缓存。正式切流前，必须明确历史 Excel 的保留策略，并把需持久化文件备份或迁入 OSS；不得把本地卷作为唯一副本。
- 生产 `.env` 单独维护，权限 `600`；输出只能说明配置是否存在，不能打印密钥值。
- 服务器只从 GitHub 准源拉取部署，不在服务器直接修改代码。

## 验证

至少运行：

```bash
python -m compileall -q app health_check.py
docker compose config
```

涉及业务逻辑时还应补充自动化测试。部署后检查 Compose 状态、`/health`、页面静态资源、上传审核、聚合分析和导出。

## Git

- 主分支为 `main`。
- remote `origin` 指向 `Max0314/report-audit-platform`。
- 提交前检查没有 `.env`、Excel、运行数据、日志或备份进入暂存区。
