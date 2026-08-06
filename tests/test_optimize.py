"""参数寻优（规则层）测试：活性标注与备选组合"""
from backend.services.backtest_service import (
    BacktestService, LOW_ACTIVITY_TRADES, simulate_rebase_grid,
)

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


class TestCrossAnchor:
    UP_THEN_BACK = [1.0, 1.1, 1.2, 1.3, 1.2, 1.1, 1.05, 1.15, 1.25, 1.2]

    def test_cross_anchor_starts_at_recent_crossing(self):
        """穿越点锚定：基准=末日价，起点=最近一次向下穿越该价位的日子"""
        prices = self.UP_THEN_BACK  # 末日 1.2；最近一次下穿越是第 8→9 日（1.25→1.2）
        r = BacktestService().backtest(
            {'grid_step': 5, 'grid_count': 3, 'amount_per_grid': 10000, 'anchor': 'cross'},
            prices)
        assert r['anchor'] == 'cross'
        assert r['cross_idx'] == 9  # 最近一次穿越即末日当天
        assert r['base'] == prices[9]

    def test_cross_anchor_fallback_when_never_crossed(self):
        """窗口内从未穿越今日价位 → 回退窗口起点并标记 cross_idx=None"""
        prices = [0.8, 0.9, 1.0, 1.1, 1.2]  # 全程低于末日价 1.2，无下穿越
        r = BacktestService().backtest(
            {'grid_step': 5, 'grid_count': 3, 'amount_per_grid': 10000, 'anchor': 'cross'},
            prices)
        assert r['cross_idx'] is None
        assert r['n'] == len(prices)  # 完整窗口

    def test_window_anchor_unchanged(self):
        r = BacktestService().backtest(
            {'grid_step': 5, 'grid_count': 3, 'amount_per_grid': 10000},
            self.UP_THEN_BACK)
        assert r['anchor'] == 'window'
        assert r['cross_idx'] is None

    def test_dates_pass_through_and_slice_with_cross(self):
        dates = [f'202608{d:02d}' for d in range(1, 11)]
        r = BacktestService().backtest(
            {'grid_step': 5, 'grid_count': 3, 'amount_per_grid': 10000},
            self.UP_THEN_BACK, dates)
        assert r['dates'] == dates  # 窗口口径：全量
        r2 = BacktestService().backtest(
            {'grid_step': 5, 'grid_count': 3, 'amount_per_grid': 10000, 'anchor': 'cross'},
            self.UP_THEN_BACK, dates)
        assert r2['cross_idx'] == 9
        assert r2['dates'] == ['20260810']  # 穿越口径：同步切片


class TestRebaseSimulate:
    RALLY = [1.0, 1.06, 1.12, 1.18, 1.25, 1.31, 1.38,  # 一路上涨，反复涨穿顶格
             1.3, 1.24, 1.31, 1.38, 1.45]

    def test_rebase_triggers_and_counts(self):
        """涨穿顶格（基准/(1-step)）即重开：重开次数与事件齐全"""
        r = simulate_rebase_grid(self.RALLY, step=5, count=3, amount=10000)
        assert r['rebases'] >= 3                # 多次涨穿
        assert r['trades'] >= 1                 # 有兑现
        assert len(r['g']) == len(self.RALLY)   # 曲线等长
        buys = [e for e in r['events'] if e['dir'] == 'buy']
        sells = [e for e in r['events'] if e['dir'] == 'sell']
        assert len(sells) == r['trades'] and len(buys) >= len(sells)

    def test_rebase_vs_static_shape(self):
        """重开口径的字段与静态一致，便于前端并排"""
        r = simulate_rebase_grid(self.RALLY, step=5, count=3, amount=10000)
        for k in ('grid_ret', 'max_dd', 'invested_pct', 'trades'):
            assert k in r

    def test_empty_prices(self):
        r = simulate_rebase_grid([], step=5, count=3, amount=10000)
        assert r['trades'] == 0 and r['rebases'] == 0
