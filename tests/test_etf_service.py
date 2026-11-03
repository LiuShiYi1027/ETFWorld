"""ETF 搜索、复权完整性、公司行动与行情时效测试（不触网）。"""
from datetime import datetime

import pandas as pd
import pytest

from backend.services.etf_service import ETFService

_BASIC = [
    {'ts_code': '159938.SZ', 'name': '广发中证全指医药卫生ETF',
     'benchmark': '中证全指医药卫生指数×100%', 'management': '广发基金',
     'list_date': '20150108', 'm_fee': 0.5, 'c_fee': 0.1},
    {'ts_code': '510300.SH', 'name': '华泰柏瑞沪深300ETF',
     'benchmark': '沪深300指数×100%', 'management': '华泰柏瑞',
     'list_date': '20120504', 'm_fee': 0.5, 'c_fee': 0.1},
    {'ts_code': '512880.SH', 'name': '国泰中证全指证券公司ETF',
     'benchmark': '中证全指证券公司指数×100%', 'management': '国泰基金',
     'list_date': '20160804', 'm_fee': 0.5, 'c_fee': 0.1},
    {'ts_code': '512690.SH', 'name': '鹏华中证酒ETF',
     'benchmark': '中证酒指数×100%', 'management': '鹏华基金',
     'list_date': '20190520', 'm_fee': 0.5, 'c_fee': 0.1},
]


@pytest.fixture()
def svc(monkeypatch):
    s = ETFService()
    monkeypatch.setattr(s, '_load_basic', lambda force=False: _BASIC)
    monkeypatch.setattr(s, '_latest_amounts', lambda: {})
    return s


class TestSearch:
    def test_search_by_code(self, svc):
        r = svc.search('159938')
        assert [x['ts_code'] for x in r] == ['159938.SZ']
        assert r[0]['match_type'] == 'exact_code'
        assert r[0]['match_confidence'] == 1.0

    def test_search_by_code_with_suffix(self, svc):
        r = svc.search('510300.SH')
        assert [x['ts_code'] for x in r] == ['510300.SH']

    def test_search_by_name_verifies_exact_benchmark(self, svc):
        r = svc.search('沪深300')
        assert [x['ts_code'] for x in r] == ['510300.SH']
        assert r[0]['match_type'] == 'exact_benchmark'
        assert r[0]['is_exact_tracking'] is True

    def test_search_by_alias_is_explicitly_fuzzy(self, svc):
        r = svc.search('白酒')  # ALIASES: 白酒 → 酒，不能当成精确跟踪关系
        hit = next(x for x in r if x['ts_code'] == '512690.SH')
        assert hit['match_type'].startswith('fuzzy_')
        assert hit['match_confidence'] < 0.5
        assert hit['is_exact_tracking'] is False

    def test_index_mapping_distinguishes_verified_and_fuzzy(self, svc, monkeypatch):
        from backend.services.watchlist_service import WatchlistService
        monkeypatch.setattr(WatchlistService, 'name_map', lambda self: {
            '000300.SH': '沪深300', 'TEST.SW': '白酒'})
        assert svc.find_for_index('000300.SH')['mapping_status'] == 'verified'
        fuzzy = svc.find_for_index('TEST.SW')
        assert fuzzy['mapping_status'] == 'fuzzy'
        assert fuzzy['match_type'].startswith('fuzzy_')

    def test_search_no_match(self, svc):
        assert svc.search('不存在的品种xyz') == []


class TestAdjustFactors:
    def test_split_is_neutralized(self):
        """1拆2：价格减半、因子翻倍 → 前复权后价格连续，账户价值无跳变"""
        rows = [('20250703', 1.80), ('20250704', 1.78),
                ('20250707', 0.89), ('20250708', 0.90)]
        factors = {'20250703': 1.0, '20250704': 1.0,
                   '20250707': 2.0, '20250708': 2.0}
        out = ETFService._apply_adj(rows, factors)
        closes = [c for _, c in out]
        assert closes == [0.9, 0.89, 0.89, 0.90]
        assert max(abs(closes[i] / closes[i - 1] - 1) for i in range(1, 4)) < 0.02

    @pytest.mark.parametrize('factors', [None, {}])
    def test_no_factors_is_safe_error(self, factors):
        rows = [('20250703', 1.8), ('20250704', 1.79)]
        with pytest.raises(ValueError, match='因子缺失'):
            ETFService._apply_adj(rows, factors)

    @pytest.mark.parametrize('factors', [
        {'20250704': 1.0},                    # 首日之前无因子
        {'20250703': 1.0},                    # 末日之后无因子
        {'20250702': 1.0, '20250703': 1.0},  # 尾部覆盖不足
    ])
    def test_incomplete_factor_coverage_is_safe_error(self, factors):
        rows = [('20250703', 1.8), ('20250704', 1.79)]
        with pytest.raises(ValueError, match='覆盖不足'):
            ETFService._apply_adj(rows, factors)

    def test_asof_match_and_latest_scale(self):
        """因子稀疏时按 ≤当日 最近一条匹配；末日价格与原始一致"""
        rows = [('20250102', 1.0), ('20250601', 1.2), ('20251231', 0.6)]
        factors = {'20250102': 1.0, '20250601': 2.0, '20251231': 2.0}
        out = ETFService._apply_adj(rows, factors)
        assert out[0][1] == 0.5
        assert out[1][1] == 1.2
        assert out[2][1] == 0.6

    def test_daily_bars_never_silently_returns_raw_as_adjusted(self, monkeypatch):
        svc = ETFService()
        rows = [('20250703', 1.8), ('20250704', 1.79)]
        monkeypatch.setattr(svc, '_resolve_ts_code', lambda symbol: '159938.SZ')
        monkeypatch.setattr(svc, '_daily_raw_rows', lambda code, days: rows)
        monkeypatch.setattr(svc, '_adj_factors', lambda *args: {})
        result = svc.daily_bars_with_status('159938')
        assert result['bars'] == []
        assert result['adjustment_status'] == 'factor_unavailable'
        with pytest.raises(ValueError, match='因子缺失'):
            svc.daily_bars('159938')

    def test_raw_bars_do_not_request_factors(self, monkeypatch):
        svc = ETFService()
        rows = [('20250703', 1.8), ('20250704', 0.9)]
        monkeypatch.setattr(svc, '_resolve_ts_code', lambda symbol: '159938.SZ')
        monkeypatch.setattr(svc, '_daily_raw_rows', lambda code, days: rows)
        monkeypatch.setattr(svc, '_adj_factors', lambda *args: (_ for _ in ()).throw(
            AssertionError('原始价复盘不应读取最新复权因子')))
        assert svc.daily_raw_bars('159938') == rows


