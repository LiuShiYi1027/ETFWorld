"""定投回测测试：普通定投 vs 估值增强定投（合成序列，确定性断言）"""
from datetime import date, timedelta

from backend.services.backtest_service import _trailing_pctile, simulate_dca


def _bars(n, price=1.0, start=date(2026, 1, 1)):
    return [((start + timedelta(days=i)).strftime('%Y%m%d'), price)
            for i in range(n)]


def _vals(n, pe_fn, start=date(2026, 1, 1)):
    return [((start + timedelta(days=i)).strftime('%Y%m%d'), pe_fn(i), None)
            for i in range(n)]


class TestTrailingPctile:
    def test_monotonic_rising_is_100(self):
        values = [10.0 + i * 0.1 for i in range(50)]
        assert _trailing_pctile(values, 49) == 100.0

    def test_crash_is_low(self):
        values = [30.0] * 49 + [10.0]
        pct = _trailing_pctile(values, 49)
        assert pct < 5  # 只有当前值最低

    def test_insufficient_samples_returns_none(self):
        assert _trailing_pctile([10.0] * 10, 9) is None


class TestSimulateDca:
    def test_plain_dca_invests_every_period(self):
        bars = _bars(155)  # 2026-01-01 起 155 天，跨 6 个自然月
        r = simulate_dca(bars, [], 1000, 'monthly', enhanced=False)
        assert r['periods'] == 6
        assert r['periods_invested'] == 6
        assert r['total_invested'] == 6000.0  # 6 × 1000（价 1.0，整手 1000 份）
        assert r['final_value'] == 6000.0
        assert r['return_pct'] == 0
        # 曲线等长且末值一致
        assert len(r['dates']) == len(r['cost']) == len(r['value']) == 155
        assert r['cost'][-1] == 6000.0

    def test_enhanced_pauses_at_high_and_doubles_at_low(self):
        """PE 前 100 天高位（分位≈100 → 停投），后 55 天暴跌（分位低 → 多投）"""
        bars = _bars(155)
        vals = _vals(155, lambda i: 30.0 if i < 100 else 10.0)
        plain = simulate_dca(bars, [], 1000, 'monthly', enhanced=False)
        enh = simulate_dca(bars, vals, 1000, 'monthly', enhanced=True)
        # 普通定投：6 期 × 1000
        assert plain['total_invested'] == 6000.0
        # 增强定投：1 月样本不足按 1×（1000）；2-4 月分位 100 停投；
        # 5 月分位≈17% 投 2×（2000）、6 月分位≈34% 投 1.5×（1500）
        assert enh['total_invested'] == 4500.0
        assert enh['periods_invested'] == 3
        assert enh['paused_periods'] == 3

    def test_enhanced_without_valuation_equals_plain(self):
        """无估值数据时增强退化为 1×，与普通定投一致"""
        bars = _bars(155)
        plain = simulate_dca(bars, [], 1000, 'monthly', enhanced=False)
        enh = simulate_dca(bars, [], 1000, 'monthly', enhanced=True)
        assert enh['total_invested'] == plain['total_invested']
        assert enh['cost'] == plain['cost']

    def test_weekly_frequency_period_count(self):
        bars = _bars(28)  # 2026-01-01（周四）起 28 天，跨 5 个 ISO 周
        r = simulate_dca(bars, [], 500, 'weekly', enhanced=False)
        assert r['periods'] == 5
        assert r['total_invested'] == 2500.0

    def test_empty_bars(self):
        r = simulate_dca([], [], 1000)
        assert r['total_invested'] == 0 and r['dates'] == []

    def test_budget_cap_stops_investing(self):
        """实验室对擂口径：投入不得超出预算，耗尽即停投"""
        bars = _bars(155)
        r = simulate_dca(bars, [], 1000, 'monthly', enhanced=False, budget=2500)
        assert r['total_invested'] <= 2500
        assert r['periods_invested'] == 2      # 只有前两期投得起
        assert r['paused_periods'] == 4        # 后四期因预算耗尽停投
