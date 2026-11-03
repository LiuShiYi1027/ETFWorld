"""估值更新管线回归：按指数补缺口、写入幂等。"""
from datetime import date, timedelta

import pandas as pd
import pytest

import backend.services.valuation_service as valuation_service
from backend.models.database import Base, ValuationPercentileTable, ValuationTable
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
    def __init__(self, rows, missing_today=(), prices=None):
        self.rows = rows          # {(ts_code, yyyymmdd): [row]}
        self.missing_today = set(missing_today)
        self.prices = prices or {}

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

    def get_index_daily(self, ts_code, trade_date=None, start_date=None, end_date=None):
        days = [trade_date] if trade_date else sorted({
            day for sym, day in self.rows if sym == ts_code
            and (not start_date or day >= start_date)
            and (not end_date or day <= end_date)
        })
        return pd.DataFrame([
            {'ts_code': ts_code, 'trade_date': day,
             'close': self.prices.get((ts_code, day), 100.0)}
            for day in days if (ts_code, day) not in self.missing_today
        ])


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

def test_existing_valuation_can_backfill_missing_close():
    """老库重复回填不能只跳过：新增指数价格字段必须补到原记录。"""
    svc = ValuationService(_FakeClient(rows={}))
    rec = _row('000300.SH', '20260105')
    with get_session() as session:
        assert svc._save_rows(session, [rec]) == 1
        rec['close_price'] = 4567.89
        assert svc._save_rows(session, [rec]) == 1
    with get_session() as session:
        row = session.query(ValuationTable).filter_by(ts_code='000300.SH').one()
        assert float(row.close_price) == 4567.89


def test_broad_index_close_comes_from_index_daily():
    client = _FakeClient(
        rows={('000300.SH', '20260105'): [_row('000300.SH', '20260105')]},
        prices={('000300.SH', '20260105'): 4567.89},
    )
    records = ValuationService(client)._fetch(_POOL[0], trade_date='20260105')
    assert records[0]['close_price'] == 4567.89


@pytest.mark.parametrize('bad_pe', [-5.0, 0.0])
def test_nonpositive_current_pe_has_no_pe_percentile(bad_pe):
    today = date(2026, 1, 5)
    with get_session() as session:
        for i in range(35):
            session.add(ValuationTable(
                ts_code='NEG.SH', trade_date=today - timedelta(days=34 - i),
                pe_ttm=bad_pe if i == 34 else 10.0 + i,
                pb=1.0 + i / 10))
    ValuationService(_FakeClient(rows={})).calc_percentiles(ts_codes=['NEG.SH'])
    with get_session() as session:
        pct = session.query(ValuationPercentileTable).filter_by(
            ts_code='NEG.SH', trade_date=today, period='all').one()
        assert pct.pe_percentile is None
        assert pct.pb_percentile == 100.0
        assert pct.sample_count == 35


def test_pb_only_percentile_uses_valid_pb_sample_count():
    today = date(2026, 1, 5)
    with get_session() as session:
        for i in range(35):
            session.add(ValuationTable(
                ts_code='PBONLY.SH', trade_date=today - timedelta(days=34 - i),
                pe_ttm=None, pb=(-1.0 if i < 3 else 1.0 + i / 10)))
    ValuationService(_FakeClient(rows={})).calc_percentiles(ts_codes=['PBONLY.SH'])
    with get_session() as session:
        pct = session.query(ValuationPercentileTable).filter_by(
            ts_code='PBONLY.SH', trade_date=today, period='all').one()
        assert pct.pe_percentile is None
        assert pct.pb_percentile == 100.0
        assert pct.sample_count == 32
