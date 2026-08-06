"""
网格回测 + 参数寻优

在真实历史价格上模拟"网格策略 vs 一直持有"：
- 基准价取窗口起点价（相当于"那时候按这套参数开网格"）
- 价格逐日下穿买入价→买入、上穿卖出价→卖出兑现
- 账户价值 =(预算-已投入)+持仓市值+底仓市值+已实现，收益率对预算归一
- 留利润(网格2.0)：卖出时只卖 sell_shares，本金全部收回，retained_shares
  转入免费底仓永久持有（市值计入账户，不再参与网格买卖）
- 破网：价格跌破最后一格买入价后无格可买，账户等同满仓持有，
  用 broken_idx 标出首次跌破的交易日供前端提示

仅用于理解策略特性，不代表未来收益。
"""
import logging
from typing import Dict, List, Optional

from backend.services.grid_service import generate_levels

logger = logging.getLogger(__name__)

DEFAULT_STEPS = [3, 5, 8, 10, 12]
DEFAULT_COUNTS = [6, 8, 10, 12, 14]

# 活性门槛：窗口内完整套利次数低于此值视为"低活性"（更像抄底而非网格）
LOW_ACTIVITY_TRADES = 3


def simulate_grid(prices: List[float], step: float, count: int, amount: float,
                  inc: float = 0, ret: float = 0, base: float = None) -> Dict:
    """在给定价格序列上跑一遍网格，返回净值曲线与统计。
    base 缺省取序列首日价（窗口起点锚定）；显式传入则作为基准价（穿越点锚定）。"""
    if not prices:
        return {'g': [], 'h': [], 'grid_ret': 0, 'hold_ret': 0,
                'trades': 0, 'max_dd': 0, 'budget': 0, 'n': 0,
                'retained_shares': 0, 'broken_idx': None, 'invested_pct': 0,
                'events': [], 'prices': []}
    base = base or prices[0]
    levels = generate_levels(base, step, count, amount, inc, ret)
    budget = sum(l['amount'] for l in levels) or 1.0
    book = [{'buy': l['buy_price'], 'sell': l['sell_price'],
             'shares': l['shares'], 'sell_shares': l['sell_shares'],
             'retained_shares': l['retained_shares'],
             'amount': l['amount'], 'held': False, 'level': l['level']}
            for l in levels]
    lowest_buy = levels[-1]['buy_price']

    realized = 0.0
    retained = 0  # 留利润积累的免费底仓份额（网格2.0）
    trades = 0
    peak = -1e9
    max_dd = 0.0
    broken_idx = None
    invested = 0.0  # 已投入资金占比的逐日累计（算资金利用率）
    events: List[Dict] = []  # 成交事件：{i, dir, level, price}，供回测图标注买卖点
    g: List[float] = []
    h: List[float] = []

    for idx, pr in enumerate(prices):
        for L in book:
            if not L['held'] and pr <= L['buy']:
                L['held'] = True
                events.append({'i': idx, 'dir': 'buy', 'level': L['level'], 'price': L['buy']})
            elif L['held'] and pr >= L['sell']:
                L['held'] = False
                # 卖出份额兑现并收回该格全部本金，留存份额转为底仓
                realized += L['sell_shares'] * L['sell'] - L['amount']
                retained += L['retained_shares']
                trades += 1
                events.append({'i': idx, 'dir': 'sell', 'level': L['level'], 'price': L['sell']})
        if broken_idx is None and pr < lowest_buy:
            broken_idx = idx
        mv = sum(L['shares'] * pr for L in book if L['held'])
        inv = sum(L['amount'] for L in book if L['held'])
        invested += inv / budget
        gr = ((budget - inv) + mv + retained * pr + realized) / budget * 100 - 100
        hr = pr / base * 100 - 100
        g.append(round(gr, 2))
        h.append(round(hr, 2))
        peak = max(peak, gr)
        max_dd = min(max_dd, gr - peak)

    return {
        'g': g, 'h': h,
        'grid_ret': round(g[-1], 2),
        'hold_ret': round(h[-1], 2),
        'trades': trades,
        'max_dd': round(abs(max_dd), 2),
        'budget': round(budget, 2),
        'n': len(prices),
        'retained_shares': retained,
        'broken_idx': broken_idx,
        'invested_pct': round(invested / len(prices) * 100, 1),
        'events': events,
        'prices': [round(p, 4) for p in prices],
    }


