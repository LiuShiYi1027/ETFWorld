"""策略实验室测试：轮动回测、对比装配、研究笔记"""
from datetime import date, timedelta

import pytest

from backend.models.database import Base
from backend.utils.db import engine, init_db
from backend.services.backtest_service import simulate_rotation

ZERO_COSTS = {'fee_rate': 0, 'min_fee': 0, 'slippage_rate': 0}


def _series(start_date, n, daily_ret_fn, price0=1.0):
    """生成 n 天价格序列，daily_ret_fn(i) 为第 i 日相对前一日的收益率"""
    rows, p = [], price0
    for i in range(n):
        if i > 0:
            p = round(p * (1 + daily_ret_fn(i)), 4)
        rows.append(((start_date + timedelta(days=i)).strftime('%Y%m%d'), p))
    return rows


class TestRotation:
    def test_switches_to_stronger_symbol(self):
        """A 先强（日涨1%）30 天后走平，B 后起（日涨2%）→ 应先持 A 后切 B"""
        a = _series(date(2026, 1, 1), 70, lambda i: 0.01 if i <= 30 else 0.0)
        b = _series(date(2026, 1, 1), 70, lambda i: 0.02 if i > 30 else 0.0)
        r = simulate_rotation({'A': a, 'B': b}, window=20,
                              rebalance='weekly', budget=100000, **ZERO_COSTS)
        buys = [e for e in r['events'] if e['dir'] == 'buy']
        sells = [e for e in r['events'] if e['dir'] == 'sell']
        assert buys[0]['symbol'] == 'A'          # 先买强者 A
        assert any(e['symbol'] == 'B' for e in buys)  # 后切到 B
        assert any(e['symbol'] == 'A' for e in sells)
        assert r['switches'] >= 2
        assert r['ret'] > 0
        assert r['holding'] == 'B'

    def test_all_negative_momentum_goes_cash(self):
        """全部品种动量为负 → 空仓持币，账户不随下跌缩水"""
        down = _series(date(2026, 1, 1), 60, lambda i: -0.01)
        down2 = _series(date(2026, 1, 1), 60, lambda i: -0.005, price0=2.0)
        r = simulate_rotation({'A': down, 'B': down2}, window=20,
                              rebalance='weekly', budget=100000, **ZERO_COSTS)
        assert r['holding'] is None              # 期末空仓
        assert not any(e['dir'] == 'buy' for e in r['events'])  # 从未买入
        assert r['final_value'] == 100000.0      # 账户纹丝不动
        assert r['max_dd'] == 0

    def test_date_intersection_alignment(self):
        """两只品种日期不齐时按交集对齐"""
        a = _series(date(2026, 1, 1), 50, lambda i: 0.001)
        b = _series(date(2026, 1, 20), 40, lambda i: 0.002)  # 晚 19 天开始
        r = simulate_rotation({'A': a, 'B': b}, window=10,
                              rebalance='weekly', budget=100000, **ZERO_COSTS)
        assert r['dates'][0] == '20260120'       # 交集从 B 的首日开始
        assert len(r['account']) == len(r['dates'])

    def test_too_few_symbols_or_short_window(self):
        a = _series(date(2026, 1, 1), 30, lambda i: 0.001)
        assert simulate_rotation({'A': a}, window=10, **ZERO_COSTS)['switches'] == 0
        assert simulate_rotation({'A': a, 'B': a}, window=100, **ZERO_COSTS)['switches'] == 0

    def test_signal_executes_on_next_bar_at_next_bar_price(self):
        dates = [(date(2026, 1, 1) + timedelta(days=i)).strftime('%Y%m%d')
                 for i in range(5)]
        a = list(zip(dates, [1.0, 1.1, 1.2, 1.3, 1.4]))
        b = list(zip(dates, [1.0] * 5))
        r = simulate_rotation({'A': a, 'B': b}, window=2,
                              rebalance='weekly', budget=10000, **ZERO_COSTS)
        buy = next(e for e in r['events'] if e['dir'] == 'buy')
        assert buy['signal_i'] == 2
        assert buy['i'] == 3
        assert buy['price'] == 1.3
        assert r['account'][2] == 10000  # 信号日尚未成交，不偷吃当日涨幅

    def test_rotation_fee_caps_lot_and_reduces_final_account(self):
        dates = [(date(2026, 1, 1) + timedelta(days=i)).strftime('%Y%m%d')
                 for i in range(5)]
        a = list(zip(dates, [0.9, 0.95, 1.0, 1.0, 1.0]))
        b = list(zip(dates, [1.0] * 5))
        r = simulate_rotation({'A': a, 'B': b}, window=2,
                              rebalance='weekly', budget=10000,
                              fee_rate=0, min_fee=5, slippage_rate=0)
        buy = next(e for e in r['events'] if e['dir'] == 'buy')
        assert buy['shares'] == 9900
        assert buy['fee'] == 5
        assert r['final_value'] == 9995
        assert r['ret'] == -0.05


