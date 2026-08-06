"""品种发现（智能寻品）测试：注入假历史数据，不走网络"""
import pytest

from backend.services import discovery_service


class _FakeSW:
    def list_industries(self):
        return [
            {'ts_code': '801780.SI', 'name': '银行', 'level': 'L1'},
            {'ts_code': '801193.SI', 'name': '证券Ⅱ', 'level': 'L2'},
            {'ts_code': '801150.SI', 'name': '医药生物', 'level': 'L1'},
        ]


class _FakeEtf:
    def _latest_amounts(self):
        return {'512800.SH': {'amount': 200000.0, 'close': 1.0}}  # 2 亿

    def search(self, kw):
        if '银行' in kw or '证券' in kw:
            return [{'ts_code': '512800.SH', 'name': f'{kw}ETF'}]
        return []  # 医药无达标 ETF


def _hist(close_end, vol_big=True, n=300):
    """构造 n 天历史：vol_big 为锯齿大波动；否则为缓慢阴跌（低波动且末端处于低位）"""
    out = []
    for i in range(n):
        if vol_big:
            c = 1.0 + ((i % 20) - 10) * 0.03
        else:
            c = 1.06 - i * ((1.06 - close_end) / n)  # 缓跌到低位，日波动≈0
        out.append({'close': c, 'pe': c * 10, 'pb': c * 2})
    out[-1] = {'close': close_end, 'pe': close_end * 10, 'pb': close_end * 2}
    return out


def _run(hist_map):
    return discovery_service.run_scan(
        _FakeSW(), _FakeEtf(),
        fetch_history=lambda code: hist_map.get(code, []),
        years=5)


@pytest.fixture(autouse=True)
def _reset_state():
    discovery_service._state.update({'running': False, 'result': None, 'error': None})
    yield


class TestDiscoveryScan:
    def test_passes_when_low_pct_high_vol_liquid(self):
        items = _run({'801780.SI': _hist(0.76)})  # 末端处于历史低位
        assert len(items) == 1
        it = items[0]
        assert it['name'] == '银行'
        assert it['valuation_percentile'] < 20
        assert it['volatility'] > 12
        assert it['etf']['ts_code'] == '512800.SH'
        assert it['score'] > 60

    def test_rejects_high_percentile(self):
        items = _run({'801780.SI': _hist(0.76), '801193.SI': _hist(1.3)})
        # 证券Ⅱ 末端在历史高位 → 被分位闸门否决
        assert [i['name'] for i in items] == ['银行']

    def test_rejects_low_volatility(self):
        items = _run({'801780.SI': _hist(0.76, vol_big=False)})
        assert items == []

    def test_rejects_without_liquid_etf(self):
        # 医药生物有低估高波历史，但 search 返回空 → 无可交易 ETF
        items = _run({'801150.SI': _hist(0.76)})
        assert items == []

    def test_rejects_short_history(self):
        items = _run({'801780.SI': _hist(0.76, n=100)})
        assert items == []

    def test_state_and_result_shape(self):
        _run({'801780.SI': _hist(0.76)})
        st = discovery_service.scan_state()
        assert st['running'] is False
        assert st['result']['scanned'] == 3
        assert st['result']['passed'] == 1
        assert st['result']['filters']['max_pct'] == 50.0
