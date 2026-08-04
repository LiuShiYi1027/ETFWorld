"""组合层测试：资金流水、三账户聚合、安全线

数据库由 tests/conftest.py 隔离到临时库，这里直接 init_db 建表即可。
"""
import pytest

from backend.models.database import Base
from backend.utils.db import engine, init_db
from backend.services.portfolio_service import PortfolioService
from backend.services.grid_service import GridService
from backend.services.trade_service import TradeService


@pytest.fixture(autouse=True)
def _clean_db():
    """每个用例前清空全部表（引擎级共享临时库，见 conftest）"""
    init_db()
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture()
def services():
    return PortfolioService(), GridService(), TradeService()


def _make_plan(grid, symbol='510300', name='测试网格'):
    return grid.create_plan({
        'name': name, 'symbol': symbol, 'base_price': 4.2, 'grid_step': 5,
        'grid_count': 3, 'amount_per_grid': 10000, 'step_increase': 0,
        'profit_retention': 30,
    })


class TestFundFlows:
    def test_principal_from_flows(self, services):
        pf, _, _ = services
        pf.add_flow({'flow_date': '2026-01-05', 'direction': 'deposit', 'amount': 200000})
        pf.add_flow({'flow_date': '2026-06-10', 'direction': 'deposit', 'amount': 100000})
        pf.add_flow({'flow_date': '2026-07-01', 'direction': 'withdraw', 'amount': 20000})
        assert pf.principal() == 280000.0

    def test_add_flow_validation(self, services):
        pf, _, _ = services
        with pytest.raises(ValueError):
            pf.add_flow({'flow_date': '2026-01-05', 'direction': 'in', 'amount': 100})
        with pytest.raises(ValueError):
            pf.add_flow({'flow_date': '2026-01-05', 'direction': 'deposit', 'amount': -5})

    def test_delete_flow(self, services):
        pf, _, _ = services
        f = pf.add_flow({'flow_date': '2026-01-05', 'direction': 'deposit', 'amount': 100})
        assert pf.delete_flow(f['id']) is True
        assert pf.delete_flow(f['id']) is False  # 已删除


class TestOverview:
    def test_three_accounts_and_safety(self, services):
        pf, grid, trade = services
        plan = _make_plan(grid)
        pf.add_flow({'flow_date': '2026-01-05', 'direction': 'deposit', 'amount': 300000})
        # 底仓（plan_id 为空）
        trade.add_trade({'symbol': '510300', 'symbol_name': '沪深300ETF',
                         'trade_date': '2026-03-01', 'direction': 'buy',
                         'price': 4.0, 'shares': 20000})
        # 网格持仓（关联计划）
        trade.add_trade({'plan_id': plan['id'], 'symbol': '510300', 'symbol_name': '沪深300ETF',
                         'trade_date': '2026-04-01', 'direction': 'buy',
                         'price': 4.2, 'shares': 2300, 'grid_level': 1})
        ov = pf.overview(prices={'510300': 4.65})
        assert ov['principal'] == 300000.0
        # 现金 = 本金 − (底仓成本 80000 + 网格成本 9660)
        assert ov['cash'] == round(300000 - 80000 - 9660, 2)
        assert ov['accounts']['core']['positions'][0]['shares'] == 20000
        assert ov['accounts']['grid']['positions'][0]['plan_name'] == '测试网格'
        # 安全线：满格资金 = 9660+9975+9855.3 = 29490.3 → 29490.3/300000
        assert ov['grid_full_capital'] == 29490.3
        assert 0.09 < ov['safety_ratio'] < 0.11
        assert ov['safety_warn'] is False
        # 市值口径
        assert ov['accounts']['core']['market_value'] == round(4.65 * 20000, 2)

    def test_retained_shares_split(self, services):
        """网格2.0：档位卖出后净剩余份额记为留存（免费底仓），从网格持仓拆出"""
        pf, grid, trade = services
        plan = _make_plan(grid)
        trade.add_trade({'plan_id': plan['id'], 'symbol': '510300',
                         'trade_date': '2026-04-01', 'direction': 'buy',
                         'price': 4.2, 'shares': 2300, 'grid_level': 1})
        trade.add_trade({'plan_id': plan['id'], 'symbol': '510300',
                         'trade_date': '2026-05-01', 'direction': 'sell',
                         'price': 4.421, 'shares': 2100, 'grid_level': 1})
        ov = pf.overview(prices={'510300': 4.65})
        assert ov['accounts']['retained']['shares'] == 200.0
        assert ov['accounts']['retained']['market_value'] == round(200 * 4.65, 2)
        assert ov['accounts']['retained']['items'][0]['plan_name'] == '测试网格'

    def test_empty_portfolio(self, services):
        pf, _, _ = services
        ov = pf.overview()
        assert ov['principal'] == 0.0
        assert ov['safety_ratio'] is None
        assert ov['accounts']['core']['positions'] == []

    def test_broken_plan_excluded_from_safety(self, services):
        pf, grid, _ = services
        plan = _make_plan(grid)
        grid.update_status(plan['id'], 'broken')
        pf.add_flow({'flow_date': '2026-01-05', 'direction': 'deposit', 'amount': 100000})
        ov = pf.overview()
        assert ov['grid_full_capital'] == 0.0  # BROKEN 不计入安全线分子
