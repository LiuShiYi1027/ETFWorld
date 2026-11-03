"""
网格回测 + 参数寻优

在真实历史价格上模拟"网格策略 vs 一直持有"：
- 基准价取窗口起点价（相当于"那时候按这套参数开网格"）
- 价格逐日下穿买入价→买入、上穿卖出价→卖出兑现
- 账户价值 =(预算-已投入)+持仓市值+底仓市值+已实现，收益率对预算归一
- 留利润(网格2.0)：卖出时只卖 sell_shares，本金全部收回，retained_shares
  转入免费底仓永久持有（市值计入账户，不再参与网格买卖）；
  不足一手（shares=0）或留利润吞噬全部卖出份额（sell_shares=0）的档位
  不参与撮合，也不计入套利次数
- 破网：价格跌破最后一格买入价后无格可买，账户等同满仓持有，
  用 broken_idx 标出首次跌破的交易日供前端提示

仅用于理解策略特性，不代表未来收益。
"""
import logging
from bisect import bisect_left
from datetime import date
from typing import Dict, List, Optional

from backend.services.grid_service import generate_levels

logger = logging.getLogger(__name__)

DEFAULT_STEPS = [3, 5, 8, 10, 12]
DEFAULT_COUNTS = [6, 8, 10, 12, 14]

# 活性门槛：验证窗口内完整套利次数低于此值视为"低活性"（更像抄底而非网格）
LOW_ACTIVITY_TRADES = 3

# settings 未配置时仍计入交易成本，避免回测默认呈现不可实现的无摩擦收益。
DEFAULT_BACKTEST_FEE_RATE = 0.0003
DEFAULT_BACKTEST_MIN_FEE = 5.0
DEFAULT_BACKTEST_SLIPPAGE_RATE = 0.0005


def _resolve_costs(fee_rate=None, min_fee=None, slippage_rate=None) -> Dict:
    from backend.config import settings

    fee_rate = getattr(settings, 'BACKTEST_FEE_RATE', DEFAULT_BACKTEST_FEE_RATE) \
        if fee_rate is None else fee_rate
    min_fee = getattr(settings, 'BACKTEST_MIN_FEE', DEFAULT_BACKTEST_MIN_FEE) \
        if min_fee is None else min_fee
    slippage_rate = getattr(settings, 'BACKTEST_SLIPPAGE_RATE',
                            DEFAULT_BACKTEST_SLIPPAGE_RATE) \
        if slippage_rate is None else slippage_rate
    costs = {'fee_rate': float(fee_rate), 'min_fee': float(min_fee),
             'slippage_rate': float(slippage_rate)}
    if any(v < 0 for v in costs.values()):
        raise ValueError('回测费用与滑点不能为负数')
    return costs


def _buy(shares: float, price: float, costs: Dict):
    execution_price = price * (1 + costs['slippage_rate'])
    notional = shares * execution_price
    fee = max(notional * costs['fee_rate'], costs['min_fee'])
    return execution_price, notional + fee, fee, shares * (execution_price - price)


def _sell(shares: float, price: float, costs: Dict):
    execution_price = price * (1 - costs['slippage_rate'])
    notional = shares * execution_price
    fee = max(notional * costs['fee_rate'], costs['min_fee'])
    return execution_price, notional - fee, fee, shares * (price - execution_price)


def _affordable_lot(desired_lot: int, cash: float, price: float, costs: Dict) -> int:
    lot = int(desired_lot / 100) * 100
    while lot > 0:
        if _buy(lot, price, costs)[1] <= cash + 1e-9:
            return lot
        lot -= 100
    return 0


def _simulate_hold(prices: List[float], costs: Dict, budget: float) -> Dict:
    """同预算、整手与建仓成本的一直持有基准；期末持货按市值，不虚拟卖出。"""
    if not prices:
        return {'account': [], 'ret': 0, 'final_value': budget, 'events': [],
                'costs': _cost_report(costs, 0, 0)}
    raw_price = prices[0]
    execution_price = raw_price * (1 + costs['slippage_rate'])
    desired = int(budget / execution_price / 100) * 100 if execution_price > 0 else 0
    lot = _affordable_lot(desired, budget, raw_price, costs)
    if lot <= 0:
        return {'account': [budget] * len(prices), 'ret': 0, 'final_value': budget,
                'events': [], 'costs': _cost_report(costs, 0, 0)}
    execution_price, cash_cost, fee, slip = _buy(lot, raw_price, costs)
    cash = budget - cash_cost
    account = [cash + lot * price for price in prices]
    event = {'i': 0, 'dir': 'buy', 'price': raw_price,
             'execution_price': execution_price, 'shares': lot,
             'fee': fee, 'slippage': slip}
    return {'account': account,
            'ret': round((account[-1] / budget - 1) * 100, 2) if budget > 0 else 0,
            'final_value': round(account[-1], 2), 'events': [event],
            'costs': _cost_report(costs, fee, slip)}


