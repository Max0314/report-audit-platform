# NeoFlow 迁移方案

更新时间：2026-08-24

## 当前状态

- 旧 AWS 部署目录：`/home/work/AI/NeoFlowAgentRuntime`。
- 旧容器：`report-audit-platform`，当前使用宿主机 `8000`，数据卷约 515 MB。
- 持久化目录包含 `uploads/`、`temp/`、`archive/`；盘点时共有 30 个 Excel 文件。
- NeoFlow 硬件门户已经暴露 `/neo_hardware/report-audit/`，但其上游仍是宿主机 `8000`，而新服务器没有对应容器或监听端口，因此请求超时。
- 本仓库已从父仓库拆分并保留本项目历史。父仓库中的 `agent-runtime-service` 不属于本项目。

## 目标状态

```text
Browser
  -> NeoFlow platform Nginx (/neo_hardware/)
  -> neo_hardware gateway
  -> report-audit-platform (loopback 39023 or shared Docker network)
  -> AI provider

Historical Excel
  -> verified backup / OSS
Local Docker volume
  -> working cache only
```

新服务器建议目录为 `/home/max/apps/report-audit-platform`。端口 `39023` 仅是当前盘点后的建议值，部署前仍须重新检查端口登记和监听状态。

## 迁移步骤

### 1. 固化代码准源

1. 确认本仓库 `main` 已推送到 GitHub。
2. 在新服务器从 GitHub clone，不从旧服务器工作目录直接复制代码。
3. 从 `.env.example` 创建生产 `.env`，权限设为 `600`，人工填写 AI 配置；不得把旧 `.env` 提交或输出。

### 2. 迁移前备份与数据分类

1. 记录旧容器、镜像、Compose 和数据卷状态。
2. 暂停新上传，停止旧容器写入后，对 `report-audit-data` 生成只读迁移包及 SHA-256 清单。
3. 将 `uploads/`、`temp/`、`archive/` 全部先按需保留，未确认前不删除任何 Excel。
4. 明确哪些文件是业务归档、哪些是可清理临时文件；需长期保留的文件迁入私有 OSS，本地卷不得是唯一副本。

### 3. 新服务器私有预演

1. clone 到 `/home/max/apps/report-audit-platform`。
2. 创建 `.env`，确认 `REPORT_AUDIT_PUBLISH_PORT=39023`。
3. 恢复迁移数据到新数据卷或工作缓存，并按 SHA-256 对账。
4. 执行 `docker compose config` 和 `docker compose up -d --build`。
5. 只通过新服务器本机或 SSH 隧道检查：

   ```bash
   curl -fsS http://127.0.0.1:39023/health
   docker compose ps
   docker compose logs --tail=100 report-audit-platform
   ```

### 4. 门户联调与切流

1. 将硬件门户 gateway 的报告审核上游改到新服务；优先使用明确的共享 Docker network 服务名，否则使用宿主机回环映射和 `host.docker.internal`。
2. 验证 `/neo_hardware/report-audit/` 的 HTML、CSS、JS 和 `/api/v1/*` 子路径。
3. 使用脱敏样例完成单文件审核、批量聚合、进度查询和 Excel 导出。
4. 检查新服务和 gateway 最近错误日志，再开放正式入口。

### 5. 观察与回滚

- 切流后先保留旧容器、旧镜像、旧目录和旧数据卷，仅停止旧容器。
- 回滚时恢复旧容器并把 gateway 上游指回旧入口；新环境数据暂不删除。
- 观察期结束且数据对账、业务验收均通过后，才进入旧环境清理。

## 删除旧目录的条件

`/home/work/AI/NeoFlowAgentRuntime` 不只包含本项目，还包含 `agent-runtime-service` 的 Compose、二进制和模型配置。只有同时满足以下条件才能删除：

1. 报告审核平台已从新仓库稳定运行并通过业务验收。
2. 旧报告数据已备份并校验，旧容器已停止且观察期内未回滚。
3. `agent-runtime-service` 维护人确认旧目录中的运行包和模型配置已由新服务器部署接管、不再作为回滚源。
4. 已确认没有容器、systemd、cron 或人工脚本引用该目录。

删除代码目录不会自动删除 Docker named volume。旧数据卷应按单独的保留期处理，并在再次获得明确授权后清理。
