"""
复盘服务：分计划统计 + 执行纪律

- 回合 rounds：一次完整的买→卖记 1 回合（按卖出笔数计）
- 已实现：移动加权平均口径（复用 portfolio_service 的分组聚合）
- 留存：卖出后档位的净剩余份额（网格2.0 的免费底仓）
- 纪律违约：未复权日线收盘触及档位价即视为"应操作"，不设提前触发容差；
  触发当日为第 1 个交易日，第 7 日内有对应成交即合规；窗口未走完记 pending，
  不计违约。同一档位的连续应操作只记 1 次，直到合规或窗口结束。

日线由 etf_service.daily_raw_bars 提供；测试可通过 bars_map 直接注入原始价。
"""
import logging
from typing import Dict, List, Optional, Tuple

from backend.services.grid_service import derive_level_states

logger = logging.getLogger(__name__)

_MISS_WINDOW = 7         # 含触发日在内的合规窗口（交易日）


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
                else self.etf.daily_raw_bars(p['symbol'], lookback_days)
            missed_buy, missed_sell, pending_buy, pending_sell = \
                self._discipline(p, trades, bars)
            pos = pos_map.get((p['symbol'], p['id']), {})
            states = derive_level_states(p.get('levels') or [], [_Row(t) for t in trades])
            plan_rows.append({
                'plan_id': p['id'], 'name': p['name'], 'symbol': p['symbol'],
                'symbol_name': p.get('symbol_name'), 'version': p['version'],
                'status': p['status'], 'price_basis': 'raw',
                'rounds': sum(1 for t in trades if t['direction'] == 'sell'),
                'realized_pnl': pos.get('realized_pnl', 0.0),
                'retained_shares': pos.get('retained_shares', 0.0),
                'cells': {'wait': states.count('wait'), 'hold': states.count('hold'),
                          'sold': states.count('sold'), 'keep': states.count('keep')},
                'missed_buy': missed_buy, 'missed_sell': missed_sell,
                'pending_buy': pending_buy, 'pending_sell': pending_sell,
                'trades_count': len(trades),
            })

        return {
            'plans': plan_rows,
            'totals': {
                'rounds': sum(r['rounds'] for r in plan_rows),
                'realized_pnl': round(sum(r['realized_pnl'] for r in plan_rows), 2),
                'missed_buy': sum(r['missed_buy'] for r in plan_rows),
                'missed_sell': sum(r['missed_sell'] for r in plan_rows),
                'pending_buy': sum(r['pending_buy'] for r in plan_rows),
                'pending_sell': sum(r['pending_sell'] for r in plan_rows),
                'total_fee': round(sum(
                    float(t['fee'] or 0) for t in self.trade.list_trades()), 2),
            },
        }

    # ---------- 纪律统计 ----------
    def _discipline(self, plan: Dict, trades: List[Dict],
                    bars: List[Tuple[str, float]]) -> Tuple[int, int, int, int]:
        """(买入违约, 卖出违约, 买入待观察, 卖出待观察)。"""
        levels = plan.get('levels') or []
        if not levels or not bars:
            return 0, 0, 0, 0
        done: Dict[tuple, set] = {}
        for t in trades:
            if t.get('grid_level') is None:
                continue
            done.setdefault((t['grid_level'], t['direction']), set()) \
                .add(t['trade_date'].replace('-', ''))

        missed_buy = missed_sell = pending_buy = pending_sell = 0
        for level in levels:
            buys = done.get((level['level'], 'buy'), set())
            sells = done.get((level['level'], 'sell'), set())
            missed, pending = self._misses(
                bars, float(level['buy_price']), 'buy', buys, sells)
            missed_buy += missed
            pending_buy += pending
            # 卖出纪律只对"买入过"的档位评估——没持有过的档位谈不上该卖。
            if buys:
                missed, pending = self._misses(
                    bars, float(level['sell_price']), 'sell', buys, sells)
                missed_sell += missed
                pending_sell += pending
        return missed_buy, missed_sell, pending_buy, pending_sell

    @staticmethod
    def _misses(bars: List[Tuple[str, float]], level_price: float, direction: str,
                buy_dates: set, sell_dates: set) -> Tuple[int, int]:
        """单档单方向 (违约数, 待观察数)，严格按原始价触及档位判定。

        触发当日是窗口第 1 日，第 7 日成交仍合规；可用 bars 不足 7 日且
        尚未成交时为 pending，不提前记违约。
        """
        def triggered(close: float) -> bool:
            return close <= level_price if direction == 'buy' else close >= level_price

        def held(d: str) -> bool:
            return sum(1 for x in buy_dates if x <= d) > \
                sum(1 for x in sell_dates if x <= d)

        obliged = direction == 'sell'
        done_dates = sell_dates if direction == 'sell' else buy_dates
        misses = pending = 0
        i, n = 0, len(bars)
        while i < n:
            d, close = bars[i]
            if not triggered(close):
                i += 1
                continue
            if held(d) != obliged:
                while i < n and triggered(bars[i][1]):
                    i += 1
                continue
            window = {bars[j][0] for j in range(i, min(i + _MISS_WINDOW, n))}
            if not (window & done_dates):
                if i + _MISS_WINDOW <= n:
                    misses += 1
                else:
                    pending += 1
            while i < n and triggered(bars[i][1]):
                i += 1
        return misses, pending


class _Row:
    """把 trade dict 包装成 derive_level_states 需要的属性访问形态"""

    __slots__ = ('direction', 'shares', 'grid_level', 'price', 'fee')

    def __init__(self, d: Dict):
        self.direction = d['direction']
        self.shares = d['shares']
        self.grid_level = d.get('grid_level')
        self.price = d['price']
        self.fee = d.get('fee', 0)
