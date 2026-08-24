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
  -> report-audit-platform (shared network: neo-hardware-report-audit)
  -> htmlsystm internal session verification
  -> AI provider

Historical Excel
  -> neoflow-neo-hardware/prod/report-audit/legacy/ (SHA-256 verified)
New uploads
  -> neoflow-neo-hardware/prod/report-audit/uploads/ (write-through)
Local Docker volume
  -> working cache only
```

新服务器目录为 `/home/max/apps/report-audit-platform`。回环端口使用已盘点空闲的
`39023`，部署时仍由脚本校验它位于平台分配的 `39020-39029` 段。

### 已确定的存储与登录方案

- 不新建 bucket，复用私有 bucket `neoflow-neo-hardware`，使用隔离前缀
  `prod/report-audit/`。建议使用仅授权该前缀的独立 RAM AccessKey；密钥只放生产
  `.env`，权限 `600`。
- 不新建用户表或登录页。报告服务把浏览器的硬件门户 Cookie 原样转给
  `htmlsystm` 内网会话校验接口；页面未登录跳回硬件门户登录页，API 返回 401。
- POST 等写请求继续使用硬件门户已有的 `csrf_token` 双提交校验。
- 任务记录绑定当前硬件门户用户，其他登录用户不能读取状态、结果或导出。
- `/health` 允许容器和宿主机探活；`/metrics`、页面、静态资源和业务 API 均需登录。

## 迁移步骤

### 1. 固化代码准源

1. 确认两个迁移分支均已推送到 GitHub，并经检查后合入各自 `main`：
   `report-audit-platform` 与 `neo_hardware_portal`。
2. 在新服务器从 GitHub clone，不从旧服务器工作目录直接复制代码。
3. 从 `.env.example` 创建生产 `.env`，权限设为 `600`。只迁移报告服务所需 AI
   配置；OSS 与内部会话密钥不得出现在终端输出、日志或 Git。

### 2. 迁移前备份与数据分类

1. 记录旧容器、镜像、Compose 和数据卷状态。
2. 暂停新上传，停止旧容器写入后，对 `report-audit-data` 生成只读迁移包及 SHA-256 清单。
3. 将 `uploads/`、`temp/`、`archive/` 全部先按需保留，未确认前不删除任何 Excel。
4. 本次迁移不猜测旧文件用途：`uploads/`、`temp/`、`archive/` 全部进入
   `prod/report-audit/legacy/`，确认保留策略后再单独清理。

### 3. 新服务器私有预演

1. 先部署 `neo_hardware_portal` 的共享网络改动；gateway 使用 Docker DNS 动态解析，
   报告容器尚未启动时只会对报告路径返回 502，不影响门户其他功能。
2. 从 GitHub clone 到 `/home/max/apps/report-audit-platform`，创建权限 600 的 `.env`，
   确认 `REPORT_AUDIT_PUBLISH_PORT=39023`、`OSS_PREFIX=prod/report-audit`、
   `HARDWARE_AUTH_INTERNAL_URL=http://htmlsystm:8000`。
3. 执行 `docker compose config` 和 `bash scripts/deploy.sh`。应用启动会执行一次真实
   OSS 写/读/删探针；无权限或配置错误会直接启动失败，避免静默落回本地。
4. 将旧 data 树只读传到新服务器暂存目录，先预演，再上传和逐对象读回校验：

   ```bash
   docker compose exec report-audit-platform \
     python -m scripts.migrate_local_tree_to_oss \
       --root /app/data/legacy-staging \
       --manifest /app/data/migration/legacy-manifest.json

   docker compose exec report-audit-platform \
     python -m scripts.migrate_local_tree_to_oss \
       --root /app/data/legacy-staging \
       --manifest /app/data/migration/legacy-manifest.json \
       --upload --verify
   ```

   两次输出的 `file_count`、`total_bytes`、`tree_sha256` 必须一致。清单和暂存目录不进 Git。
5. 只通过新服务器本机或 SSH 隧道检查：

   ```bash
   curl -fsS http://127.0.0.1:39023/health
   docker compose ps
   docker compose logs --tail=100 report-audit-platform
   ```

### 4. 门户联调与切流

1. 硬件门户 gateway 上游固定为共享网络服务名 `report-audit-platform:8000`，Cookie
   显式透传；不使用宿主机 bridge IP 或 `0.0.0.0:8000`。
2. 验证 `/neo_hardware/report-audit/` 的 HTML、CSS、JS 和 `/api/v1/*` 子路径。
3. 分别验证未登录跳转、已登录复用、CSRF 拒绝；再用脱敏样例完成单文件审核、
   批量聚合、进度查询和 Excel 导出，并确认新对象位于 `prod/report-audit/uploads/`。
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
