#!/usr/bin/env bash
# ETFWorld 开发者一键启动：创建 venv、装依赖、起 Web 服务
set -e
cd "$(dirname "$0")"

[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r backend/requirements.txt

echo "ETFWorld → http://127.0.0.1:8000"
exec python -m uvicorn backend.api.main:app --reload