def _cost_report(costs: Dict, fees: float, slippage: float) -> Dict:
    return {**costs, 'fees': round(fees, 2), 'slippage': round(slippage, 2),
            'total': round(fees + slippage, 2)}

def simulate_grid(prices: List[float], step: float, count: int, amount: float,
                  inc: float = 0, ret: float = 0, base: float = None,
                  mode: str = 'amount', shares: float = 0,
                  fee_rate: float = None, min_fee: float = None,
                  slippage_rate: float = None, budget: float = None) -> Dict:
    """按费后现金账户模拟网格；``budget`` 是真实可用现金上限而非事后缩放。"""
    costs = _resolve_costs(fee_rate, min_fee, slippage_rate)
    if not prices:
        initial_budget = float(budget) if budget is not None else 0.0
        return {'g': [], 'h': [], 'grid_ret': 0, 'hold_ret': 0,
                'trades': 0, 'max_dd': 0, 'budget': round(initial_budget, 2), 'n': 0,
                'retained_shares': 0, 'broken_idx': None, 'invested_pct': 0,
                'events': [], 'prices': [], 'account': [],
                'final_value': round(initial_budget, 2),
                'costs': _cost_report(costs, 0, 0)}
    base = prices[0] if base is None else base
    levels = generate_levels(base, step, count, amount, inc, ret,
                             grid_mode=mode, shares_per_grid=shares)
    initial_budget = float(budget) if budget is not None else \
        (sum(l['amount'] for l in levels) or 1.0)
    book = [{'buy': l['buy_price'], 'sell': l['sell_price'],
             'shares': l['shares'], 'sell_shares': l['sell_shares'],
             'retained_shares': l['retained_shares'],
             'amount': l['amount'], 'held': False, 'level': l['level']}
            for l in levels]
    lowest_buy = levels[-1]['buy_price']
    hold = _simulate_hold(prices, costs, initial_budget)

    cash = initial_budget
    retained = 0
    trades = 0
    fees = slippage = 0.0
    peak = 1.0 if initial_budget > 0 else -1e18
    max_dd = 0.0
    broken_idx = None
    invested = 0.0
    events: List[Dict] = []
    g: List[float] = []
    h: List[float] = []
    account: List[float] = []

    for idx, pr in enumerate(prices):
        for L in book:
            if int(L['shares']) <= 0:
                continue
            if not L['held'] and pr <= L['buy']:
                execution_price, cash_cost, fee, slip = _buy(L['shares'], L['buy'], costs)
                if cash_cost > cash + 1e-9:
                    continue  # 现金不足（含费用）则该格不成交，绝不透支或事后缩放
                cash -= cash_cost
                fees += fee
                slippage += slip
                L['held'] = True
                events.append({'i': idx, 'dir': 'buy', 'level': L['level'],
                               'price': L['buy'], 'execution_price': execution_price,
                               'shares': L['shares'], 'fee': fee, 'slippage': slip})
            elif L['held'] and pr >= L['sell']:
                if int(L['sell_shares']) <= 0:
                    continue
                execution_price, proceeds, fee, slip = _sell(
                    L['sell_shares'], L['sell'], costs)
                cash += proceeds
                fees += fee
                slippage += slip
                L['held'] = False
                retained += L['retained_shares']
                trades += 1
                events.append({'i': idx, 'dir': 'sell', 'level': L['level'],
                               'price': L['sell'], 'execution_price': execution_price,
                               'shares': L['sell_shares'], 'fee': fee, 'slippage': slip})
        if broken_idx is None and pr < lowest_buy:
            broken_idx = idx
        mv = sum(L['shares'] * pr for L in book if L['held'])
        inv = sum(L['amount'] for L in book if L['held'])
        invested += inv / initial_budget if initial_budget > 0 else 0
        value = cash + mv + retained * pr
        nav = value / initial_budget if initial_budget > 0 else 0.0
        gr = (nav - 1) * 100
        hr = (hold['account'][idx] / initial_budget - 1) * 100 \
            if initial_budget > 0 else 0
        g.append(round(gr, 2))
        h.append(round(hr, 2))
        account.append(round(value, 2))
        peak = max(peak, nav)
        if peak > 0:
            max_dd = max(max_dd, (peak - nav) / peak)

    final_value = cash + sum(L['shares'] * prices[-1] for L in book if L['held']) \
        + retained * prices[-1]
    return {
        'g': g, 'h': h,
        'grid_ret': round((final_value / initial_budget - 1) * 100, 2)
        if initial_budget > 0 else 0,
        'hold_ret': hold['ret'],
        'trades': trades,
        'max_dd': round(max_dd * 100, 2),
        'budget': round(initial_budget, 2),
        'n': len(prices),
        'retained_shares': retained,
        'broken_idx': broken_idx,
        'invested_pct': round(invested / len(prices) * 100, 1),
        'events': events,
        'prices': [round(p, 4) for p in prices],
        'account': account,
        'final_value': round(final_value, 2),
        'hold_account': [round(v, 2) for v in hold['account']],
        'hold_costs': hold['costs'],
        'costs': _cost_report(costs, fees, slippage),
    }