class TestCompare:
    @pytest.fixture(autouse=True)
    def _clean_db(self):
        init_db()
        with engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                conn.execute(table.delete())

    def _bars_of(self, symbol, days):
        # 注入假行情：A 缓慢上行，B 横盘，其它缓跌
        fn = {'A': lambda i: 0.001, 'B': lambda i: 0.0}.get(
            symbol, lambda i: -0.0005)
        return _series(date(2025, 1, 2), days, fn)

    def test_single_compare_three_strategies(self):
        from backend.services.lab_service import LabService
        r = LabService().compare(
            {'kind': 'single', 'symbol': 'A', 'symbol_name': '测试A',
             'strategies': ['hold', 'grid', 'dca'], 'lookback_days': 300,
             **ZERO_COSTS},
            self._bars_of)
        assert r['kind'] == 'single'
        keys = [s['key'] for s in r['series']]
        assert keys == ['hold', 'grid', 'dca']
        for s in r['series']:
            assert len(s['nav']) == len(r['dates'])  # 横轴对齐
            assert abs(s['nav'][0] - 1.0) < 0.01     # 净值起点归一
            assert s['stats']['ret'] is not None

    def test_rotation_compare_with_reference_lines(self):
        from backend.services.lab_service import LabService
        r = LabService().compare(
            {'kind': 'rotation',
             'pool': [{'symbol': 'A'}, {'symbol': 'B'}],
             'window': 10, 'rebalance': 'weekly', 'lookback_days': 300,
             **ZERO_COSTS},
            self._bars_of)
        keys = [s['key'] for s in r['series']]
        assert 'rotation' in keys and 'hold_A' in keys and 'hold_B' in keys
        assert all(len(s['nav']) == len(r['dates']) for s in r['series'])

    def test_grid_obeys_shared_budget_before_computing_final_value(self):
        from backend.services.lab_service import LabService
        bars = [((date(2025, 1, 2) + timedelta(days=i)).strftime('%Y%m%d'),
                 1.0 if i == 0 else 0.95) for i in range(60)]
        r = LabService().compare(
            {'kind': 'single', 'symbol': 'A', 'strategies': ['grid'],
             'lookback_days': 60, 'budget': 15000,
             'grid': {'grid_step': 5, 'grid_count': 3, 'amount_per_grid': 10000},
             **ZERO_COSTS}, lambda symbol, days: bars)
        grid = r['series'][0]
        buys = [e for e in grid['events'] if e['dir'] == 'buy']
        assert len(buys) == 1  # 第二格需 9500，剩余现金仅 5000，不得透支
        assert grid['stats']['final_value'] == 14500
        assert grid['stats']['ret'] == -3.33
        assert r['cost_assumptions']['fee_rate'] == 0

    def test_rotation_pool_size_validation(self):
        from backend.services.lab_service import LabService
        with pytest.raises(ValueError, match='2-6'):
            LabService().compare({'kind': 'rotation', 'pool': [{'symbol': 'A'}]},
                                 self._bars_of)


class TestResearchNotes:
    @pytest.fixture(autouse=True)
    def _clean_db(self):
        init_db()
        with engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                conn.execute(table.delete())

    def test_save_list_delete(self):
        from backend.services.lab_service import LabService
        svc = LabService()
        with pytest.raises(ValueError):
            svc.save_note({'title': '', 'spec': {}})
        n = svc.save_note({'title': '银行三策略对擂',
                           'spec': {'kind': 'single', 'symbol': '512800.SH'},
                           'stats': {'grid': 7.9}, 'note': '网格跑输持有'})
        assert n['id']
        rows = svc.list_notes()
        assert len(rows) == 1 and rows[0]['spec']['symbol'] == '512800.SH'
        assert svc.delete_note(n['id']) is True
        assert svc.list_notes() == []
