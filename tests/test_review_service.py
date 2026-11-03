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
    def daily_raw_bars(self, symbol, days=250):
        raise AssertionError('测试应通过 bars_map 注入原始日线')

    def daily_bars(self, symbol, days=250):
        raise AssertionError('执行复盘不得读取前复权日线')


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
        """原始价触及买价且七个交易日内无买入 → 该买没买"""
        grid, _, svc = _services()
        _plan(grid)
        bars = _bars([1.0, 0.97, 0.949, 0.96, 0.97, 0.98, 0.99, 1.0, 1.01, 1.02])
        r = svc.review(bars_map={'510300': bars})
        assert r['plans'][0]['missed_buy'] == 2

    def test_buy_in_window_is_compliant(self):
        grid, trade, svc = _services()
        p = _plan(grid)
        trade.add_trade({'plan_id': p['id'], 'symbol': '510300',
                         'trade_date': '2026-01-02', 'direction': 'buy',
                         'price': 1.0, 'shares': 100, 'grid_level': 1})
        trade.add_trade({'plan_id': p['id'], 'symbol': '510300',
                         'trade_date': '2026-01-05', 'direction': 'buy',
                         'price': 0.95, 'shares': 100, 'grid_level': 2})
        bars = _bars([1.0, 0.97, 0.949, 0.96, 0.97, 0.98, 0.99, 1.0, 1.01, 1.02])
        r = svc.review(bars_map={'510300': bars})
        assert r['plans'][0]['missed_buy'] == 0

    def test_missed_sell_requires_holding(self):
        grid, trade, svc = _services()
        p = _plan(grid)
        trade.add_trade({'plan_id': p['id'], 'symbol': '510300',
                         'trade_date': '2026-01-01', 'direction': 'buy',
                         'price': 0.9025, 'shares': 100, 'grid_level': 3})
        bars = _bars([0.90, 0.92, 0.9505, 0.94, 0.93, 0.92, 0.91, 0.90, 0.90, 0.90])
        r = svc.review(bars_map={'510300': bars})
        row = r['plans'][0]
        assert row['missed_sell'] == 1
        # G1 一段；G2 在第1日、第4日各起一段独立波次，均满七日未买。
        assert row['missed_buy'] == 3

    def test_unheld_level_sell_cross_ignored(self):
        grid, _, svc = _services()
        _plan(grid)
        bars = _bars([0.90, 0.92, 0.9505, 0.94, 0.93, 0.92, 0.91, 0.90, 0.90, 0.90])
        r = svc.review(bars_map={'510300': bars})
        assert r['plans'][0]['missed_sell'] == 0

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
        r = svc.review(bars_map={})
        assert r['plans'][0]['missed_buy'] == 0

    def test_recent_trigger_is_pending_not_missed(self):
        grid, _, svc = _services()
        _plan(grid)
        r = svc.review(bars_map={'510300': _bars([1.0, 0.99, 0.98])})
        row = r['plans'][0]
        assert row['missed_buy'] == 0
        assert row['pending_buy'] == 1
        assert r['totals']['pending_buy'] == 1

    def test_trigger_day_counts_as_window_day_one(self):
        grid, trade, svc = _services()
        p = _plan(grid)
        trade.add_trade({'plan_id': p['id'], 'symbol': '510300',
                         'trade_date': '2026-01-01', 'direction': 'buy',
                         'price': 1.0, 'shares': 100, 'grid_level': 1})
        r = svc.review(bars_map={'510300': _bars([1.0])})
        row = r['plans'][0]
        assert row['missed_buy'] == 0 and row['pending_buy'] == 0

    def test_seventh_day_execution_is_compliant(self):
        grid, trade, svc = _services()
        p = _plan(grid)
        trade.add_trade({'plan_id': p['id'], 'symbol': '510300',
                         'trade_date': '2026-01-07', 'direction': 'buy',
                         'price': 1.0, 'shares': 100, 'grid_level': 1})
        r = svc.review(bars_map={'510300': _bars([1.0] * 7)})
        row = r['plans'][0]
        assert row['missed_buy'] == 0 and row['pending_buy'] == 0

    def test_completed_seventh_day_without_execution_is_missed(self):
        grid, trade, svc = _services()
        p = _plan(grid)
        trade.add_trade({'plan_id': p['id'], 'symbol': '510300',
                         'trade_date': '2026-01-08', 'direction': 'buy',
                         'price': 1.0, 'shares': 100, 'grid_level': 1})
        r = svc.review(bars_map={'510300': _bars([1.0] * 8)})
        assert r['plans'][0]['missed_buy'] == 1  # 第 8 日成交不能补救已结束窗口

    def test_one_percent_tolerance_does_not_trigger_early(self):
        grid, _, svc = _services()
        _plan(grid)
        r = svc.review(bars_map={'510300': _bars([1.009] * 7)})
        row = r['plans'][0]
        assert row['missed_buy'] == 0 and row['pending_buy'] == 0

    def test_review_uses_raw_prices_not_latest_adjusted_history(self):
        class RawOnlyEtf:
            def __init__(self):
                self.raw_calls = 0

            def daily_raw_bars(self, symbol, days=250):
                self.raw_calls += 1
                return _bars([1.0] * 7)

            def daily_bars(self, symbol, days=250):
                raise AssertionError('最新复权因子不得改写历史执行纪律')

        grid, _, svc = _services()
        _plan(grid)
        raw = RawOnlyEtf()
        svc.etf = raw
        r = svc.review()
        assert raw.raw_calls == 1
        assert r['plans'][0]['missed_buy'] == 1
        assert r['plans'][0]['price_basis'] == 'raw'
