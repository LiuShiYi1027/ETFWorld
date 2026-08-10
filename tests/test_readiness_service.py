"""雷达就绪度测试：无数据指数返回占位行（监控池新增立即可见）、有数据正常评分"""
from datetime import date, timedelta

import pytest

from backend.models.database import (Base, ValuationPercentileTable,
                                     ValuationTable)
from backend.utils.db import engine, get_session, init_db
from backend.services.readiness_service import ReadinessService
from backend.services.watchlist_service import WatchlistService


@pytest.fixture(autouse=True)
def _clean_db():
    init_db()
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


class TestNoDataStub:
    def test_empty_db_returns_stubs_not_empty(self):
        """空库时 41 只播种指数全部以「数据回填中」占位行返回，而不是消失"""
        rows = ReadinessService().assess_all()
        assert len(rows) == 41  # SUPPORTED_INDICES 播种数
        for r in rows:
            assert r['level'] == 'unknown'
            assert r['verdict'] == '数据回填中'
            assert r['score'] == -1

    def test_stub_sorts_after_assessed(self):
        """有数据的指数正常评分并排在占位行之前"""
        WatchlistService().add('TEST.SI', '测试行业', '行业二级', 'sw')
        today = date(2026, 8, 10)
        with get_session() as session:
            for i in range(100):  # 100 天收盘价，满足波动率样本量
                d = today - timedelta(days=i)
                session.add(ValuationTable(
                    ts_code='TEST.SI', trade_date=d,
                    close_price=100 + (i % 7), pe_ttm=20.0, pb=2.0))
            session.add(ValuationPercentileTable(
                ts_code='TEST.SI', trade_date=today, period='5y',
                pe_percentile=10.0, pb_percentile=20.0, sample_count=100))
        rows = ReadinessService().assess_all()
        test_row = next(r for r in rows if r['ts_code'] == 'TEST.SI')
        assert test_row['level'] in ('go', 'maybe', 'wait', 'no')
        assert test_row['score'] >= 0
        assert test_row['valuation_percentile'] == 15.0  # (10+20)/2
        assert rows[-1]['score'] == -1  # 占位行沉底
        assert rows.index(test_row) < len(rows) - 41  # 排所有占位行之前
