"""执行层测试：档位自动匹配、棋盘状态推导、破网处置"""
import pytest

from backend.models.database import Base
from backend.utils.db import engine, init_db
from backend.services.grid_service import (
    GridService, derive_level_states, generate_levels, match_grid_level,
)
from backend.services.trade_service import TradeService


@pytest.fixture(autouse=True)
def _clean_db():
    init_db()
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture()
def services():
    return GridService(), TradeService()


_LEVELS = generate_levels(1.0, 5, 3, 10000)  # G1 1.0/1.0526, G2 0.95/1.0, G3 0.9025/0.95


class TestMatchGridLevel:
    def test_buy_matches_nearest_buy_price(self):
        assert match_grid_level(_LEVELS, 'buy', 0.951) == 2      # 0.95 ±1.5% 内
        assert match_grid_level(_LEVELS, 'buy', 1.001) == 1      # 1.0 附近

    def test_sell_matches_nearest_sell_price(self):
        assert match_grid_level(_LEVELS, 'sell', 1.05) == 1      # G1 卖价 1.0526
        assert match_grid_level(_LEVELS, 'sell', 0.949) == 3     # G3 卖价 0.95

    def test_out_of_tolerance_returns_none(self):
        assert match_grid_level(_LEVELS, 'buy', 0.85) is None    # 距 G3 0.9025 超 5%
        assert match_grid_level([], 'buy', 1.0) is None
        assert match_grid_level(_LEVELS, 'buy', 0) is None


class _T:  # 模拟 TradeTable 行
    def __init__(self, direction, shares, grid_level):
        self.direction, self.shares, self.grid_level = direction, shares, grid_level


class TestDeriveLevelStates:
    def test_four_states(self):
        trades = [
            _T('buy', 100, 1),                     # G1 已卖
            _T('sell', 100, 1),
            _T('buy', 200, 2), _T('sell', 100, 2),  # G2 留存（净剩 100）
            _T('buy', 300, 3),                      # G3 持有
        ]
        assert derive_level_states(_LEVELS, trades) == ['sold', 'keep', 'hold']

    def test_all_wait_when_no_trades(self):
        assert derive_level_states(_LEVELS, []) == ['wait', 'wait', 'wait']

    def test_trades_without_level_ignored(self):
        trades = [_T('buy', 100, None)]
        assert derive_level_states(_LEVELS, trades) == ['wait', 'wait', 'wait']


class TestAddTradeAutoMatch:
    def test_auto_match_on_plan_trade(self, services):
        grid, trade = services
        plan = grid.create_plan({'name': '测试', 'symbol': '510300', 'base_price': 1.0,
                                 'grid_step': 5, 'grid_count': 3, 'amount_per_grid': 10000})
        t = trade.add_trade({'plan_id': plan['id'], 'symbol': '510300',
                             'trade_date': '2026-07-01', 'direction': 'buy',
                             'price': 0.951, 'shares': 100})
        assert t['grid_level'] == 2  # 0.951 ≈ G2 买价 0.95

    def test_explicit_level_not_overwritten(self, services):
        grid, trade = services
        plan = grid.create_plan({'name': '测试', 'symbol': '510300', 'base_price': 1.0,
                                 'grid_step': 5, 'grid_count': 3, 'amount_per_grid': 10000})
        t = trade.add_trade({'plan_id': plan['id'], 'symbol': '510300',
                             'trade_date': '2026-07-01', 'direction': 'buy',
                             'price': 0.951, 'shares': 100, 'grid_level': 3})
        assert t['grid_level'] == 3

    def test_planless_trade_stays_levelless(self, services):
        _, trade = services
        t = trade.add_trade({'symbol': '510300', 'trade_date': '2026-07-01',
                             'direction': 'buy', 'price': 1.0, 'shares': 100})
        assert t['grid_level'] is None

    def test_get_plan_includes_level_states(self, services):
        grid, trade = services
        plan = grid.create_plan({'name': '测试', 'symbol': '510300', 'base_price': 1.0,
                                 'grid_step': 5, 'grid_count': 3, 'amount_per_grid': 10000})
        trade.add_trade({'plan_id': plan['id'], 'symbol': '510300',
                         'trade_date': '2026-07-01', 'direction': 'buy',
                         'price': 1.0, 'shares': 100})
        d = grid.get_plan(plan['id'])
        assert d['level_states'] == ['hold', 'wait', 'wait']


