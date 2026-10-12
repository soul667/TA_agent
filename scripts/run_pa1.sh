#!/usr/bin/env bash
set -euo pipefail
ta_project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ta_project_root"
if [[ -f .env.local ]]; then
  set -a
  source .env.local
  set +a
fi
export AA_ROOT="$ta_project_root/datasets/pa1_2026fa"
export AA_OUTPUT_LANG=zh
if [[ ! -f "$AA_ROOT/data/assignment/cs111-2026fa-pa1.json" ]]; then
  echo 'PA1 作业配置不存在，请先完成提交包导入和作业配置。' >&2
  exit 1
fi
# 默认绑定 0.0.0.0 开放局域网（学生手机扫码答题）；如仅本机使用，启动前
# export AA_BIND_HOST=127.0.0.1 收回。Host 白名单已默认放行私网段。
exec "$ta_project_root/.venv/bin/uvicorn" app.main:app --host "${AA_BIND_HOST:-0.0.0.0}" --port "${AA_PORT:-8000}"
