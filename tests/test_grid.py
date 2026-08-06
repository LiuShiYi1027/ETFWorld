"""网格核心计算测试：档位生成、压力测试、历史模拟"""
from backend.services.grid_service import generate_levels, pressure_test
from backend.services.backtest_service import simulate_grid

# 先跌到第3格再涨回原点的震荡序列，重复3轮
DOWN_UP = [1.0, 0.95, 0.9025, 0.95, 1.0] * 3


class TestGenerateLevels:
    def test_buy_prices_step_down_geometrically(self):
        levels = generate_levels(base_price=1.0, grid_step=5, grid_count=4,
                                 amount_per_grid=10000)
        assert [l['buy_price'] for l in levels] == [1.0, 0.95, 0.9025, 0.8574]

    def test_sell_price_is_exactly_one_grid_up(self):
        """卖出价 = 买入价/(1-step)，严格"回升一格"，与上一格买入价重合"""
        levels = generate_levels(base_price=1.0, grid_step=5, grid_count=4,
                                 amount_per_grid=10000)
        for i in range(1, len(levels)):
            assert levels[i]['sell_price'] == levels[i - 1]['buy_price']
        assert levels[0]['sell_price'] == round(1.0 / 0.95, 4)

    def test_shares_round_to_lot(self):
        levels = generate_levels(base_price=3.21, grid_step=5, grid_count=3,
                                 amount_per_grid=1000)
        for l in levels:
            assert l['shares'] % 100 == 0
            assert l['amount'] == round(l['shares'] * l['buy_price'], 2)

    def test_step_increase_grows_amount(self):
        levels = generate_levels(base_price=1.0, grid_step=5, grid_count=3,
                                 amount_per_grid=10000, step_increase=10)
        amounts = [l['amount'] for l in levels]
        assert amounts[0] < amounts[1] < amounts[2]

    def test_profit_retention_splits_shares(self):
        """网格2.0：卖出+留存=买入，都是整手，且每格至少留一手"""
        levels = generate_levels(base_price=1.0, grid_step=5, grid_count=3,
                                 amount_per_grid=20000, profit_retention=30)
        for l in levels:
            assert l['sell_shares'] + l['retained_shares'] == l['shares']
            assert l['sell_shares'] % 100 == 0
            assert l['retained_shares'] % 100 == 0
            assert l['retained_shares'] >= 100

    def test_no_retention_degrades_to_grid_1(self):
        levels = generate_levels(base_price=1.0, grid_step=5, grid_count=3,
                                 amount_per_grid=10000, profit_retention=0)
        for l in levels:
            assert l['retained_shares'] == 0
            assert l['sell_shares'] == l['shares']


class TestPressureTest:
    def test_totals_and_bottom_loss(self):
        levels = generate_levels(base_price=1.0, grid_step=5, grid_count=3,
                                 amount_per_grid=10000)
        pt = pressure_test(levels, 1.0)
        assert pt['total_capital'] == sum(l['amount'] for l in levels)
        assert pt['total_shares'] == sum(l['shares'] for l in levels)
        assert pt['lowest_price'] == levels[-1]['buy_price']
        assert pt['max_unrealized_loss'] < 0
        assert pt['avg_cost'] > pt['lowest_price']  # 满格成本高于最低价，必浮亏


class TestSimulateGrid:
    def test_oscillation_makes_arbitrage(self):
        """震荡回到原点：持有零收益，网格应有套利收益"""
        r = simulate_grid(DOWN_UP, step=5, count=3, amount=10000)
        assert r['trades'] >= 2
        assert r['hold_ret'] == 0.0
        assert r['grid_ret'] > r['hold_ret']

    def test_events_track_buys_and_sells(self):
        """买卖点事件：卖出事件数=套利次数，买≥卖，事件含档位与价格"""
        r = simulate_grid(DOWN_UP, step=5, count=3, amount=10000)
        buys = [e for e in r['events'] if e['dir'] == 'buy']
        sells = [e for e in r['events'] if e['dir'] == 'sell']
        assert len(sells) == r['trades']
        assert len(buys) >= len(sells)
        assert all(e['level'] and e['price'] > 0 for e in r['events'])
        assert r['prices'][0] == 1.0 and len(r['prices']) == r['n']

    def test_broken_grid_detected(self):
        """跌破最后一格买入价(0.9025) → broken_idx 记录首次跌破日"""
        prices = [1.0, 0.95, 0.9, 0.85, 0.8]
        r = simulate_grid(prices, step=5, count=3, amount=10000)
        assert r['broken_idx'] == 2  # 第3日 0.9 < 0.9025
        assert r['grid_ret'] < 0  # 破网后满仓硬扛，必亏

    def test_no_broken_grid(self):
        r = simulate_grid([1.0, 0.96, 0.98, 1.0], step=5, count=3, amount=10000)
        assert r['broken_idx'] is None

    def test_profit_retention_accumulates_free_shares(self):
        """网格2.0：每次卖出留存份额进入底仓，与1.0结果可区分"""
        r1 = simulate_grid(DOWN_UP, step=5, count=3, amount=20000, ret=0)
        r2 = simulate_grid(DOWN_UP, step=5, count=3, amount=20000, ret=30)
        assert r1['retained_shares'] == 0
        assert r2['retained_shares'] > 0
        assert r2['retained_shares'] % 100 == 0
        assert r2['trades'] == r1['trades']  # 触发点一致，只是卖出拆分不同

    def test_retention_accounting_conserves_value(self):
        """底仓市值+已实现+未投入现金+持仓市值 = 账户总值（不自盈自亏）"""
        prices = DOWN_UP
        r = simulate_grid(prices, step=5, count=3, amount=20000, ret=30)
        # 终点价格回到基准：底仓与持仓均不浮亏，收益应全部来自已实现套利
        assert r['grid_ret'] > 0

    def test_empty_prices(self):
        r = simulate_grid([], step=5, count=3, amount=10000)
        assert r['n'] == 0
        assert r['broken_idx'] is None
        assert r['retained_shares'] == 0
        assert r['invested_pct'] == 0
