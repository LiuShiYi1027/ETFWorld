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


def simulate_grid(prices: List[float], step: float, count: int, amount: float,
                  inc: float = 0, ret: float = 0) -> Dict:
    """在给定价格序列上跑一遍网格，返回净值曲线与统计。"""
    if not prices:
        return {'g': [], 'h': [], 'grid_ret': 0, 'hold_ret': 0,
                'trades': 0, 'max_dd': 0, 'budget': 0, 'n': 0,
                'retained_shares': 0, 'broken_idx': None, 'invested_pct': 0}
    base = prices[0]
    levels = generate_levels(base, step, count, amount, inc, ret)
    budget = sum(l['amount'] for l in levels) or 1.0
    book = [{'buy': l['buy_price'], 'sell': l['sell_price'],
             'shares': l['shares'], 'sell_shares': l['sell_shares'],
             'retained_shares': l['retained_shares'],
             'amount': l['amount'], 'held': False}
            for l in levels]
    lowest_buy = levels[-1]['buy_price']

    realized = 0.0
    retained = 0  # 留利润积累的免费底仓份额（网格2.0）
    trades = 0
    peak = -1e9
    max_dd = 0.0
    broken_idx = None
    invested = 0.0  # 已投入资金占比的逐日累计（算资金利用率）
    g: List[float] = []
    h: List[float] = []

    for idx, pr in enumerate(prices):
        for L in book:
            if not L['held'] and pr <= L['buy']:
                L['held'] = True
            elif L['held'] and pr >= L['sell']:
                L['held'] = False
                # 卖出份额兑现并收回该格全部本金，留存份额转为底仓
                realized += L['sell_shares'] * L['sell'] - L['amount']
                retained += L['retained_shares']
                trades += 1
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
    }


class BacktestService:
    """回测与参数寻优"""

    def backtest(self, params: Dict, prices: List[float]) -> Dict:
        r = simulate_grid(
            prices,
            float(params['grid_step']), int(params['grid_count']),
            float(params['amount_per_grid']),
            float(params.get('step_increase', 0)),
            float(params.get('profit_retention', 0)),
        )
        r['base'] = prices[0] if prices else None
        r['last'] = prices[-1] if prices else None
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
                        'dd': b['max_dd'], 'trades': b['trades'], 'score': score}
                cells.append(cell)
                if best is None or score > best['score']:
                    best = cell
        return {'cells': cells, 'best': best, 'steps': steps, 'counts': counts, 'n': len(prices)}
