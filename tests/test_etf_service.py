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


class TestAdjustFactors:
    def test_split_is_neutralized(self):
        """1拆2：价格减半、因子翻倍 → 前复权后价格连续，账户价值无跳变"""
        from backend.services.etf_service import ETFService
        rows = [('20250703', 1.80), ('20250704', 1.78),
                ('20250707', 0.89), ('20250708', 0.90)]  # 7/7 拆分
        factors = {'20250703': 1.0, '20250704': 1.0,
                   '20250707': 2.0, '20250708': 2.0}
        out = ETFService._apply_adj(rows, factors)
        closes = [c for _, c in out]
        assert closes == [0.9, 0.89, 0.89, 0.90]  # 除以最新因子 2.0
        assert max(abs(closes[i] / closes[i - 1] - 1) for i in range(1, 4)) < 0.02

    def test_no_factors_returns_raw(self):
        from backend.services.etf_service import ETFService
        rows = [('20250703', 1.8), ('20250704', 1.79)]
        assert ETFService._apply_adj(rows, None) == rows
        assert ETFService._apply_adj(rows, {}) == rows

    def test_asof_match_and_latest_scale(self):
        """因子稀疏时按 ≤当日 最近一条匹配；末日价格与原始一致"""
        from backend.services.etf_service import ETFService
        rows = [('20250102', 1.0), ('20250601', 1.2), ('20251231', 0.6)]
        factors = {'20250102': 1.0, '20250601': 2.0}  # 6月因子变化
        out = ETFService._apply_adj(rows, factors)
        assert out[0][1] == 0.5   # 1.0 × 1.0/2.0
        assert out[1][1] == 1.2   # 1.2 × 2.0/2.0（asof 命中 6月因子）
        assert out[2][1] == 0.6   # 末日=原始价（前复权定义）
