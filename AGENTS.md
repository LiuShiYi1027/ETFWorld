## Imported Claude Cowork project instructions

## 项目约定

- Tushare 初始化有唯一入口：`backend/services/tushare_init.py` 的 `get_pro()` / `pro_bar()`。
  任何新代码需要 Tushare 数据时必须从这里拿 pro 对象，不要自行 `ts.pro_api()`——
  关键是 `pro._DataApi__http_url` 必须指向自定义数据源（默认 `https://ttx.dailyfetch.top/`），
  否则会提示 "Token 不对"；调用 `pro_bar` 必须显式传 `api=pro`。
- AI 研判配置使用 `AI_API_URL` / `AI_API_KEY` / `AI_MODEL`（旧 `DEEPSEEK_*` 仅作兼容读取）。
- 密钥只写入 `backend/.env`（已被 Git 忽略），通过 `settings.save_runtime_settings()` 持久化，绝不写进源码或提交。
- 数据库 schema 变更不能只改 `backend/models/database.py`：`create_all` 不会给老表加列。
  必须同时在 `backend/utils/migrate.py` 的 `MIGRATIONS` 追加递增版本号的迁移函数
  （`init_db()` 启动时自动执行），并在 `tests/test_migrate.py` 补断言。