def simulate_rebase_grid(prices: List[float], step: float, count: int, amount: float,
                         inc: float = 0, ret: float = 0) -> Dict:
    """自动上移重开口径的回测（与 simulate_grid 同输入，供静态/重开对比）。

    规则：价格涨过当前网格第 1 格卖出价（此时所有持仓必然已兑现、账户全现金），
    立即以现价为新基准重开网格（与产品的「上移重开」语义一致）。
    每次重开占用一份新的预算，收益率对累计投入资本归一。
    """
    if not prices:
        return {'g': [], 'trades': 0, 'rebases': 0, 'grid_ret': 0, 'max_dd': 0,
                'invested_pct': 0, 'events': []}
    top_step = step / 100
    base = prices[0]
    total_capital = 0.0
    realized = 0.0
    retained = 0
    rebases = 0
    trades = 0
    peak = -1e9
    max_dd = 0.0
    invested = 0.0
    events: List[Dict] = []
    g: List[float] = []
    book: List[Dict] = []

    for idx, pr in enumerate(prices):
        if pr > base / (1 - top_step):  # 涨穿顶格 → 网格全空 → 上移重开
            base = pr
            rebases += 1
            levels = generate_levels(base, step, count, amount, inc, ret)
            total_capital += sum(l['amount'] for l in levels)
            book = [{'buy': l['buy_price'], 'sell': l['sell_price'],
                     'shares': l['shares'], 'sell_shares': l['sell_shares'],
                     'retained_shares': l['retained_shares'],
                     'amount': l['amount'], 'held': False, 'level': l['level']}
                    for l in levels]
        for L in book:
            if not L['held'] and pr <= L['buy']:
                L['held'] = True
                events.append({'i': idx, 'dir': 'buy', 'level': L['level'], 'price': L['buy']})
            elif L['held'] and pr >= L['sell']:
                L['held'] = False
                realized += L['sell_shares'] * L['sell'] - L['amount']
                retained += L['retained_shares']
                trades += 1
                events.append({'i': idx, 'dir': 'sell', 'level': L['level'], 'price': L['sell']})
        if not book or total_capital <= 0:
            g.append(g[-1] if g else 0.0)
            continue
        inv = sum(L['amount'] for L in book if L['held'])
        mv = sum(L['shares'] * pr for L in book if L['held'])
        invested += inv / total_capital
        gr = ((total_capital - inv) + mv + retained * pr + realized) / total_capital * 100 - 100
        g.append(round(gr, 2))
        peak = max(peak, gr)
        max_dd = min(max_dd, gr - peak)

    return {
        'g': g,
        'trades': trades,
        'rebases': rebases,
        'grid_ret': round(g[-1], 2),
        'max_dd': round(abs(max_dd), 2),
        'invested_pct': round(invested / len(prices) * 100, 1),
        'events': events,
    }


class BacktestService:
    """回测与参数寻优"""
    def backtest(self, params: Dict, prices: List[float],
                 dates: List[str] = None) -> Dict:
        anchor = params.get('anchor', 'window')
        cross_idx = None
        if anchor == 'cross' and len(prices) >= 2:
            # 穿越点锚定：基准=窗口末日价格，找最近一次从上往下穿越该价位的日子
            today = prices[-1]
            for i in range(len(prices) - 1, 0, -1):
                if prices[i] <= today < prices[i - 1]:
                    cross_idx = i
                    break
            if cross_idx is not None:
                prices = prices[cross_idx:]
                if dates:
                    dates = dates[cross_idx:]
        r = simulate_grid(
            prices,
            float(params['grid_step']), int(params['grid_count']),
            float(params['amount_per_grid']),
            float(params.get('step_increase', 0)),
            float(params.get('profit_retention', 0)),
            base=(prices[0] if anchor == 'cross' else None),
        )
        r['base'] = prices[0] if prices else None
        r['last'] = prices[-1] if prices else None
        r['anchor'] = anchor
        r['cross_idx'] = cross_idx  # None 表示窗口内从未穿越（回退为窗口起点）
        r['dates'] = dates or []    # 与 prices 等长的交易日（YYYYMMDD），供前端日期轴
        # 对比口径：自动上移重开（涨穿顶格即重开），同一段行情并排评估
        if params.get('compare_rebase'):
            r['rebase'] = simulate_rebase_grid(
                prices, float(params['grid_step']), int(params['grid_count']),
                float(params['amount_per_grid']),
                float(params.get('step_increase', 0)),
                float(params.get('profit_retention', 0)))
        return r

    def optimize(self, params: Dict, prices: List[float],
                 steps: Optional[List[float]] = None,
                 counts: Optional[List[int]] = None) -> Dict:
        steps = steps or DEFAULT_STEPS
        counts = counts or DEFAULT_COUNTS
        amount = float(params['amount_per_grid'])
        inc = float(params.get('step_increase', 0))
        ret = float(params.get('profit_retention', 0))
        cells = []
        best = None
        for st in steps:
            for ct in counts:
                b = simulate_grid(prices, st, ct, amount, inc, ret)
                # 风险调整：收益减去回撤惩罚
                score = round(b['grid_ret'] - 0.45 * b['max_dd'], 2)
                cell = {'step': st, 'count': ct, 'ret': b['grid_ret'],
                        'dd': b['max_dd'], 'trades': b['trades'],
                        'invested_pct': b['invested_pct'],
                        'low_activity': b['trades'] < LOW_ACTIVITY_TRADES,
                        'score': score}
                cells.append(cell)
                if best is None or score > best['score']:
                    best = cell
        # best 若是低活性，同时在合格组合里给一个备选（保留信息，不做硬淘汰）
        qualified = [c for c in cells if not c['low_activity']]
        best_active = max(qualified, key=lambda c: c['score']) if qualified else None
        return {'cells': cells, 'best': best, 'best_active': best_active,
                'low_activity_trades': LOW_ACTIVITY_TRADES,
                'steps': steps, 'counts': counts, 'n': len(prices)}
