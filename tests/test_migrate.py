"""数据库迁移机制测试：老库补列、幂等、新库盖章、数据保留。"""
import sqlalchemy as sa
from sqlalchemy import text

from backend.models.database import Base
from backend.utils import migrate


def _make_engine(path):
    return sa.create_engine(f'sqlite:///{path}', connect_args={'check_same_thread': False})


def _cols(eng, table):
    with eng.connect() as c:
        return {r[1] for r in c.execute(text(f'PRAGMA table_info("{table}")'))}


def _user_version(eng):
    with eng.connect() as c:
        return c.execute(text('PRAGMA user_version')).scalar()


def _create_v1_grid_plans(eng):
    """模拟早期老库：grid_plans 缺少 version/step_increase/profit_retention/note 等后加列"""
    with eng.begin() as c:
        c.execute(text('''
            CREATE TABLE grid_plans (
                id INTEGER PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                symbol VARCHAR(20) NOT NULL,
                symbol_name VARCHAR(100),
                base_price NUMERIC(12, 4) NOT NULL,
                grid_step NUMERIC(8, 4) NOT NULL,
                grid_count INTEGER NOT NULL,
                amount_per_grid NUMERIC(14, 2) NOT NULL,
                levels JSON,
                status VARCHAR(20),
                created_at DATETIME,
                updated_at DATETIME
            )
        '''))
        c.execute(text('''
            INSERT INTO grid_plans (name, symbol, base_price, grid_step, grid_count,
                                    amount_per_grid, status)
            VALUES ('老计划', '510300.SH', 4.0, 5, 10, 10000, 'active')
        '''))


def test_old_db_reconciled_and_data_kept(tmp_path):
    eng = _make_engine(tmp_path / 'old.db')
    _create_v1_grid_plans(eng)
    with eng.begin() as c:  # 老库 trades 表（缺 fee/grid_level/note/dca_plan_id）
        c.execute(text('''
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY, plan_id INTEGER, symbol VARCHAR(20) NOT NULL,
                symbol_name VARCHAR(100), trade_date DATE NOT NULL,
                direction VARCHAR(10) NOT NULL, price NUMERIC(12, 4) NOT NULL,
                shares NUMERIC(16, 2) NOT NULL, created_at DATETIME
            )
        '''))
    assert 'profit_retention' not in _cols(eng, 'grid_plans')

    Base.metadata.create_all(eng)  # 老表不会被改，只补新表
    migrate.run_migrations(eng)

    cols = _cols(eng, 'grid_plans')
    for col in ('version', 'step_increase', 'profit_retention', 'note',
                'grid_mode', 'shares_per_grid'):
        assert col in cols, f'老库缺列未补齐: {col}'
    for col in ('fee', 'grid_level', 'note', 'dca_plan_id'):
        assert col in _cols(eng, 'trades'), f'老库 trades 缺列未补齐: {col}'
    assert _user_version(eng) == migrate.SCHEMA_VERSION

    with eng.connect() as c:  # 老数据还在
        row = c.execute(text('SELECT name, status FROM grid_plans')).fetchone()
    assert row == ('老计划', 'active')


def test_migrations_idempotent(tmp_path):
    eng = _make_engine(tmp_path / 'twice.db')
    _create_v1_grid_plans(eng)
    Base.metadata.create_all(eng)
    migrate.run_migrations(eng)
    migrate.run_migrations(eng)  # 再跑一次不应报错、不应重复补列
    assert _user_version(eng) == migrate.SCHEMA_VERSION
    assert 'profit_retention' in _cols(eng, 'grid_plans')


def test_fresh_db_stamped_without_migrating(tmp_path):
    eng = _make_engine(tmp_path / 'fresh.db')
    Base.metadata.create_all(eng)
    migrate.run_migrations(eng)
    assert _user_version(eng) == migrate.SCHEMA_VERSION
    # create_all 已给出全列，reconcile 无需也不应破坏任何列
    for col in ('version', 'step_increase', 'profit_retention', 'note'):
        assert col in _cols(eng, 'grid_plans')


def test_ensure_column_idempotent(tmp_path):
    eng = _make_engine(tmp_path / 'col.db')
    with eng.begin() as c:
        c.execute(text('CREATE TABLE t (id INTEGER PRIMARY KEY)'))
        assert migrate.ensure_column(c, 't', 'x', 'NUMERIC(8, 4)') is True
        assert migrate.ensure_column(c, 't', 'x', 'NUMERIC(8, 4)') is False


def test_old_db_gets_app_meta_table(tmp_path):
    """迁移 5：老库升级后 app_meta 键值表存在（监控池一次性播种标记）"""
    eng = _make_engine(tmp_path / 'old5.db')
    _create_v1_grid_plans(eng)
    Base.metadata.create_all(eng)
    migrate.run_migrations(eng)
    with eng.connect() as c:
        tables = {r[0] for r in c.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'"))}
    assert 'app_meta' in tables
    assert _user_version(eng) == migrate.SCHEMA_VERSION
