"""复盘服务测试：回合/已实现/留存统计与纪律违约判定"""
import pytest

from backend.models.database import Base
from backend.utils.db import engine, init_db
from backend.services.grid_service import GridService
from backend.services.trade_service import TradeService
from backend.services.portfolio_service import PortfolioService
from backend.services.review_service import ReviewService


@pytest.fixture(autouse=True)
def _clean_db():
    init_db()
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


class _FakeEtf:
    def daily_bars(self, symbol, days=250):
        raise AssertionError('测试应通过 bars_map 注入日线')


def _services():
    grid, trade, pf = GridService(), TradeService(), PortfolioService()
    return grid, trade, ReviewService(grid, trade, pf, _FakeEtf())


def _plan(grid):
    # G1 1.0/1.0526 · G2 0.95/1.0 · G3 0.9025/0.95
    return grid.create_plan({'name': '复盘测试', 'symbol': '510300', 'base_price': 1.0,
                             'grid_step': 5, 'grid_count': 3, 'amount_per_grid': 10000})


def _bars(seq):
    """[(YYYYMMDD, close)] 递增日期"""
    return [(f'202601{d:02d}', c) for d, c in enumerate(seq, start=1)]


class TestPlanStats:
    def test_rounds_realized_retained(self):
        grid, trade, svc = _services()
        p = _plan(grid)
        trade.add_trade({'plan_id': p['id'], 'symbol': '510300',
                         'trade_date': '2026-01-02', 'direction': 'buy',
                         'price': 1.0, 'shares': 10000, 'grid_level': 1})
        trade.add_trade({'plan_id': p['id'], 'symbol': '510300',
                         'trade_date': '2026-01-10', 'direction': 'sell',
                         'price': 1.0526, 'shares': 9800, 'grid_level': 1})
        r = svc.review(bars_map={'510300': []}, prices={'510300': 1.06})
        row = r['plans'][0]
        assert row['rounds'] == 1
        # 已实现 = (1.0526 - 1.0) * 9800 = 515.48
        assert row['realized_pnl'] == pytest.approx(515.48, abs=0.01)
        assert row['retained_shares'] == 200.0        # 买 10000 卖 9800，净剩即留存
        assert row['cells'] == {'wait': 2, 'hold': 0, 'sold': 0, 'keep': 1}
        assert r['totals']['rounds'] == 1

    def test_empty_plan_all_zero(self):
        grid, _, svc = _services()
        _plan(grid)
        r = svc.review(bars_map={'510300': []})
        row = r['plans'][0]
        assert row['rounds'] == 0 and row['missed_buy'] == 0
        assert row['cells']['wait'] == 3


class TestDiscipline:
    def test_missed_buy_counted(self):
        """价格穿越买价且窗口内无买入 → 该买没买；下行途中 G1、G2 各触发一波"""
        grid, _, svc = _services()
        _plan(grid)
        # G1 买价 1.0：第 1 日 1.0 ≤ 1.01 触发；G2 买价 0.95：第 3 日 0.949 ≤ 0.9595 触发
        bars = _bars([1.0, 0.97, 0.949, 0.96, 0.97, 0.98, 0.99, 1.0, 1.01, 1.02])
        r = svc.review(bars_map={'510300': bars})
        assert r['plans'][0]['missed_buy'] == 2  # G1、G2 各一波

    def test_buy_in_window_is_compliant(self):
        """触发后窗口内有对应买入 → 不违约"""
        grid, trade, svc = _services()
        p = _plan(grid)
        trade.add_trade({'plan_id': p['id'], 'symbol': '510300',
                         'trade_date': '2026-01-02', 'direction': 'buy',
                         'price': 1.0, 'shares': 100, 'grid_level': 1})   # G1 窗口内合规
        trade.add_trade({'plan_id': p['id'], 'symbol': '510300',
                         'trade_date': '2026-01-05', 'direction': 'buy',
                         'price': 0.95, 'shares': 100, 'grid_level': 2})  # G2 窗口内合规
        bars = _bars([1.0, 0.97, 0.949, 0.96, 0.97, 0.98, 0.99, 1.0, 1.01, 1.02])
        r = svc.review(bars_map={'510300': bars})
        assert r['plans'][0]['missed_buy'] == 0

    def test_missed_sell_requires_holding(self):
        """卖出纪律只评估买入过的档位：未持有的档穿越卖价不记违约"""
        grid, trade, svc = _services()
        p = _plan(grid)
        # G3 买入 → 卖价 0.95：第 3 日 0.9505 触发且无卖出 → 违约 1 次
        trade.add_trade({'plan_id': p['id'], 'symbol': '510300',
                         'trade_date': '2026-01-01', 'direction': 'buy',
                         'price': 0.9025, 'shares': 100, 'grid_level': 3})
        bars = _bars([0.90, 0.92, 0.9505, 0.94, 0.93, 0.92, 0.91, 0.90, 0.90, 0.90])
        r = svc.review(bars_map={'510300': bars})
        row = r['plans'][0]
        assert row['missed_sell'] == 1
        assert row['missed_buy'] == 2  # G1/G2 从未买入：下行穿越各自记一波（语义正确）

    def test_unheld_level_sell_cross_ignored(self):
        grid, _, svc = _services()
        _plan(grid)
        bars = _bars([0.90, 0.92, 0.9505, 0.94, 0.93, 0.92, 0.91, 0.90, 0.90, 0.90])
        r = svc.review(bars_map={'510300': bars})
        assert r['plans'][0]['missed_sell'] == 0  # 从未买入 → 不评估卖出纪律

    def test_no_cross_no_miss(self):
        grid, _, svc = _services()
        _plan(grid)
        bars = _bars([1.1, 1.12, 1.08, 1.11, 1.09, 1.10, 1.12, 1.13, 1.14, 1.15])
        r = svc.review(bars_map={'510300': bars})
        row = r['plans'][0]
        assert row['missed_buy'] == 0 and row['missed_sell'] == 0

    def test_no_bars_graceful(self):
        grid, _, svc = _services()
        _plan(grid)
        r = svc.review(bars_map={})  # 取不到日线 → 不崩溃、不违约
        assert r['plans'][0]['missed_buy'] == 0