def simulate_rebase_grid(prices: List[float], step: float, count: int, amount: float,
                         inc: float = 0, ret: float = 0,
                         mode: str = 'amount', shares: float = 0,
                         fee_rate: float = None, min_fee: float = None,
                         slippage_rate: float = None) -> Dict:
    """重开追加预算按外部现金流单位化，收益与回撤使用 TWR 链接的单位净值。"""
    costs = _resolve_costs(fee_rate, min_fee, slippage_rate)
    if not prices:
        return {'g': [], 'trades': 0, 'rebases': 0, 'grid_ret': 0, 'max_dd': 0,
                'invested_pct': 0, 'events': [], 'account': [], 'final_value': 0,
                'total_contributions': 0, 'external_cash_flows': [],
                'costs': _cost_report(costs, 0, 0)}
    top_step = step / 100

    def _open(base_price: float) -> List[Dict]:
        levels = generate_levels(base_price, step, count, amount, inc, ret,
                                 grid_mode=mode, shares_per_grid=shares)
        return [{'buy': l['buy_price'], 'sell': l['sell_price'],
                 'shares': l['shares'], 'sell_shares': l['sell_shares'],
                 'retained_shares': l['retained_shares'],
                 'amount': l['amount'], 'held': False, 'level': l['level']}
                for l in levels]

    base = prices[0]
    book = _open(base)
    total_capital = sum(L['amount'] for L in book)
    cash = total_capital
    units = total_capital
    retained = 0
    rebases = trades = 0
    fees = slippage = 0.0
    peak = 1.0
    max_dd = 0.0
    invested = 0.0
    events: List[Dict] = []
    cash_flows: List[Dict] = []
    g: List[float] = []
    account: List[float] = []
    final_value = total_capital

    for idx, pr in enumerate(prices):
        for L in book:
            if int(L['shares']) <= 0:
                continue
            if not L['held'] and pr <= L['buy']:
                execution_price, cash_cost, fee, slip = _buy(L['shares'], L['buy'], costs)
                if cash_cost > cash + 1e-9:
                    continue
                cash -= cash_cost
                fees += fee
                slippage += slip
                L['held'] = True
                events.append({'i': idx, 'dir': 'buy', 'level': L['level'],
                               'price': L['buy'], 'execution_price': execution_price,
                               'shares': L['shares'], 'fee': fee, 'slippage': slip})
            elif L['held'] and pr >= L['sell']:
                if int(L['sell_shares']) <= 0:
                    continue
                execution_price, proceeds, fee, slip = _sell(
                    L['sell_shares'], L['sell'], costs)
                cash += proceeds
                fees += fee
                slippage += slip
                L['held'] = False
                retained += L['retained_shares']
                trades += 1
                events.append({'i': idx, 'dir': 'sell', 'level': L['level'],
                               'price': L['sell'], 'execution_price': execution_price,
                               'shares': L['sell_shares'], 'fee': fee, 'slippage': slip})

        value_before_flow = cash + retained * pr + sum(
            L['shares'] * pr for L in book if L['held'])
        if pr > base / (1 - top_step):
            base = pr
            rebases += 1
            new_book = _open(base)
            contribution = sum(L['amount'] for L in new_book)
            if contribution > 0:
                if units > 0 and value_before_flow != 0:
                    # 按注资前单位净值增发份额；注资本身不改变单位净值。
                    units += contribution / (value_before_flow / units)
                elif units == 0:
                    units = contribution
                cash += contribution
                total_capital += contribution
                cash_flows.append({'i': idx, 'amount': contribution})
            book = new_book

        inv = sum(L['amount'] for L in book if L['held'])
        mv = sum(L['shares'] * pr for L in book if L['held'])
        invested += inv / total_capital if total_capital > 0 else 0
        final_value = cash + mv + retained * pr
        nav = final_value / units if units > 0 else 1.0
        g.append(round((nav - 1) * 100, 2))
        account.append(round(final_value, 2))
        peak = max(peak, nav)
        if peak > 0:
            max_dd = max(max_dd, (peak - nav) / peak)

    return {
        'g': g,
        'trades': trades,
        'rebases': rebases,
        'grid_ret': g[-1],
        'max_dd': round(max_dd * 100, 2),
        'invested_pct': round(invested / len(prices) * 100, 1),
        'events': events,
        'account': account,
        'final_value': round(final_value, 2),
        'total_contributions': round(total_capital, 2),
        'external_cash_flows': cash_flows,
        'costs': _cost_report(costs, fees, slippage),
        'return_method': 'unit_nav_twr',
    }


