"""参数寻优（规则层）测试：活性标注与备选组合"""
from backend.services.backtest_service import BacktestService, LOW_ACTIVITY_TRADES

# 震荡回到原点的序列：买得勤、卖得也勤
OSC = [1.0, 0.95, 0.9025, 0.95, 1.0] * 4
# 单边上涨：几乎不成交
UP = [1.0 + i * 0.004 for i in range(40)]


class TestOptimize:
    def test_cells_carry_activity_metrics(self):
        r = BacktestService().optimize({'amount_per_grid': 10000}, OSC,
                                       steps=[5], counts=[3, 5])
        assert r['n'] == len(OSC)
        for c in r['cells']:
            assert 'invested_pct' in c and 'low_activity' in c and 'trades' in c
        # 震荡行情成交活跃 → 不应有低活性标记
        assert all(not c['low_activity'] for c in r['cells'])

    def test_low_activity_flag_and_best_active(self):
        # 单边上涨：几乎无成交，所有组合都低活性
        r = BacktestService().optimize({'amount_per_grid': 10000}, UP,
                                       steps=[5, 8], counts=[3, 5])
        assert all(c['low_activity'] for c in r['cells'])
        assert r['best_active'] is None  # 没有合格组合时明确为 None（不硬选）

    def test_best_active_prefers_active_grid(self):
        r = BacktestService().optimize({'amount_per_grid': 10000}, OSC,
                                       steps=[5], counts=[3, 5])
        assert r['best_active'] is not None
        assert r['best_active']['trades'] >= LOW_ACTIVITY_TRADES
        assert r['best_active']['low_activity'] is False