class _PagedPro:
    def __init__(self, records):
        self.records = records
        self.calls = []

    def fund_adj(self, **kwargs):
        self.calls.append(kwargs)
        rows = [r for r in self.records
                if kwargs.get('start_date', '') <= r['trade_date']
                and r['trade_date'] <= kwargs.get('end_date', '99999999')]
        offset = kwargs['offset']
        return pd.DataFrame(rows[offset:offset + kwargs['limit']])


class TestFactorRetrieval:
    def test_pagination_date_range_cache_and_refresh(self, monkeypatch):
        import backend.services.etf_service as module
        records = [{'trade_date': f'2025010{i}', 'adj_factor': float(i)}
                   for i in range(1, 6)]
        pro = _PagedPro(records)
        monkeypatch.setattr(module, 'get_pro', lambda: pro)
        monkeypatch.setattr(module, '_ADJ_PAGE_SIZE', 2)
        svc = ETFService()
        expected = {r['trade_date']: r['adj_factor'] for r in records}
        assert svc._adj_factors('159938.SZ', '20250101', '20250105') == expected
        assert len(pro.calls) == 3
        assert all(c['start_date'] == '20250101' and c['end_date'] == '20250105'
                   for c in pro.calls)

        svc._adj_factors('159938.SZ', '20250101', '20250105')
        assert len(pro.calls) == 3  # 同日期范围当天命中缓存
        monkeypatch.setattr(svc, '_load_basic', lambda force=False: [])
        svc.refresh()
        assert svc._adj_cache == {}
        svc._adj_factors('159938.SZ', '20250101', '20250105')
        assert len(pro.calls) == 6


class TestCorporateActionsAndQuotes:
    def test_factor_change_after_plan_creation_pauses_old_grid(self, monkeypatch):
        svc = ETFService()
        monkeypatch.setattr(svc, '_resolve_ts_code', lambda symbol: '159938.SZ')
        today = datetime.now().strftime('%Y%m%d')
        monkeypatch.setattr(svc, '_adj_factors', lambda *args, **kwargs: {
            '20251231': 1.0, '20260102': 1.0, '20260103': 2.0, today: 2.0})
        result = svc.corporate_action_check('159938', '2026-01-01T09:00:00')
        assert result['status'] == 'ok'
        assert result['has_changed'] is True and result['pause_required'] is True
        assert result['changes'] == [
            {'trade_date': '20260103', 'old_factor': 1.0, 'new_factor': 2.0}]

    def test_unknown_factor_baseline_is_not_all_clear(self, monkeypatch):
        svc = ETFService()
        monkeypatch.setattr(svc, '_resolve_ts_code', lambda symbol: '159938.SZ')
        monkeypatch.setattr(svc, '_adj_factors', lambda *args, **kwargs: {
            '20260103': 2.0, datetime.now().strftime('%Y%m%d'): 2.0})
        result = svc.corporate_action_check('159938', '2026-01-01')
        assert result['status'] == 'unavailable'
        assert result['has_changed'] is None
        assert result['pause_required'] is True

    def test_quotes_expose_stale_basis_and_keep_trade_date(self, monkeypatch):
        svc = ETFService()
        today = datetime.now().strftime('%Y%m%d')
        monkeypatch.setattr(svc, '_latest_amounts', lambda: {
            '159938.SZ': {'close': 1.2, 'trade_date': today},
            '510300.SH': {'close': 3.2, 'trade_date': '20200102'},
        })
        result = svc.quotes(['159938', '510300', 'UNKNOWN'])
        assert result['159938']['stale'] is False
        assert result['159938']['trade_date'] == today
        assert result['510300']['stale'] is True
        assert result['510300']['stale_reason'] == 'not_latest_market_date'
        assert result['UNKNOWN']['stale'] is True
        assert result['UNKNOWN']['stale_reason'] == 'missing_trade_date'
        assert all(q['as_of_date'] for q in result.values())