class BacktestService:
    """回测与参数寻优"""
    def backtest(self, params: Dict, prices: List[float],
                 dates: List[str] = None) -> Dict:
        anchor = params.get('anchor', 'window')
        cross_idx = None
        if anchor == 'cross' and len(prices) >= 2:
            today = prices[-1]
            for i in range(len(prices) - 1, 0, -1):
                if prices[i] <= today < prices[i - 1]:
                    cross_idx = i
                    break
            if cross_idx is not None:
                prices = prices[cross_idx:]
                if dates:
                    dates = dates[cross_idx:]
        cost_kwargs = {k: params.get(k) for k in ('fee_rate', 'min_fee', 'slippage_rate')}
        r = simulate_grid(
            prices,
            float(params['grid_step']), int(params['grid_count']),
            float(params.get('amount_per_grid') or 0),
            float(params.get('step_increase', 0)),
            float(params.get('profit_retention', 0)),
            base=(prices[0] if anchor == 'cross' else None),
            mode=params.get('grid_mode', 'amount'),
            shares=float(params.get('shares_per_grid') or 0),
            budget=params.get('budget'), **cost_kwargs)
        r['base'] = prices[0] if prices else None
        r['last'] = prices[-1] if prices else None
        r['anchor'] = anchor
        r['cross_idx'] = cross_idx
        r['dates'] = dates or []
        if params.get('compare_rebase'):
            r['rebase'] = simulate_rebase_grid(
                prices, float(params['grid_step']), int(params['grid_count']),
                float(params.get('amount_per_grid') or 0),
                float(params.get('step_increase', 0)),
                float(params.get('profit_retention', 0)),
                mode=params.get('grid_mode', 'amount'),
                shares=float(params.get('shares_per_grid') or 0), **cost_kwargs)
        return r

    def optimize(self, params: Dict, prices: List[float],
                 steps: Optional[List[float]] = None,
                 counts: Optional[List[int]] = None) -> Dict:
        steps = steps or DEFAULT_STEPS
        counts = counts or DEFAULT_COUNTS
        amount = float(params['amount_per_grid'])
        inc = float(params.get('step_increase', 0))
        ret = float(params.get('profit_retention', 0))
        cost_kwargs = {k: params.get(k) for k in ('fee_rate', 'min_fee', 'slippage_rate')}
        costs = _resolve_costs(**cost_kwargs)
        n = len(prices)
        split = max(1, int(n * 0.7)) if n >= 4 else n
        validation = {
            'method': 'chronological_holdout', 'train_ratio': 0.7,
            'train_n': split, 'validation_n': n - split,
            'train_end_idx': split - 1 if split else None,
            'validation_start_idx': split if split < n else None,
            'selection': 'validation_score among active cells; low-activity fallback flagged',
        }
        if n < 4:
            return {'cells': [], 'best': None, 'best_active': None,
                    'low_activity_trades': LOW_ACTIVITY_TRADES,
                    'steps': steps, 'counts': counts, 'n': n,
                    'costs': _cost_report(costs, 0, 0), 'validation': validation,
                    'error': '样本不足，无法进行时间顺序训练/验证'}
        train_prices, validation_prices = prices[:split], prices[split:]
        cells = []
        for st in steps:
            for ct in counts:
                train = simulate_grid(train_prices, st, ct, amount, inc, ret, **cost_kwargs)
                valid = simulate_grid(validation_prices, st, ct, amount, inc, ret,
                                      **cost_kwargs)
                train_score = round(train['grid_ret'] - 0.45 * train['max_dd'], 2)
                valid_score = round(valid['grid_ret'] - 0.45 * valid['max_dd'], 2)
                valid_low = valid['trades'] < LOW_ACTIVITY_TRADES
                cells.append({
                    'step': st, 'count': ct,
                    'ret': valid['grid_ret'], 'dd': valid['max_dd'],
                    'trades': valid['trades'], 'invested_pct': valid['invested_pct'],
                    'low_activity': valid_low, 'score': valid_score,
                    'train_ret': train['grid_ret'], 'train_dd': train['max_dd'],
                    'train_trades': train['trades'], 'train_score': train_score,
                    'train_low_activity': train['trades'] < LOW_ACTIVITY_TRADES,
                    'validation_ret': valid['grid_ret'], 'validation_dd': valid['max_dd'],
                    'validation_trades': valid['trades'],
                    'validation_invested_pct': valid['invested_pct'],
                    'validation_score': valid_score,
                    'validation_low_activity': valid_low,
                })
        qualified = [c for c in cells if not c['validation_low_activity']]
        best_active = max(qualified, key=lambda c: c['validation_score']) if qualified else None
        best = best_active or max(cells, key=lambda c: c['validation_score'])
        return {'cells': cells, 'best': best, 'best_active': best_active,
                'low_activity_trades': LOW_ACTIVITY_TRADES,
                'steps': steps, 'counts': counts, 'n': n,
                'costs': _cost_report(costs, 0, 0), 'validation': validation}


