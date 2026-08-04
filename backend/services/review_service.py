"""
复盘服务：分计划统计 + 执行纪律

- 回合 rounds：一次完整的买→卖记 1 回合（按卖出笔数计）
- 已实现：移动加权平均口径（复用 portfolio_service 的分组聚合）
- 留存：卖出后档位的净剩余份额（网格2.0 的免费底仓）
- 纪律违约：日线收盘穿越档位价（买入价 ×1.01 / 卖出价 ×0.99 容差）视为"应操作"，
  其后 7 个交易日内无该档位对应方向的成交 → 记 1 次违约；
  同一档位的连续应操作只记 1 次，直到合规或窗口结束

日线由 etf_service.daily_bars 提供；测试可通过 bars_map 直接注入。
"""
import logging
from datetime import date as date_type
from typing import Dict, List, Optional, Tuple

from backend.services.grid_service import derive_level_states

logger = logging.getLogger(__name__)

_MISS_TOLERANCE = 0.01   # 穿越容差 ±1%
_MISS_WINDOW = 7         # 应操作后的合规窗口（交易日）


class ReviewService:
    """复盘统计（服务实例由 main.py 注入）"""

    def __init__(self, grid_service, trade_service, portfolio_service, etf_service):
        self.grid = grid_service
        self.trade = trade_service
        self.portfolio = portfolio_service
        self.etf = etf_service

    def review(self, lookback_days: int = 250,
               bars_map: Optional[Dict[str, List[Tuple[str, float]]]] = None,
               prices: Optional[Dict[str, float]] = None) -> Dict:
        plans = self.grid.list_plans()
        ov = self.portfolio.overview(prices or {})
        # (symbol, plan_id) → 聚合持仓（含 realized_pnl / retained_shares）
        pos_map = {}
        for pos in ov['accounts']['grid']['positions']:
            pos_map[(pos['symbol'], pos['plan_id'])] = pos

        plan_rows = []
        for p in plans:
            trades = self.trade.list_trades(plan_id=p['id'])
            bars = bars_map.get(p['symbol']) if bars_map is not None \
                else self.etf.daily_bars(p['symbol'], lookback_days)
            missed_buy, missed_sell = self._discipline(p, trades, bars)
            pos = pos_map.get((p['symbol'], p['id']), {})
            states = derive_level_states(p.get('levels') or [], [_Row(t) for t in trades])
            plan_rows.append({
                'plan_id': p['id'], 'name': p['name'], 'symbol': p['symbol'],
                'symbol_name': p.get('symbol_name'), 'version': p['version'],
                'status': p['status'],
                'rounds': sum(1 for t in trades if t['direction'] == 'sell'),
                'realized_pnl': pos.get('realized_pnl', 0.0),
                'retained_shares': pos.get('retained_shares', 0.0),
                'cells': {'wait': states.count('wait'), 'hold': states.count('hold'),
                          'sold': states.count('sold'), 'keep': states.count('keep')},
                'missed_buy': missed_buy, 'missed_sell': missed_sell,
                'trades_count': len(trades),
            })

        return {
            'plans': plan_rows,
            'totals': {
                'rounds': sum(r['rounds'] for r in plan_rows),
                'realized_pnl': round(sum(r['realized_pnl'] for r in plan_rows), 2),
                'missed_buy': sum(r['missed_buy'] for r in plan_rows),
                'missed_sell': sum(r['missed_sell'] for r in plan_rows),
                'total_fee': round(sum(
                    float(t['fee'] or 0) for t in self.trade.list_trades()), 2),
            },
        }

    # ---------- 纪律统计 ----------
    def _discipline(self, plan: Dict, trades: List[Dict],
                    bars: List[Tuple[str, float]]) -> Tuple[int, int]:
        """(该买没买次数, 该卖没卖次数)。bars 为空时返回 (0, 0) 并记日志。"""
        levels = plan.get('levels') or []
        if not levels or not bars:
            return 0, 0
        # (档位, 方向) → 成交日期集合（统一为 YYYYMMDD 紧凑格式，与日线对齐）
        done: Dict[tuple, set] = {}
        for t in trades:
            if t.get('grid_level') is None:
                continue
            done.setdefault((t['grid_level'], t['direction']), set()) \
                .add(t['trade_date'].replace('-', ''))

        missed_buy = missed_sell = 0
        for l in levels:
            buys = done.get((l['level'], 'buy'), set())
            sells = done.get((l['level'], 'sell'), set())
            missed_buy += self._misses(bars, float(l['buy_price']), 'buy', buys, sells)
            # 卖出纪律只对"买入过"的档位评估——没持有过的档位谈不上该卖
            if buys:
                missed_sell += self._misses(bars, float(l['sell_price']), 'sell', buys, sells)
        return missed_buy, missed_sell

    @staticmethod
    def _misses(bars: List[Tuple[str, float]], level_price: float, direction: str,
                buy_dates: set, sell_dates: set) -> int:
        """单档单方向违约计数。

        语义（执行状态感知）：
        - 买入义务只在档位"未持有"时存在：已持有的档穿越买价不算该买
        - 卖出义务只在档位"持有中"时存在：未持有的档穿越卖价不算该卖
        - 触发日起 7 个交易日内无对应成交 → 记 1 次；
          同一波连续穿越只记 1 次（退出触发区后再进入才算新事件）
        """
        def triggered(close: float) -> bool:
            return close <= level_price * (1 + _MISS_TOLERANCE) if direction == 'buy' \
                else close >= level_price * (1 - _MISS_TOLERANCE)

        def held(d: str) -> bool:
            return sum(1 for x in buy_dates if x <= d) > \
                sum(1 for x in sell_dates if x <= d)

        obliged = (direction == 'sell')  # 卖出义务要求持有，买入义务要求未持有
        done_dates = sell_dates if direction == 'sell' else buy_dates
        misses = 0
        i, n = 0, len(bars)
        while i < n:
            d, close = bars[i]
            if not triggered(close):
                i += 1
                continue
            if held(d) != obliged:  # 义务状态不匹配：本波不算违约
                while i < n and triggered(bars[i][1]):
                    i += 1
                continue
            window = {bars[j][0] for j in range(i, min(i + _MISS_WINDOW, n))}
            if not (window & done_dates):
                misses += 1
            while i < n and triggered(bars[i][1]):  # 跳过本波触发段
                i += 1
        return misses


class _Row:
    """把 trade dict 包装成 derive_level_states 需要的属性访问形态"""

    __slots__ = ('direction', 'shares', 'grid_level')

    def __init__(self, d: Dict):
        self.direction = d['direction']
        self.shares = d['shares']
        self.grid_level = d.get('grid_level')
