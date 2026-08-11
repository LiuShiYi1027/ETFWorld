"""定投服务测试：倍数规则、待投判定、纪律统计"""
from datetime import date, timedelta

import pytest

from backend.models.database import Base
from backend.utils.db import engine, init_db
from backend.services.dca_service import DcaService
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
    return DcaService(trade), trade


def _make_plan(svc, freq='weekly', amount=1000, status='active', name='酒定投'):
    p = svc.create_plan({'name': name, 'symbol': '512690', 'symbol_name': '鹏华中证酒ETF',
                         'base_amount': amount, 'frequency': freq})
    if status != 'active':
        svc.update_status(p['id'], status)
    return p


_READINESS = {'中证酒': {'valuation_percentile': 15.0}}


class TestSuggest:
    def test_multiplier_bands(self, services):
        svc, _ = services
        plan = {'base_amount': 1000}
        cases = [(10.0, 2.0, 'invest'), (19.9, 2.0, 'invest'),
                 (20.0, 1.5, 'invest'), (39.9, 1.5, 'invest'),
                 (40.0, 1.0, 'invest'), (59.9, 1.0, 'invest'),
                 (60.0, 0.5, 'invest'), (79.9, 0.5, 'invest'),
                 (80.0, 0.0, 'pause'), (89.9, 0.0, 'pause'),
                 (90.0, 0.0, 'profit_take'), (99.0, 0.0, 'profit_take')]
        for pct, mult, action in cases:
            s = svc.suggest(plan, pct)
            assert s['multiplier'] == mult, f'pct={pct}'
            assert s['action'] == action, f'pct={pct}'
            assert s['amount'] == round(1000 * mult, 2)

    def test_unmatched_index_falls_back_to_base(self, services):
        svc, _ = services
        s = svc.suggest({'base_amount': 1000}, None)
        assert s['multiplier'] == 1.0 and s['amount'] == 1000
        assert '未关联指数' in s['label']


class TestDueTodos:
    def test_weekly_due_with_multiplier(self, services):
        svc, _ = services
        _make_plan(svc)
        todos = svc.due_todos(_READINESS, today=date(2026, 8, 5))  # 周三
        assert len(todos) == 1
        t = todos[0]
        assert t['period_label'] == '本周'
        assert t['multiplier'] == 2.0 and t['amount'] == 2000  # 分位 15% → 低估加倍
        assert t['index_name'] == '中证酒'

    def test_invested_this_week_no_todo(self, services):
        svc, trade = services
        p = _make_plan(svc)
        trade.add_trade({'symbol': '512690', 'symbol_name': '鹏华中证酒ETF',
                         'trade_date': '2026-08-04', 'direction': 'buy',
                         'price': 0.5, 'shares': 2000, 'dca_plan_id': p['id']})
        todos = svc.due_todos(_READINESS, today=date(2026, 8, 5))
        assert todos == []

    def test_last_week_investment_still_due(self, services):
        svc, trade = services
        p = _make_plan(svc)
        trade.add_trade({'symbol': '512690', 'symbol_name': '鹏华中证酒ETF',
                         'trade_date': '2026-07-29', 'direction': 'buy',  # 上周
                         'price': 0.5, 'shares': 2000, 'dca_plan_id': p['id']})
        todos = svc.due_todos(_READINESS, today=date(2026, 8, 5))
        assert len(todos) == 1

    def test_monthly_frequency(self, services):
        svc, trade = services
        p = _make_plan(svc, freq='monthly')
        trade.add_trade({'symbol': '512690', 'symbol_name': '鹏华中证酒ETF',
                         'trade_date': '2026-08-01', 'direction': 'buy',
                         'price': 0.5, 'shares': 2000, 'dca_plan_id': p['id']})
        assert svc.due_todos(_READINESS, today=date(2026, 8, 20)) == []  # 本月已投
        todos = svc.due_todos(_READINESS, today=date(2026, 9, 1))       # 下月待投
        assert len(todos) == 1 and todos[0]['period_label'] == '本月'

    def test_paused_and_closed_no_todo(self, services):
        svc, _ = services
        _make_plan(svc, status='paused', name='暂停的')
        _make_plan(svc, status='closed', name='关闭的')
        assert svc.due_todos(_READINESS, today=date(2026, 8, 5)) == []

    def test_unmatched_symbol_base_amount(self, services):
        svc, _ = services
        svc.create_plan({'name': '纳指定投', 'symbol': '513100',
                         'symbol_name': '纳指ETF', 'base_amount': 500,
                         'frequency': 'weekly'})
        todos = svc.due_todos(_READINESS, today=date(2026, 8, 5))
        assert len(todos) == 1
        assert todos[0]['multiplier'] == 1.0 and todos[0]['valuation_pct'] is None


class TestPlanSummary:
    def test_invested_and_missed(self, services):
        svc, trade = services
        p = _make_plan(svc)
        # 计划 created_at 是今天；补两笔历史成交（往前两周）制造缺席
        w0 = date.today() - timedelta(days=14)
        w1 = date.today() - timedelta(days=7)
        for d in (w0, w1):
            trade.add_trade({'symbol': '512690', 'symbol_name': '鹏华中证酒ETF',
                             'trade_date': d.isoformat(), 'direction': 'buy',
                             'price': 0.5, 'shares': 2000, 'dca_plan_id': p['id']})
        s = svc.plan_summary(p['id'])
        assert s['total_invested'] == 2000.0  # 2 × (0.5 × 2000)
        assert s['periods_done'] == 2
        assert s['periods_elapsed'] >= 2
        assert s['periods_missed'] == s['periods_elapsed'] - 2


class TestValidation:
    def test_bad_params(self, services):
        svc, _ = services
        with pytest.raises(ValueError, match='基准金额'):
            svc.create_plan({'symbol': 'x', 'base_amount': 0})
        with pytest.raises(ValueError, match='weekly'):
            svc.create_plan({'symbol': 'x', 'base_amount': 100, 'frequency': 'daily'})
        with pytest.raises(ValueError, match='标的'):
            svc.create_plan({'base_amount': 100})
