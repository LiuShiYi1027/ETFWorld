## Imported Claude Cowork project instructions

## 项目约定

- Tushare 初始化有唯一入口：`backend/services/tushare_init.py` 的 `get_pro()` / `pro_bar()`。
  任何新代码需要 Tushare 数据时必须从这里拿 pro 对象，不要自行 `ts.pro_api()`——
  关键是 `pro._DataApi__http_url` 必须指向自定义数据源（默认 `https://ttx.dailyfetch.top/`），
  否则会提示 "Token 不对"；调用 `pro_bar` 必须显式传 `api=pro`。
- AI 研判配置使用 `AI_API_URL` / `AI_API_KEY` / `AI_MODEL`（旧 `DEEPSEEK_*` 仅作兼容读取）。
- 密钥只写入 `backend/.env`（已被 Git 忽略），通过 `settings.save_runtime_settings()` 持久化，绝不写进源码或提交。
