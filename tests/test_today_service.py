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


class _QuoteEtf:
    def __init__(self, quote=None, corporate=None):
        self.quote = quote
        self.corporate = corporate
        self.corporate_calls = []

    def quotes(self, symbols):
        return {s: self.quote for s in symbols}

    def corporate_action_check(self, symbol, since_date):
        self.corporate_calls.append((symbol, since_date))
        return self.corporate


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

    def test_crossed_shallower_buy_wins_over_deeper_wait(self):
        """跳空穿越浅档时，待办应指向已穿越的浅档，而不是更深的未触发档"""
        grid, _, svc = _make_today()
        _add_plan(grid)  # G1 1.15 / G2 1.081 / G3 1.01614
        # 现价 1.05：G2 已穿越（距 2.95%），G3 还未到（距 3.23%）
        r = svc.today(near_pct=3.0, prices={'512880': 1.05})
        buys = [t for t in r['todos'] if t['direction'] == 'buy']
        assert len(buys) == 1 and buys[0]['level'] == 2

    def test_sold_level_can_buy_again(self):
        """完成一轮买卖的档位回到买价附近时，应能再次产生买入待办（网格循环）"""
        grid, trade, svc = _make_today()
        p = _add_plan(grid)
        trade.add_trade({'plan_id': p['id'], 'symbol': '512880',
                         'trade_date': '2026-07-10', 'direction': 'buy',
                         'price': 1.15, 'shares': 100, 'grid_level': 1})
        trade.add_trade({'plan_id': p['id'], 'symbol': '512880',
                         'trade_date': '2026-07-11', 'direction': 'sell',
                         'price': 1.2234, 'shares': 100, 'grid_level': 1})
        r = svc.today(near_pct=2.0, prices={'512880': 1.15})
        buys = [t for t in r['todos'] if t['direction'] == 'buy']
        assert len(buys) == 1 and buys[0]['level'] == 1

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

    def test_uncovered_partial_sell_todo_caps_to_remaining_shares(self):
        """亏损部分卖出后仍持有，不能把计划卖出份额再次超卖。"""
        grid, trade, svc = _make_today()
        p = _add_plan(grid)
        trade.add_trade({'plan_id': p['id'], 'symbol': '512880',
                         'trade_date': '2026-07-10', 'direction': 'buy',
                         'price': 1.15, 'shares': 1000, 'grid_level': 1})
        trade.add_trade({'plan_id': p['id'], 'symbol': '512880',
                         'trade_date': '2026-07-11', 'direction': 'sell',
                         'price': 1.0, 'shares': 900, 'grid_level': 1})
        r = svc.today(near_pct=2.0, prices={'512880': 1.22})
        sells = [t for t in r['todos'] if t['direction'] == 'sell']
        assert len(sells) == 1
        assert sells[0]['level'] == 1 and sells[0]['shares'] == 100
        assert sells[0]['amount'] == round(sells[0]['price'] * 100, 2)

    def test_no_prices_no_todos(self):
        grid, _, svc = _make_today()
        _add_plan(grid)
        r = svc.today(prices={})
        assert r['todos'] == []
        assert len(r['health']) == 1

    def test_paused_plan_no_todos(self):
        """回归：暂停的计划不产生买卖待办（健康度仍保留）"""
        grid, _, svc = _make_today()
        _add_plan(grid, status='paused')
        r = svc.today(near_pct=2.0, prices={'512880': 1.14})  # 距 G1 买卖价均 ≤2%
        assert r['todos'] == []
        assert len(r['health']) == 1

    def test_broken_plan_no_todos_but_alert(self):
        """回归：破网装死=不再挂新单，不产生待办；破网预警灯保留"""
        grid, _, svc = _make_today()
        _add_plan(grid, status='broken')
        r = svc.today(near_pct=2.0, prices={'512880': 1.0})  # 低于最低档 → 破网
        assert r['todos'] == []
        assert len(r['alerts']['broken']) == 1


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


class TestExitAlerts:
    def test_warn_tier_at_75(self):
        rows = [{'name': '证券Ⅱ', 'valuation_percentile': 75.0}]
        grid, _, svc = _make_today(rows)
        _add_plan(grid)
        r = svc.today(prices={'512880': 1.10})
        exits = r['alerts']['exit']
        assert len(exits) == 1
        assert exits[0]['tier'] == 'warn'
        assert exits[0]['index_name'] == '证券Ⅱ'
        assert '只卖不买' in exits[0]['verdict']

    def test_exit_tier_at_85(self):
        rows = [{'name': '证券Ⅱ', 'valuation_percentile': 85.0}]
        grid, _, svc = _make_today(rows)
        _add_plan(grid)
        r = svc.today(prices={'512880': 1.10})
        exits = r['alerts']['exit']
        assert len(exits) == 1
        assert exits[0]['tier'] == 'exit'
        assert '收网' in exits[0]['verdict']

    def test_no_exit_alert_at_65(self):
        rows = [{'name': '证券Ⅱ', 'valuation_percentile': 65.0}]
        grid, _, svc = _make_today(rows)
        _add_plan(grid)
        r = svc.today(prices={'512880': 1.10})
        assert r['alerts']['exit'] == []

    def test_unmatched_symbol_no_exit_alert(self):
        rows = [{'name': '证券Ⅱ', 'valuation_percentile': 90.0}]
        grid, _, svc = _make_today(rows)
        _add_plan(grid, name='纳指网格', symbol='513100', symbol_name='纳指ETF')
        r = svc.today(prices={'513100': 1.10})
        assert r['alerts']['exit'] == []

    def test_closed_plan_excluded(self):
        rows = [{'name': '证券Ⅱ', 'valuation_percentile': 90.0}]
        grid, _, svc = _make_today(rows)
        _add_plan(grid, status='closed')
        r = svc.today(prices={'512880': 1.10})
        assert r['alerts']['exit'] == []


