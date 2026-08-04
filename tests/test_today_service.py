"""今日视图测试：待办生成、预警三灯、指数名匹配"""
import pytest

from backend.models.database import Base
from backend.utils.db import engine, init_db
from backend.services.grid_service import GridService
from backend.services.trade_service import TradeService
from backend.services.portfolio_service import PortfolioService
from backend.services.today_service import TodayService, match_index_name


@pytest.fixture(autouse=True)
def _clean_db():
    init_db()
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


class _FakeReadiness:
    def __init__(self, rows):
        self._rows = rows

    def assess_all(self):
        return self._rows


class _FakeEtf:  # 不会被调用：测试都注入 prices
    def quotes(self, symbols):
        raise AssertionError('测试不应触发网络取价')


def _make_today(readiness_rows=()):
    grid, trade, pf = GridService(), TradeService(), PortfolioService()
    svc = TodayService(grid, trade, _FakeEtf(), _FakeReadiness(list(readiness_rows)), pf)
    return grid, trade, svc


def _add_plan(grid, name='证券网格', symbol='512880', symbol_name='证券ETF',
              base=1.15, status='active'):
    p = grid.create_plan({'name': name, 'symbol': symbol, 'symbol_name': symbol_name,
                          'base_price': base, 'grid_step': 6, 'grid_count': 3,
                          'amount_per_grid': 8000})
    if status != 'active':
        grid.update_status(p['id'], status)
    return p


class TestMatchIndexName:
    def test_longest_substring_wins(self):
        names = ['沪深300', '银行', '证券Ⅱ', '国有大型银行Ⅱ']
        assert match_index_name('沪深300ETF', names) == '沪深300'
        assert match_index_name('证券ETF', names) == '证券Ⅱ'
        assert match_index_name('银行ETF', names) == '银行'
        assert match_index_name('国有大行ETF', names) is None  # 非子串不误配
        assert match_index_name('红利ETF', names) is None
        assert match_index_name('', names) is None


class TestTodos:
    def test_next_buy_todo_execution_aware(self):
        grid, trade, svc = _make_today()
        # base 1.15, step 6%: G1 1.15 / G2 1.081 / G3 1.01614
        p = _add_plan(grid)
        # G2 已买入 → 待办的下一买应是 G3，而非 G2
        trade.add_trade({'plan_id': p['id'], 'symbol': '512880',
                         'trade_date': '2026-07-10', 'direction': 'buy',
                         'price': 1.081, 'shares': 100})
        r = svc.today(near_pct=2.0, prices={'512880': 1.02})
        buys = [t for t in r['todos'] if t['direction'] == 'buy']
        assert len(buys) == 1 and buys[0]['level'] == 3
        # G2 已持有 → 卖档候选为 G2 卖价 1.15；1.14586 距 1.02 超 2%，不入列
        sells = [t for t in r['todos'] if t['direction'] == 'sell']
        assert sells == []

    def test_sell_todo_when_near(self):
        grid, trade, svc = _make_today()
        p = _add_plan(grid)
        trade.add_trade({'plan_id': p['id'], 'symbol': '512880',
                         'trade_date': '2026-07-10', 'direction': 'buy',
                         'price': 1.081, 'shares': 100})  # G2 持有，卖价 1.15
        r = svc.today(near_pct=2.0, prices={'512880': 1.14})
        sells = [t for t in r['todos'] if t['direction'] == 'sell']
        assert len(sells) == 1 and sells[0]['level'] == 2
        assert sells[0]['dist_pct'] <= 2.0

    def test_no_prices_no_todos(self):
        grid, _, svc = _make_today()
        _add_plan(grid)
        r = svc.today(prices={})
        assert r['todos'] == []
        assert len(r['health']) == 1


class TestAlerts:
    def test_broken_alert_by_price(self):
        grid, _, svc = _make_today()
        _add_plan(grid)
        r = svc.today(prices={'512880': 1.0})  # 最低档 G3 买价 1.01614 → 破网
        assert len(r['alerts']['broken']) == 1
        assert r['alerts']['high'] == []

    def test_high_alert(self):
        grid, _, svc = _make_today()
        _add_plan(grid)
        r = svc.today(prices={'512880': 1.20})  # 高于基准 1.15 → 高位运行
        assert len(r['alerts']['high']) == 1
        assert r['alerts']['broken'] == []

    def test_valuation_alert_over_50pct(self):
        rows = [{'name': '证券Ⅱ', 'valuation_percentile': 68.0, 'verdict': '不建议（估值偏高）'}]
        grid, _, svc = _make_today(rows)
        _add_plan(grid)
        r = svc.today(prices={'512880': 1.10})
        assert len(r['alerts']['valuation']) == 1
        assert r['alerts']['valuation'][0]['index_name'] == '证券Ⅱ'

    def test_valuation_alert_skipped_under_50(self):
        rows = [{'name': '证券Ⅱ', 'valuation_percentile': 22.0, 'verdict': '适合开启'}]
        grid, _, svc = _make_today(rows)
        _add_plan(grid)
        r = svc.today(prices={'512880': 1.10})
        assert r['alerts']['valuation'] == []