# ---------- 定投回测（估值增强 vs 普通定投对比） ----------

def _trailing_pctile(values: List[float], end: int, window: int = 1250) -> Optional[float]:
    """时点分位：在截至 end 的回看窗口（默认约 5 年=1250 个交易日）内，
    当前值 ≤ 历史值的比例。样本不足 30 条返回 None（与估值分位口径一致）。"""
    seg = [v for v in values[max(0, end - window + 1):end + 1] if v is not None and v > 0]
    if len(seg) < 30:
        return None
    cur = values[end]
    if cur is None or cur <= 0:
        return None
    return round(sum(1 for v in seg if v <= cur) / len(seg) * 100, 2)


def _dca_period_key(dstr: str, frequency: str):
    """交易日 'YYYYMMDD' 的定投周期：weekly=ISO(年,周)，monthly=(年,月)"""
    if frequency == 'monthly':
        return (int(dstr[:4]), int(dstr[4:6]))
    d = date(int(dstr[:4]), int(dstr[4:6]), int(dstr[6:8]))
    iso = d.isocalendar()
    return (iso[0], iso[1])


def _xirr(cash_flows: List[tuple]) -> Optional[float]:
    """按实际日期、ACT/365 求 XIRR；无正负现金流或无有限根时返回 None。"""
    if len(cash_flows) < 2 or not any(v < 0 for _, v in cash_flows) \
            or not any(v > 0 for _, v in cash_flows):
        return None

    def as_date(dstr):
        return date(int(dstr[:4]), int(dstr[4:6]), int(dstr[6:8]))

    start = as_date(cash_flows[0][0])
    dated = [((as_date(d) - start).days / 365.0, v) for d, v in cash_flows]

    def npv(rate):
        return sum(v / ((1 + rate) ** years) for years, v in dated)

    low, high = -0.999999, 1.0
    f_low, f_high = npv(low), npv(high)
    while f_low * f_high > 0 and high < 1e6:
        high *= 2
        f_high = npv(high)
    if f_low * f_high > 0:
        return None
    for _ in range(200):
        mid = (low + high) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-8:
            return mid
        if f_low * f_mid <= 0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    return (low + high) / 2


