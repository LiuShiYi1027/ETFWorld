"""定投回测测试：普通定投 vs 估值增强定投（合成序列，确定性断言）"""
from datetime import date, timedelta

import pytest

from backend.services.backtest_service import _trailing_pctile, simulate_dca

ZERO_COSTS = {'fee_rate': 0, 'min_fee': 0, 'slippage_rate': 0}


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
        r = simulate_dca(bars, [], 1000, 'monthly', enhanced=False, **ZERO_COSTS)
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
        plain = simulate_dca(bars, [], 1000, 'monthly', enhanced=False, **ZERO_COSTS)
        enh = simulate_dca(bars, vals, 1000, 'monthly', enhanced=True, **ZERO_COSTS)
        # 普通定投：6 期 × 1000
        assert plain['total_invested'] == 6000.0
        # 增强定投：1 月样本不足按 1×（1000）；2-4 月分位 100 停投；
        # 5 月分位≈17% 投 2×（2000）、6 月分位≈34% 投 1.5×（1500）
        assert enh['total_invested'] == 4500.0
        assert enh['periods_invested'] == 3
        assert enh['paused_periods'] == 3

    def test_enhanced_without_valuation_keeps_next_day_execution(self):
        """无估值数据时倍数退化为 1×，但仍遵守信号次日成交。"""
        bars = _bars(155)
        plain = simulate_dca(bars, [], 1000, 'monthly', enhanced=False, **ZERO_COSTS)
        enh = simulate_dca(bars, [], 1000, 'monthly', enhanced=True, **ZERO_COSTS)
        assert enh['total_invested'] == plain['total_invested']
        assert enh['cost'][-1] == plain['cost'][-1]
        assert enh['events'][0]['date'] > plain['events'][0]['date']

    def test_weekly_frequency_period_count(self):
        bars = _bars(28)  # 2026-01-01（周四）起 28 天，跨 5 个 ISO 周
        r = simulate_dca(bars, [], 500, 'weekly', enhanced=False, **ZERO_COSTS)
        assert r['periods'] == 5
        assert r['total_invested'] == 2500.0

    def test_empty_bars(self):
        r = simulate_dca([], [], 1000, **ZERO_COSTS)
        assert r['total_invested'] == 0 and r['dates'] == []

    def test_budget_cap_stops_investing(self):
        """实验室对擂口径：投入不得超出预算，耗尽即停投"""
        bars = _bars(155)
        r = simulate_dca(bars, [], 1000, 'monthly', enhanced=False, budget=2400,
                         **ZERO_COSTS)
        assert r['total_invested'] == 2400  # 第三期买入剩余预算可承担的 400 份
        assert r['periods_invested'] == 3
        assert r['paused_periods'] == 3       # 后续三期因预算耗尽停投

    def test_enhanced_uses_prior_valuation_and_executes_next_bar(self):
        bars = [('20260102', 1.0), ('20260105', 2.0), ('20260106', 2.0)]
        # 信号日前低估 → 2 倍；信号日盘后突然高估，不得反向污染该信号。
        vals = _vals(30, lambda i: 20.0 if i < 29 else 10.0,
                     start=date(2025, 12, 1)) + [('20260102', 30.0, None)]
        r = simulate_dca(bars, vals, 1000, 'monthly', enhanced=True, **ZERO_COSTS)
        assert len(r['events']) == 1
        event = r['events'][0]
        assert event['signal_date'] == '20260102'
        assert event['date'] == '20260105'
        assert event['price'] == 2.0
        assert event['multiplier'] == 2.0
        assert r['total_invested'] == 2000

    def test_min_fee_is_part_of_invested_cash_and_return(self):
        bars = [('20240102', 1.0), ('20250102', 2.0)]
        r = simulate_dca(bars, [], 1000, 'monthly', enhanced=False,
                         budget=1100, fee_rate=0, min_fee=5, slippage_rate=0)
        assert r['total_invested'] == 1005
        assert r['final_value'] == 2000
        assert r['return_pct'] == pytest.approx((2000 / 1005 - 1) * 100, abs=0.01)
        assert r['costs']['fees'] == 5
        assert r['return_pct_basis'] == 'cumulative_actual_investment'

    def test_xirr_uses_actual_calendar_dates(self):
        bars = [('20240102', 1.0), ('20250102', 2.0)]
        r = simulate_dca(bars, [], 1000, 'monthly', enhanced=False,
                         budget=1000, **ZERO_COSTS)
        # 一次投入、366 天后翻倍：累计收益率 100%，XIRR 按 ACT/365 年化。
        assert r['return_pct'] == 100
        assert r['xirr'] == pytest.approx((2 ** (365 / 366) - 1) * 100, abs=0.001)
        assert r['cash_flows'][0] == {'date': '20240102', 'amount': -1000}
        assert r['cash_flows'][-1] == {'date': '20250102', 'amount': 2000}
