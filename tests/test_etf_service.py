"""ETF 搜索测试：代码 / 名称 / 基准三路召回（不触网，注入假数据）"""
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

    def test_search_by_code_with_suffix(self, svc):
        r = svc.search('510300.SH')
        assert [x['ts_code'] for x in r] == ['510300.SH']

    def test_search_by_name(self, svc):
        r = svc.search('沪深300')
        assert [x['ts_code'] for x in r] == ['510300.SH']

    def test_search_by_alias(self, svc):
        r = svc.search('白酒')  # ALIASES: 白酒 → 酒，命中「中证酒」
        assert '512690.SH' in [x['ts_code'] for x in r]

    def test_search_no_match(self, svc):
        assert svc.search('不存在的品种xyz') == []
