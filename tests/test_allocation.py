"""资金分配建议测试：四分支（超安全线 / 现金偏低 / 现金过多+候选 / 正常）"""
import pytest

from backend.models.database import Base
from backend.utils.db import engine, init_db
from backend.services.portfolio_service import PortfolioService
from backend.services.grid_service import GridService
from backend.services.trade_service import TradeService


@pytest.fixture(autouse=True)
def _clean_db():
    init_db()
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture()
def services():
    return PortfolioService(), GridService(), TradeService()


def _readiness(level='go', name='证券Ⅱ', score=72.0, pct=18.0):
    return [{'ts_code': '801193.SI', 'name': name, 'level': level,
             'score': score, 'valuation_percentile': pct}]


class TestAllocationAdvice:
    def test_safety_warn_blocks_new_plans(self, services):
        pf, grid, _ = services
        # 满格资金 29490.3 / 本金 30000 ≈ 98% > 70% → safety_warn
        grid.create_plan({'name': '大网格', 'symbol': '510300', 'base_price': 4.2,
                          'grid_step': 5, 'grid_count': 3, 'amount_per_grid': 10000})
        pf.add_flow({'flow_date': '2026-01-05', 'direction': 'deposit', 'amount': 30000})
        ov = pf.overview()
        assert ov['safety_warn'] is True
        r = PortfolioService.allocation_advice(ov, _readiness())
        assert r['level'] == 'warn' and '安全线' in r['headline']
        assert r['candidates'] == []

    def test_low_cash_warns(self, services):
        pf, _, trade = services
        pf.add_flow({'flow_date': '2026-01-05', 'direction': 'deposit', 'amount': 100000})
        trade.add_trade({'symbol': '510300', 'symbol_name': '沪深300ETF',
                         'trade_date': '2026-03-01', 'direction': 'buy',
                         'price': 4.0, 'shares': 24000})  # 成本 96000，现金仅 4000 (4%)
        ov = pf.overview()
        r = PortfolioService.allocation_advice(ov, _readiness())
        assert r['level'] == 'warn' and '现金水位偏低' in r['headline']

    def test_high_cash_recommends_unheld_go_candidates(self, services):
        pf, _, trade = services
        pf.add_flow({'flow_date': '2026-01-05', 'direction': 'deposit', 'amount': 100000})
        trade.add_trade({'symbol': '510300', 'symbol_name': '沪深300ETF',
                         'trade_date': '2026-03-01', 'direction': 'buy',
                         'price': 4.0, 'shares': 5000})  # 现金 80000 (80%)
        ov = pf.overview()
        rows = _readiness('go', '证券Ⅱ', 72.0) + _readiness('go', '沪深300', 80.0) \
            + _readiness('no', '电池', 30.0)
        r = PortfolioService.allocation_advice(ov, rows)
        assert r['level'] == 'info'
        names = [c['name'] for c in r['candidates']]
        assert names == ['证券Ⅱ']          # 沪深300 已持仓不推荐；电池非 go 不推荐
        assert '15%' in r['detail']        # 单计划满格资金上限提示

    def test_high_cash_without_candidates(self, services):
        pf, _, _ = services
        pf.add_flow({'flow_date': '2026-01-05', 'direction': 'deposit', 'amount': 100000})
        ov = pf.overview()  # 无持仓，现金 100%
        r = PortfolioService.allocation_advice(ov, _readiness('wait', '电池', 40.0))
        assert r['level'] == 'ok' and '暂无低估候选' in r['headline']

    def test_normal_water_level(self, services):
        pf, _, trade = services
        pf.add_flow({'flow_date': '2026-01-05', 'direction': 'deposit', 'amount': 100000})
        trade.add_trade({'symbol': '510300', 'symbol_name': '沪深300ETF',
                         'trade_date': '2026-03-01', 'direction': 'buy',
                         'price': 4.0, 'shares': 20000})  # 现金 20000 (20%)
        ov = pf.overview()
        r = PortfolioService.allocation_advice(ov, _readiness())
        assert r['level'] == 'ok' and '现金水位合理' in r['headline']
