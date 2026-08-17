"""轮动执行侧测试：目标计算、持仓推导、调仓待办双腿完成态"""
from datetime import date, timedelta

import pytest

from backend.models.database import Base
from backend.utils.db import engine, init_db
from backend.services.rotation_service import RotationService, rotation_target
from backend.services.trade_service import TradeService


@pytest.fixture(autouse=True)
def _clean_db():
    init_db()
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture()
def services():
    trade = TradeService()
    return RotationService(trade), trade


_POOL = [{'symbol': 'A', 'symbol_name': '甲'}, {'symbol': 'B', 'symbol_name': '乙'}]


def _make_plan(svc, rebalance='weekly', **kw):
    params = {'name': '测试轮动', 'pool': _POOL, 'window': 5, 'rebalance': rebalance}
    params.update(kw)
    return svc.create_plan(params)


def _closes(up=True, n=15):
    """生成单调收盘序列（up=True 涨 / False 跌）"""
    base = 1.0
    out = []
    for i in range(n):
        base *= 1.01 if up else 0.99
        out.append(round(base, 4))
    return out


class TestTarget:
    def test_picks_strongest(self):
        target, mom = rotation_target({'A': _closes(True), 'B': _closes(False)}, 3)
        assert target == 'A'
        assert mom['A'] > 0 > mom['B']

    def test_all_negative_is_cash(self):
        target, _ = rotation_target({'A': _closes(False), 'B': _closes(False)}, 3)
        assert target is None

    def test_insufficient_data(self):
        target, mom = rotation_target({'A': [1.0, 1.1], 'B': [1.0, 1.2]}, 10)
        assert target is None and mom == {}


class TestDueTodos:
    def test_enter_todo_when_no_holding(self, services):
        svc, _ = services
        _make_plan(svc)
        todos = svc.due_todos(lambda s, d: _closes(s == 'A'), today=date(2026, 8, 12))
        assert len(todos) == 1
        t = todos[0]
        assert t['action'] == 'enter'      # 空仓 → 买 A
        assert t['target']['symbol'] == 'A'
        assert t['buy_done'] is False

    def test_switch_todo_two_legs(self, services):
        svc, trade = services
        p = _make_plan(svc)
        trade.add_trade({'symbol': 'B', 'symbol_name': '乙',
                         'trade_date': '2026-08-05', 'direction': 'buy',
                         'price': 1.0, 'shares': 1000, 'rotation_plan_id': p['id']})
        # A 涨 B 跌 → 目标 A，当前持 B → switch
        todos = svc.due_todos(lambda s, d: _closes(s == 'A'), today=date(2026, 8, 12))
        assert len(todos) == 1 and todos[0]['action'] == 'switch'
        t = todos[0]
        assert t['holding']['symbol'] == 'B' and t['target']['symbol'] == 'A'
        assert t['sell_done'] is False and t['buy_done'] is False

        # 记卖出（本期）→ 待办仍在（买腿未完成）
        trade.add_trade({'symbol': 'B', 'symbol_name': '乙',
                         'trade_date': '2026-08-12', 'direction': 'sell',
                         'price': 1.0, 'shares': 1000, 'rotation_plan_id': p['id']})
        todos = svc.due_todos(lambda s, d: _closes(s == 'A'), today=date(2026, 8, 12))
        assert len(todos) == 1 and todos[0]['sell_done'] is True

        # 再记买入 → 待办消失
        trade.add_trade({'symbol': 'A', 'symbol_name': '甲',
                         'trade_date': '2026-08-12', 'direction': 'buy',
                         'price': 1.0, 'shares': 1000, 'rotation_plan_id': p['id']})
        assert svc.due_todos(lambda s, d: _closes(s == 'A'), today=date(2026, 8, 12)) == []

    def test_target_equals_holding_no_todo(self, services):
        svc, trade = services
        p = _make_plan(svc)
        trade.add_trade({'symbol': 'A', 'symbol_name': '甲',
                         'trade_date': '2026-08-05', 'direction': 'buy',
                         'price': 1.0, 'shares': 1000, 'rotation_plan_id': p['id']})
        # A 一直最强 → 目标=持仓 → 无待办
        todos = svc.due_todos(lambda s, d: _closes(s == 'A'), today=date(2026, 8, 12))
        assert todos == []

    def test_exit_todo_when_all_negative(self, services):
        svc, trade = services
        p = _make_plan(svc)
        trade.add_trade({'symbol': 'A', 'symbol_name': '甲',
                         'trade_date': '2026-08-05', 'direction': 'buy',
                         'price': 1.0, 'shares': 1000, 'rotation_plan_id': p['id']})
        todos = svc.due_todos(lambda s, d: _closes(False), today=date(2026, 8, 12))
        assert len(todos) == 1 and todos[0]['action'] == 'exit'
        assert todos[0]['target'] is None

    def test_paused_plan_no_todo(self, services):
        svc, _ = services
        p = _make_plan(svc)
        svc.update_status(p['id'], 'paused')
        assert svc.due_todos(lambda s, d: _closes(True), today=date(2026, 8, 12)) == []


class TestValidation:
    def test_pool_and_window(self, services):
        svc, _ = services
        with pytest.raises(ValueError, match='2-6'):
            svc.create_plan({'pool': [{'symbol': 'A'}]})
        with pytest.raises(ValueError, match='5-120'):
            svc.create_plan({'pool': _POOL, 'window': 3})
        with pytest.raises(ValueError, match='weekly'):
            svc.create_plan({'pool': _POOL, 'window': 20, 'rebalance': 'daily'})
