#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "部署失败：缺少 .env" >&2
  exit 1
fi

if [[ "$(stat -c '%a' .env 2>/dev/null || true)" != "600" ]]; then
  echo "部署失败：.env 权限必须为 600（执行 chmod 600 .env）" >&2
  exit 1
fi

required=(
  STORAGE_BACKEND OSS_ENDPOINT OSS_BUCKET OSS_ACCESS_KEY_ID OSS_ACCESS_KEY_SECRET OSS_PREFIX
  HARDWARE_AUTH_ENABLED HARDWARE_AUTH_INTERNAL_URL HARDWARE_AUTH_INTERNAL_SECRET
)
for name in "${required[@]}"; do
  if ! grep -Eq "^${name}=.+" .env; then
    echo "部署失败：.env 中 ${name} 未配置" >&2
    exit 1
  fi
done

if ! grep -Eq '^STORAGE_BACKEND=oss([[:space:]]*)$' .env; then
  echo "部署失败：生产环境 STORAGE_BACKEND 必须为 oss" >&2
  exit 1
fi
if ! grep -Eq '^OSS_BUCKET=neoflow-neo-hardware([[:space:]]*)$' .env; then
  echo "部署失败：本次迁移必须复用 neoflow-neo-hardware bucket" >&2
  exit 1
fi
if ! grep -Eq '^OSS_PREFIX=prod/report-audit/?([[:space:]]*)$' .env; then
  echo "部署失败：OSS_PREFIX 必须为 prod/report-audit" >&2
  exit 1
fi

port="$(sed -n 's/^REPORT_AUDIT_PUBLISH_PORT=\([0-9][0-9]*\).*/\1/p' .env | tail -n 1)"
port="${port:-39023}"
if (( port < 39020 || port > 39029 )); then
  echo "部署失败：发布端口必须位于平台分配的 39020-39029" >&2
  exit 1
fi

if ! docker network inspect neo-hardware-report-audit >/dev/null 2>&1; then
  echo "部署失败：共享网络不存在，请先部署 neo_hardware_portal 的迁移分支" >&2
  exit 1
fi

docker compose config >/dev/null
if [[ "${1:-}" == "--no-build" ]]; then
  docker compose up -d
elif [[ -n "${1:-}" ]]; then
  echo "用法：bash scripts/deploy.sh [--no-build]" >&2
  exit 1
else
  docker compose up -d --build
fi

for _ in $(seq 1 36); do
  if curl --fail --silent --show-error "http://127.0.0.1:${port}/health" >/dev/null; then
    docker compose ps
    echo "报告审核平台部署完成，回环探针通过"
    exit 0
  fi
  sleep 5
done

docker compose ps >&2
echo "部署失败：180 秒内健康检查未通过，请检查 docker compose logs --tail=100" >&2
exit 1