def simulate_dca(bars: List[tuple], valuations: List[tuple],
                 base_amount: float, frequency: str = 'weekly',
                 enhanced: bool = True, budget: float = None,
                 fee_rate: float = None, min_fee: float = None,
                 slippage_rate: float = None) -> Dict:
    """增强定投在 d 日仅用 d 日前估值出信号，下一交易日成交；收益均为费后。"""
    from backend.config import settings

    costs = _resolve_costs(fee_rate, min_fee, slippage_rate)
    if not bars:
        return {'dates': [], 'cost': [], 'value': [], 'total_invested': 0,
                'final_value': 0, 'return_pct': 0, 'xirr': None, 'periods': 0,
                'periods_invested': 0, 'paused_periods': 0, 'events': [],
                'cash_flows': [], 'costs': _cost_report(costs, 0, 0),
                'return_pct_basis': 'cumulative_actual_investment'}

    val_dates = [v[0] for v in valuations]
    pe_series = [v[1] for v in valuations]
    pb_series = [v[2] for v in valuations]
    invested = shares = fees = slippage = 0.0
    periods, invested_periods, paused_periods = set(), set(), set()
    dates, cost_line, value_line = [], [], []
    events: List[Dict] = []
    cash_flows: List[tuple] = []
    pending = None

    def execute(key, mult, signal_date, execution_date, close):
        nonlocal invested, shares, fees, slippage, pending
        execution_price = close * (1 + costs['slippage_rate'])
        desired = int(base_amount * mult / execution_price / 100) * 100 \
            if execution_price > 0 else 0
        available = float(budget) - invested if budget is not None else float('inf')
        lot = _affordable_lot(desired, available, close, costs)
        if lot > 0:
            execution_price, cash_cost, fee, slip = _buy(lot, close, costs)
            shares += lot
            invested += cash_cost
            fees += fee
            slippage += slip
            invested_periods.add(key)
            cash_flows.append((execution_date, -cash_cost))
            events.append({'date': execution_date, 'signal_date': signal_date,
                           'shares': lot, 'price': close,
                           'execution_price': execution_price, 'fee': fee,
                           'slippage': slip, 'multiplier': mult})
        else:
            paused_periods.add(key)
        pending = None

    for dstr, close in bars:
        if pending is not None:
            execute(pending['key'], pending['mult'], pending['signal_date'], dstr, close)
        key = _dca_period_key(dstr, frequency)
        if key not in periods:
            periods.add(key)
            mult = 1.0
            if enhanced and valuations:
                # 信号日盘后估值不可用于该信号，更不能用于下一日成交。
                i = bisect_left(val_dates, dstr) - 1
                if i >= 0:
                    pcts = [p for p in (_trailing_pctile(pe_series, i),
                                        _trailing_pctile(pb_series, i))
                            if p is not None]
                    pct = sum(pcts) / len(pcts) if pcts else None
                else:
                    pct = None
                if pct is not None and pct >= settings.DCA_PROFIT_TAKE_PCT:
                    mult = 0.0
                elif pct is not None:
                    mult = next((m for lo, hi, m, _ in settings.DCA_MULTIPLIERS
                                 if lo <= pct < hi), 1.0)
            if enhanced:
                pending = {'key': key, 'mult': mult, 'signal_date': dstr}
            else:
                execute(key, mult, dstr, dstr, close)
        dates.append(dstr)
        cost_line.append(round(invested, 2))
        value_line.append(round(shares * close, 2))
    if pending is not None:
        paused_periods.add(pending['key'])  # 样本结束，下一交易日不可见，不伪造成交

    final = shares * bars[-1][1]
    if final > 0:
        cash_flows.append((bars[-1][0], final))
    annualized = _xirr(cash_flows)
    return {
        'dates': dates, 'cost': cost_line, 'value': value_line,
        'total_invested': round(invested, 2),
        'final_value': round(final, 2),
        'return_pct': round((final / invested - 1) * 100, 2) if invested > 0 else 0,
        'return_pct_basis': 'cumulative_actual_investment',
        'xirr': round(annualized * 100, 4) if annualized is not None else None,
        'xirr_basis': 'actual_cash_flow_dates_act_365',
        'periods': len(periods),
        'periods_invested': len(invested_periods),
        'paused_periods': len(paused_periods),
        'events': events,
        'cash_flows': [{'date': d, 'amount': round(v, 2)} for d, v in cash_flows],
        'costs': _cost_report(costs, fees, slippage),
        'execution': 'next_trading_day_after_signal' if enhanced else 'period_first_close',
        'valuation_data': 'strictly_before_signal_date',
    }