class TestDcaTodos:
    def _svc_with_dca(self, readiness_rows=()):
        from backend.services.dca_service import DcaService
        grid, trade = GridService(), TradeService()
        dca = DcaService(trade)
        svc = TodayService(grid, trade, _FakeEtf(),
                           _FakeReadiness(list(readiness_rows)),
                           PortfolioService(), dca)
        return grid, trade, dca, svc

    def test_dca_todo_appears_in_today(self):
        rows = [{'name': '证券Ⅱ', 'valuation_percentile': 15.0}]
        grid, trade, dca, svc = self._svc_with_dca(rows)
        dca.create_plan({'name': '证券定投', 'symbol': '512880',
                         'symbol_name': '证券ETF', 'base_amount': 1000,
                         'frequency': 'weekly'})
        r = svc.today(prices={'512880': 1.10})
        assert len(r['dca_todos']) == 1
        t = r['dca_todos'][0]
        assert t['multiplier'] == 2.0 and t['amount'] == 2000  # 15% → 低估加倍

    def test_dca_todo_gone_after_invested(self):
        rows = [{'name': '证券Ⅱ', 'valuation_percentile': 15.0}]
        from datetime import date
        grid, trade, dca, svc = self._svc_with_dca(rows)
        p = dca.create_plan({'name': '证券定投', 'symbol': '512880',
                             'symbol_name': '证券ETF', 'base_amount': 1000,
                             'frequency': 'weekly'})
        trade.add_trade({'symbol': '512880', 'symbol_name': '证券ETF',
                         'trade_date': date.today().isoformat(), 'direction': 'buy',
                         'price': 1.0, 'shares': 2000, 'dca_plan_id': p['id']})
        r = svc.today(prices={'512880': 1.10})
        assert r['dca_todos'] == []

    def test_no_dca_service_backward_compatible(self):
        grid, _, svc = _make_today()  # 不注入 dca_service
        _add_plan(grid)
        r = svc.today(prices={'512880': 1.10})
        assert r['dca_todos'] == []


class TestMarketDataSafety:
    def test_stale_quote_keeps_date_but_blocks_todo_and_broken_alert(self):
        grid, _, svc = _make_today()
        _add_plan(grid)
        svc.etf = _QuoteEtf(quote={
            'ts_code': '512880.SH', 'close': 1.0, 'trade_date': '20260710',
            'stale': True, 'stale_reason': 'not_current_trade_date',
            'as_of_date': '2026-07-11',
        })
        r = svc.today(near_pct=2.0)
        assert r['todos'] == []
        assert r['alerts']['broken'] == []
        assert r['quotes']['512880']['trade_date'] == '20260710'
        assert r['quotes']['512880']['stale'] is True
        assert r['health'][0]['quote_date'] == '20260710'
        assert r['health'][0]['stale_reason'] == 'not_current_trade_date'
        assert r['health'][0]['next_buy'] is None
        assert r['health'][0]['next_sell'] is None

    def test_factor_change_pauses_old_grid_and_requires_review(self):
        grid, _, svc = _make_today()
        p = _add_plan(grid)
        changes = [{'trade_date': '20260711', 'old_factor': 1.0, 'new_factor': 2.0}]
        svc.etf = _QuoteEtf(corporate={
            'ts_code': '512880.SH', 'since_date': '20260710',
            'as_of_date': '20260711', 'status': 'ok', 'has_changed': True,
            'pause_required': True, 'changes': changes, 'latest_factor': 2.0,
            'message': None,
        })
        r = svc.today(near_pct=2.0, prices={'512880': 1.15})
        assert grid.get_plan(p['id'])['status'] == 'paused'
        assert r['todos'] == []
        alert = r['alerts']['corporate_action'][0]
        assert alert['needs_review'] is True
        assert alert['has_changed'] is True
        assert alert['changes'] == changes
        created_at = grid.get_plan(p['id'])['created_at']
        assert svc.etf.corporate_calls == [('512880', created_at[:10])]

    def test_unavailable_factor_check_fails_closed(self):
        grid, _, svc = _make_today()
        p = _add_plan(grid)
        svc.etf = _QuoteEtf(corporate={
            'ts_code': '512880.SH', 'since_date': '20260710',
            'as_of_date': '20260711', 'status': 'unavailable',
            'has_changed': None, 'pause_required': True, 'changes': [],
            'latest_factor': None, 'message': '复权因子缺失',
        })
        r = svc.today(near_pct=2.0, prices={'512880': 1.15})
        assert grid.get_plan(p['id'])['status'] == 'paused'
        assert r['todos'] == []
        alert = r['alerts']['corporate_action'][0]
        assert alert['has_changed'] is None
        assert alert['needs_review'] is True
