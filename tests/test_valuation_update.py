"""估值更新管线回归：按指数补缺口、写入幂等。"""
import pandas as pd
import pytest

import backend.services.valuation_service as valuation_service
from backend.models.database import Base, ValuationTable
from backend.services.valuation_service import ValuationService
from backend.utils.db import engine, get_session, init_db


@pytest.fixture(autouse=True)
def _clean_db():
    init_db()
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


def _row(ts_code, trade_date, pe=10.0, pb=1.0):
    return {
        'ts_code': ts_code, 'trade_date': trade_date, 'close': None,
        'total_mv': 1000.0, 'float_mv': 800.0, 'turnover_rate': 1.0,
        'pe': pe, 'pe_ttm': pe, 'pb': pb,
    }


class _FakeClient:
    def __init__(self, rows, missing_today=()):
        self.rows = rows          # {(ts_code, yyyymmdd): [row]}
        self.missing_today = set(missing_today)

    def is_connected(self):
        return True

    def get_recent_trade_dates(self, limit=5):
        return ['20260105']

    def get_index_dailybasic(self, ts_code, trade_date=None, start_date=None, end_date=None):
        if trade_date:
            if (ts_code, trade_date) in self.missing_today:
                return pd.DataFrame([])
            return pd.DataFrame(self.rows.get((ts_code, trade_date), []))
        out = []
        for (sym, day), rows in self.rows.items():
            if sym != ts_code:
                continue
            if start_date and day < start_date:
                continue
            if end_date and day > end_date:
                continue
            out.extend(rows)
        out.sort(key=lambda r: r['trade_date'])
        return pd.DataFrame(out)


_POOL = [
    {'ts_code': '000300.SH', 'name': '沪深300', 'category': '宽基', 'source': 'index'},
    {'ts_code': '801193.SI', 'name': '证券Ⅱ', 'category': '行业二级', 'source': 'index'},
]


def test_update_latest_backfills_each_index_gap(monkeypatch):
    """B 指数 20260105 延迟发布：当日记 failed，但应把 01/03、01/05 缺口补回来"""
    monkeypatch.setattr(valuation_service, '_pool', lambda: _POOL)
    with get_session() as session:  # B 库内只到 20260101
        session.add(ValuationTable(
            ts_code='801193.SI', trade_date=valuation_service._parse_date('20260101'),
            pe=9.0, pe_ttm=9.0, pb=0.9, data_source='tushare'))
    client = _FakeClient(
        rows={
            ('000300.SH', '20260105'): [_row('000300.SH', '20260105')],
            ('801193.SI', '20260103'): [_row('801193.SI', '20260103')],
            ('801193.SI', '20260105'): [_row('801193.SI', '20260105')],
        },
        missing_today={('801193.SI', '20260105')},
    )
    svc = ValuationService(client)
    r = svc.update_latest()
    assert r['status'] == 'success'
    assert r['saved'] == 1          # 当日只有 A 入库
    assert r['failed'] == 1         # B 当日延迟发布
    assert r['gap_filled'] == 2     # B 的 01/03、01/05 被按只回补
    with get_session() as session:
        days = [d.isoformat() for (d,) in session.query(ValuationTable.trade_date)
                .filter_by(ts_code='801193.SI')
                .order_by(ValuationTable.trade_date).all()]
    assert days == ['2026-01-01', '2026-01-03', '2026-01-05']


def test_save_rows_idempotent_with_savepoint():
    """同一批记录写两次：第二次只跳过，不撞唯一约束、不让整批回滚"""
    svc = ValuationService(_FakeClient(rows={}))
    rec = _row('000300.SH', '20260105')
    with get_session() as session:
        assert svc._save_rows(session, [rec]) == 1
        assert svc._save_rows(session, [rec]) == 0
    with get_session() as session:
        n = session.query(ValuationTable).filter_by(ts_code='000300.SH').count()
    assert n == 1