class TestBreakAction:
    def _plan(self, grid):
        return grid.create_plan({'name': '证券网格', 'symbol': '512880', 'base_price': 1.15,
                                 'grid_step': 6, 'grid_count': 10, 'amount_per_grid': 8000})

    def test_hold_marks_broken(self, services):
        grid, _ = services
        p = self._plan(grid)
        r = grid.break_action(p['id'], 'hold')
        assert r['action'] == 'hold'
        assert grid.get_plan(p['id'])['status'] == 'broken'

    def test_stop_marks_closed(self, services):
        grid, _ = services
        p = self._plan(grid)
        grid.break_action(p['id'], 'stop')
        assert grid.get_plan(p['id'])['status'] == 'closed'

    def test_extend_creates_followup_plan(self, services):
        grid, _ = services
        p = self._plan(grid)
        r = grid.break_action(p['id'], 'extend', new_base_price=0.842)
        assert r['action'] == 'extend'
        old = grid.get_plan(p['id'])
        new = grid.get_plan(r['new_plan_id'])
        assert old['status'] == 'broken'
        assert new['status'] == 'active'
        assert new['name'] == '证券网格·接网'
        assert new['base_price'] == 0.842
        assert new['grid_step'] == old['grid_step']  # 原参数延续
        assert new['levels'][0]['buy_price'] == 0.842

    def test_extend_requires_price(self, services):
        grid, _ = services
        p = self._plan(grid)
        with pytest.raises(ValueError):
            grid.break_action(p['id'], 'extend')

    def test_unknown_action_and_missing_plan(self, services):
        grid, _ = services
        with pytest.raises(ValueError):
            grid.break_action(1, 'freeze')
        assert grid.break_action(999, 'hold') is None


class TestDeletePlan:
    def test_delete_unlinks_trades_to_core(self, services):
        """回归：删除计划不连带删除成交——解绑为底仓，账本不随计划蒸发"""
        grid, trade = services
        plan = grid.create_plan({'name': '测试', 'symbol': '510300', 'base_price': 1.0,
                                 'grid_step': 5, 'grid_count': 3, 'amount_per_grid': 10000})
        t = trade.add_trade({'plan_id': plan['id'], 'symbol': '510300',
                             'trade_date': '2026-07-01', 'direction': 'buy',
                             'price': 1.0, 'shares': 100})
        assert t['grid_level'] == 1

        r = grid.delete_plan(plan['id'])
        assert r == {'ok': True, 'unlinked_trades': 1}
        assert grid.get_plan(plan['id']) is None
        kept = trade.list_trades(symbol='510300')
        assert len(kept) == 1                        # 成交保留
        assert kept[0]['plan_id'] is None            # 解绑 → 落入底仓
        assert kept[0]['grid_level'] is None
        pos = trade.get_positions()
        assert pos[0]['shares'] == 100               # 持仓账本完好

    def test_delete_missing_plan(self, services):
        grid, _ = services
        assert grid.delete_plan(999) is None

    def test_delete_planless_no_unlink_count(self, services):
        grid, _ = services
        plan = grid.create_plan({'name': '空仓', 'symbol': '510300', 'base_price': 1.0,
                                 'grid_step': 5, 'grid_count': 3, 'amount_per_grid': 10000})
        assert grid.delete_plan(plan['id']) == {'ok': True, 'unlinked_trades': 0}
