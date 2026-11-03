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


def _bars(up=True, n=15, end=date(2026, 8, 12)):
    """带日期的交易日收盘序列，供周期首日与下一交易日口径使用。"""
    days = []
    cursor = end
    while len(days) < n:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return [(d.isoformat(), close)
            for d, close in zip(reversed(days), _closes(up, n))]


class TestTarget:
    def test_picks_strongest(self):
        target, mom = rotation_target({'A': _closes(True), 'B': _closes(False)}, 3)
        assert target == 'A'
        assert mom['A'] > 0 > mom['B']

    def test_ranking_uses_unrounded_momentum(self):
        closes = {
            'A': [100, 101, 102, 103, 104, 110.001],
            'B': [100, 101, 102, 103, 104, 110.004],
        }
        target, mom = rotation_target(closes, 5)
        assert round(mom['A'], 2) == round(mom['B'], 2)
        assert target == 'B'
        assert mom['B'] > mom['A']

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
        todos = svc.due_todos(lambda s, d: _bars(s == 'A'), today=date(2026, 8, 12))
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
        todos = svc.due_todos(lambda s, d: _bars(s == 'A'), today=date(2026, 8, 12))
        assert len(todos) == 1 and todos[0]['action'] == 'switch'
        t = todos[0]
        assert t['holding']['symbol'] == 'B' and t['target']['symbol'] == 'A'
        assert t['sell_done'] is False and t['buy_done'] is False

        # 记卖出（本期）→ 待办仍在（买腿未完成）
        trade.add_trade({'symbol': 'B', 'symbol_name': '乙',
                         'trade_date': '2026-08-12', 'direction': 'sell',
                         'price': 1.0, 'shares': 1000, 'rotation_plan_id': p['id']})
        todos = svc.due_todos(lambda s, d: _bars(s == 'A'), today=date(2026, 8, 12))
        assert len(todos) == 1 and todos[0]['sell_done'] is True

        # 再记买入 → 待办消失
        trade.add_trade({'symbol': 'A', 'symbol_name': '甲',
                         'trade_date': '2026-08-12', 'direction': 'buy',
                         'price': 1.0, 'shares': 1000, 'rotation_plan_id': p['id']})
        assert svc.due_todos(lambda s, d: _bars(s == 'A'), today=date(2026, 8, 12)) == []

    def test_signal_locked_within_period(self, services):
        """同一调仓周期内信号锁定：周三行情翻转不改变本周已给目标"""
        svc, _ = services
        _make_plan(svc)
        first = svc.due_todos(lambda s, d: _bars(s == 'A'), today=date(2026, 8, 12))
        assert first[0]['target']['symbol'] == 'A'
        flipped = svc.due_todos(
            lambda s, d: _bars(s == 'B', end=date(2026, 8, 13)),
            today=date(2026, 8, 13))
        assert flipped[0]['target']['symbol'] == 'A'  # 仍为本周锁定信号

    def test_wrong_symbol_buy_does_not_complete_buy_leg(self, services):
        """买错标的不能买腿完成：待办必须保留"""
        svc, trade = services
        p = _make_plan(svc)
        trade.add_trade({'symbol': 'B', 'symbol_name': '乙',
                         'trade_date': '2026-08-05', 'direction': 'buy',
                         'price': 1.0, 'shares': 1000, 'rotation_plan_id': p['id']})
        trade.add_trade({'symbol': 'B', 'symbol_name': '乙',
                         'trade_date': '2026-08-12', 'direction': 'sell',
                         'price': 1.0, 'shares': 1000, 'rotation_plan_id': p['id']})
        trade.add_trade({'symbol': 'C', 'symbol_name': '丙',  # 目标 A，错记成 C
                         'trade_date': '2026-08-12', 'direction': 'buy',
                         'price': 1.0, 'shares': 1000, 'rotation_plan_id': p['id']})
        todos = svc.due_todos(lambda s, d: _bars(s == 'A'), today=date(2026, 8, 12))
        assert len(todos) == 1
        assert todos[0]['buy_done'] is False
        assert todos[0]['target']['symbol'] == 'A'

    def test_partial_sell_does_not_complete_sell_leg(self, services):
        """只卖出一半旧持仓，卖腿不算完成"""
        svc, trade = services
        p = _make_plan(svc)
        trade.add_trade({'symbol': 'B', 'symbol_name': '乙',
                         'trade_date': '2026-08-05', 'direction': 'buy',
                         'price': 1.0, 'shares': 1000, 'rotation_plan_id': p['id']})
        trade.add_trade({'symbol': 'B', 'symbol_name': '乙',
                         'trade_date': '2026-08-12', 'direction': 'sell',
                         'price': 1.0, 'shares': 500, 'rotation_plan_id': p['id']})
        todos = svc.due_todos(lambda s, d: _bars(s == 'A'), today=date(2026, 8, 12))
        assert len(todos) == 1
        assert todos[0]['sell_done'] is False

    def test_target_equals_holding_no_todo(self, services):
        svc, trade = services
        p = _make_plan(svc)
        trade.add_trade({'symbol': 'A', 'symbol_name': '甲',
                         'trade_date': '2026-08-05', 'direction': 'buy',
                         'price': 1.0, 'shares': 1000, 'rotation_plan_id': p['id']})
        # A 一直最强 → 目标=持仓 → 无待办
        todos = svc.due_todos(lambda s, d: _bars(s == 'A'), today=date(2026, 8, 12))
        assert todos == []

    def test_exit_todo_when_all_negative(self, services):
        svc, trade = services
        p = _make_plan(svc)
        trade.add_trade({'symbol': 'A', 'symbol_name': '甲',
                         'trade_date': '2026-08-05', 'direction': 'buy',
                         'price': 1.0, 'shares': 1000, 'rotation_plan_id': p['id']})
        todos = svc.due_todos(lambda s, d: _bars(False), today=date(2026, 8, 12))
        assert len(todos) == 1 and todos[0]['action'] == 'exit'
        assert todos[0]['target'] is None

    def test_missing_same_window_blocks_entire_pool_signal(self, services):
        svc, _ = services
        _make_plan(svc)

        def bars_of(symbol, days):
            bars = _bars(symbol == 'A')
            if symbol == 'B':
                # 历史长度足够，但缺周期首日，不能与 A 的旧收盘价拼窗。
                bars = [b for b in bars if b[0] != '2026-08-10']
            return bars

        todos = svc.due_todos(bars_of, today=date(2026, 8, 12))
        assert len(todos) == 1
        assert todos[0]['status'] == 'data_insufficient'
        assert todos[0]['action'] is None
        assert todos[0]['target'] is None
        assert todos[0]['momentum'] == {}
        assert todos[0]['missing_data'] == ['B']
        recovered = svc.due_todos(
            lambda s, d: _bars(s == 'A'), today=date(2026, 8, 12))
        assert recovered[0]['action'] == 'enter'
        assert recovered[0]['missing_data'] == []

    def test_midweek_first_start_and_restart_keep_first_day_signal(self, services):
        svc, trade = services
        _make_plan(svc)
        bars = {'A': _bars(True), 'B': _bars(False)}
        # 周三 B 已反超；若误用首次打开时间会选 B，正确口径仍用周一。
        bars['B'][-1] = ('2026-08-12', 10.0)

        first = svc.due_todos(lambda s, d: bars[s], today=date(2026, 8, 12))
        assert first[0]['target']['symbol'] == 'A'
        assert first[0]['signal_date'] == '2026-08-10'
        assert first[0]['market_date'] == '2026-08-10'
        assert first[0]['earliest_trade_date'] == '2026-08-11'

        restarted = RotationService(trade)
        after = restarted.due_todos(lambda s, d: bars[s], today=date(2026, 8, 12))
        assert after[0]['target']['symbol'] == 'A'
        assert after[0]['signal_date'] == first[0]['signal_date']
        assert after[0]['momentum'] == first[0]['momentum']

    def test_signal_executes_no_earlier_than_next_trade_date(self, services):
        svc, _ = services
        _make_plan(svc)
        monday = date(2026, 8, 10)
        pending = svc.due_todos(
            lambda s, d: _bars(s == 'A', end=monday), today=monday)
        assert pending[0]['status'] == 'awaiting_execution'
        assert pending[0]['action'] is None
        assert pending[0]['signal_date'] == '2026-08-10'
        assert pending[0]['earliest_trade_date'] is None

        tuesday = date(2026, 8, 11)
        executable = svc.due_todos(
            lambda s, d: _bars(s == 'A', end=tuesday), today=tuesday)
        assert executable[0]['action'] == 'enter'
        assert executable[0]['signal_date'] == '2026-08-10'
        assert executable[0]['earliest_trade_date'] == '2026-08-11'

    def test_paused_plan_no_todo(self, services):
        svc, _ = services
        p = _make_plan(svc)
        svc.update_status(p['id'], 'paused')
        assert svc.due_todos(lambda s, d: _bars(True), today=date(2026, 8, 12)) == []


class TestValidation:
    def test_pool_and_window(self, services):
        svc, _ = services
        with pytest.raises(ValueError, match='2-6'):
            svc.create_plan({'pool': [{'symbol': 'A'}]})
        with pytest.raises(ValueError, match='5-120'):
            svc.create_plan({'pool': _POOL, 'window': 3})
        with pytest.raises(ValueError, match='weekly'):
            svc.create_plan({'pool': _POOL, 'window': 20, 'rebalance': 'daily'})
