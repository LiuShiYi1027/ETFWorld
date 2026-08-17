"""
数据库结构迁移：轻量有序迁移表 + SQLite PRAGMA user_version 版本追踪。

- 新库：init_db() 的 create_all 已建出最新结构，直接 stamp 到最新版本，不跑迁移。
- 老库：按 MIGRATIONS 顺序逐条补齐（每条幂等、事务内执行、逐条 bump 版本）。
- 非 SQLite 引擎直接跳过（本项目恒为 SQLite）。

加新迁移的步骤：
    1. 改 backend/models/database.py 的模型；
    2. 在下面 MIGRATIONS 追加 (版本号, 迁移函数)，版本号递增；
    3. 在 tests/test_migrate.py 补对应断言。
"""
import logging

from sqlalchemy import text

from backend.models.database import Base

logger = logging.getLogger(__name__)

# 当前 schema 版本号 = MIGRATIONS 里最大版本号（见文件底部 assert）


def _table_names(conn):
    rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
    return {r[0] for r in rows}


def _column_names(conn, table):
    return {r[1] for r in conn.execute(text(f'PRAGMA table_info("{table}")'))}


def ensure_column(conn, table, column, ddl_type):
    """缺列则补列（幂等；SQLite 的 ADD COLUMN 不支持 IF NOT EXISTS，须先查）。"""
    if column in _column_names(conn, table):
        return False
    conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl_type}'))
    logger.info('迁移补列: %s.%s (%s)', table, column, ddl_type)
    return True


def _reconcile_columns(conn, dialect):
    """对齐模型与已有表的列：只补缺失列，不动已有列/索引/约束。

    注意：SQLite 不允许 ADD COLUMN 带非常量默认值的 NOT NULL 列，
    这里统一按「裸类型」补列（可空、无默认），由 ORM 层 Python 默认值兜底。
    """
    existing = _table_names(conn)
    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing:
            continue  # 新表由 create_all 负责
        for col in table.columns:
            ddl_type = col.type.compile(dialect=dialect)
            ensure_column(conn, table_name, col.name, ddl_type)


def _m001_reconcile(conn, dialect):
    """迁移 1：基线对齐 —— 把开发期/早期版本建的老表补齐到当前模型列。"""
    _reconcile_columns(conn, dialect)


def _m002_grid_mode(conn, dialect):
    """迁移 2：v2.3 网格投入方式（grid_mode / shares_per_grid 列）。"""
    _reconcile_columns(conn, dialect)


def _m003_dca(conn, dialect):
    """迁移 3：v2.4 定投计划（trades.dca_plan_id 列；dca_plans 新表由 create_all 建）。"""
    _reconcile_columns(conn, dialect)


def _m004_rotation(conn, dialect):
    """迁移 4：v2.6 轮动计划（trades.rotation_plan_id 列；rotation_plans 新表由 create_all 建）。"""
    _reconcile_columns(conn, dialect)


# (版本号, 迁移函数)；版本号严格递增，迁移函数签名 fn(conn, dialect)
MIGRATIONS = [
    (1, _m001_reconcile),
    (2, _m002_grid_mode),
    (3, _m003_dca),
    (4, _m004_rotation),
]

SCHEMA_VERSION = MIGRATIONS[-1][0]
assert [v for v, _ in MIGRATIONS] == sorted(v for v, _ in MIGRATIONS), \
    'MIGRATIONS 版本号必须递增'


def _get_user_version(conn):
    return conn.execute(text('PRAGMA user_version')).scalar()


def _set_user_version(conn, version):
    # PRAGMA 不支持参数绑定，版本号是我们自己的 int，安全
    conn.execute(text(f'PRAGMA user_version = {int(version)}'))


def run_migrations(engine):
    """在 create_all 之后调用：新库 stamp 版本，老库跑增量迁移。"""
    if engine.dialect.name != 'sqlite':
        logger.info('非 SQLite 引擎（%s），跳过迁移', engine.dialect.name)
        return
    with engine.begin() as conn:
        current = _get_user_version(conn)
        if current >= SCHEMA_VERSION:
            return
        is_fresh = not (_table_names(conn) - {'sqlite_sequence'})
        if is_fresh:
            # 全新库：create_all 已是最新结构，直接盖章
            _set_user_version(conn, SCHEMA_VERSION)
            logger.info('新库，schema 版本直接置为 %s', SCHEMA_VERSION)
            return
        for version, fn in MIGRATIONS:
            if version <= current:
                continue
            fn(conn, engine.dialect)
            _set_user_version(conn, version)
            logger.info('数据库迁移完成 → schema 版本 %s', version)