# ---------- 轮动回测（动量最强者满仓，全负动量空仓） ----------

def simulate_rotation(bars_by_symbol: Dict[str, List[tuple]],
                      window: int = 20, rebalance: str = 'weekly',
                      budget: float = 100000, fee_rate: float = None,
                      min_fee: float = None, slippage_rate: float = None) -> Dict:
    """d 日收盘计算动量信号，下一交易日按费后现金账户执行调仓。"""
    costs = _resolve_costs(fee_rate, min_fee, slippage_rate)
    symbols = list(bars_by_symbol.keys())
    if len(symbols) < 2 or window < 2:
        return {'dates': [], 'account': [], 'events': [], 'ret': 0,
                'max_dd': 0, 'switches': 0, 'final_value': budget, 'holding': None,
                'costs': _cost_report(costs, 0, 0)}
    common = None
    for s in symbols:
        ds = {d for d, _ in bars_by_symbol[s]}
        common = ds if common is None else common & ds
    dates = sorted(common or [])
    if len(dates) < window + 2:
        return {'dates': [], 'account': [], 'events': [], 'ret': 0,
                'max_dd': 0, 'switches': 0, 'final_value': budget, 'holding': None,
                'costs': _cost_report(costs, 0, 0)}
    px = {s: dict(bars_by_symbol[s]) for s in symbols}
    cash = float(budget)
    held_sym, held_shares = None, 0.0
    events: List[Dict] = []
    switches = 0
    fees = slippage = 0.0
    account: List[float] = []
    peak, max_dd = -1e18, 0.0
    seen_periods = set()
    pending = None
    value = cash

    for i, d in enumerate(dates):
        if pending is not None:
            target = pending['target']
            if target != held_sym:
                changed = False
                if held_sym is not None:
                    raw_price = px[held_sym][d]
                    execution_price, proceeds, fee, slip = _sell(
                        held_shares, raw_price, costs)
                    cash += proceeds
                    fees += fee
                    slippage += slip
                    events.append({'i': i, 'signal_i': pending['i'],
                                   'signal_date': pending['date'], 'date': d,
                                   'dir': 'sell', 'symbol': held_sym,
                                   'price': raw_price, 'execution_price': execution_price,
                                   'shares': held_shares, 'fee': fee, 'slippage': slip})
                    held_shares = 0.0
                    changed = True
                new_sym = None
                if target is not None:
                    raw_price = px[target][d]
                    execution_price = raw_price * (1 + costs['slippage_rate'])
                    desired = int(cash / execution_price / 100) * 100 \
                        if execution_price > 0 else 0
                    lot = _affordable_lot(desired, cash, raw_price, costs)
                    if lot > 0:
                        execution_price, cash_cost, fee, slip = _buy(lot, raw_price, costs)
                        cash -= cash_cost
                        fees += fee
                        slippage += slip
                        held_shares = lot
                        new_sym = target
                        changed = True
                        events.append({'i': i, 'signal_i': pending['i'],
                                       'signal_date': pending['date'], 'date': d,
                                       'dir': 'buy', 'symbol': target,
                                       'price': raw_price, 'execution_price': execution_price,
                                       'shares': lot, 'fee': fee, 'slippage': slip})
                held_sym = new_sym
                if changed:
                    switches += 1
            pending = None

        key = _dca_period_key(d, rebalance)
        if i >= window and key not in seen_periods:
            seen_periods.add(key)
            mom = {}
            for s in symbols:
                p_now, p_prev = px[s][d], px[s][dates[i - window]]
                mom[s] = (p_now / p_prev - 1) if p_prev > 0 else -1
            best = max(symbols, key=lambda s: mom[s])
            pending = {'target': best if mom[best] > 0 else None, 'i': i, 'date': d}

        value = cash + (held_shares * px[held_sym][d] if held_sym else 0.0)
        account.append(round(value, 2))
        peak = max(peak, value)
        if peak > 0:
            max_dd = max(max_dd, (peak - value) / peak)

    return {
        'dates': dates,
        'account': account,
        'events': events,
        'ret': round((value / budget - 1) * 100, 2) if budget > 0 else 0,
        'max_dd': round(max_dd * 100, 2),
        'switches': switches,
        'final_value': round(value, 2),
        'holding': held_sym,
        'costs': _cost_report(costs, fees, slippage),
        'execution': 'next_trading_day_after_signal',
    }
