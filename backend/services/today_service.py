"""
今日视图服务：待办清单 + 四盏预警灯 + 组合摘要

把原先在前端驾驶舱的 planHealth 逻辑移到服务端，并升级为"执行感知"：
- 下一买档：低于现价的最高 buy_price，且该档处于"待买"状态（无成交）
- 下一卖档：高于现价的最低 sell_price，且该档处于"持有"状态（有买未卖）
- 距现价 ≤ near_pct（默认 2%）的档位进入今日待办

预警四盏灯：破网（现价 < 最低档或计划已 broken）、高位运行（现价 ≥ 基准价）、
估值越界（综合分位 >50% —— 雷达否决线，暂停买入）、
退出引导（≥70% 只卖不买 / ≥80% 建议收网，阈值见 settings.EXIT_*）。
"""
import logging
from typing import Dict, List, Optional

from backend.config import settings
from backend.services.grid_service import derive_level_states
from backend.utils.matching import match_index_name  # noqa: F401 — 供外部统一引用

logger = logging.getLogger(__name__)


class TodayService:
    """今日待办与预警聚合（服务实例由 main.py 注入）"""

    def __init__(self, grid_service, trade_service, etf_service,
                 readiness_service, portfolio_service):
        self.grid = grid_service
        self.trade = trade_service
        self.etf = etf_service
        self.readiness = readiness_service
        self.portfolio = portfolio_service

    def today(self, near_pct: float = 2.0, prices: Optional[Dict[str, float]] = None) -> Dict:
        plans = [p for p in self.grid.list_plans() if p['status'] in ('active', 'paused', 'broken')]
        if prices is None:
            prices = self._fetch_prices([p['symbol'] for p in plans])

        readiness = self._readiness_map()
        todos, health = [], []
        alerts = {'broken': [], 'high': [], 'valuation': [], 'exit': []}

        for p in plans:
            levels = p.get('levels') or []
            if not levels:
                continue
            cur = prices.get(p['symbol'])
            h = self._plan_health(p, levels, cur)
            health.append(h)
            if cur is None:
                continue

            # 待办：距现价 ≤ near_pct 的下一买/卖档
            for cand in (h['next_buy'], h['next_sell']):
                if cand and cand['dist_pct'] <= near_pct:
                    todos.append(cand)

            # 预警灯
            if p['status'] == 'broken' or cur < h['low']:
                alerts['broken'].append(self._alert_base(p, cur, h))
            elif cur >= h['top']:
                alerts['high'].append(self._alert_base(p, cur, h))
            idx_name = match_index_name(p.get('symbol_name') or '', list(readiness.keys()))
            idx = readiness.get(idx_name) if idx_name else None
            if idx and idx.get('valuation_percentile') is not None \
                    and idx['valuation_percentile'] > 50:
                alerts['valuation'].append({
                    **self._alert_base(p, cur, h),
                    'index_name': idx_name,
                    'valuation_percentile': idx['valuation_percentile'],
                    'verdict': idx.get('verdict'),
                })
            # 退出引导：≥70% 只卖不买；≥80% 建议收网（CLOSED 计划不参与，上面已过滤）
            if idx and idx.get('valuation_percentile') is not None \
                    and idx['valuation_percentile'] >= settings.EXIT_WARN_PCT:
                pct = idx['valuation_percentile']
                tier = 'exit' if pct >= settings.EXIT_EXIT_PCT else 'warn'
                alerts['exit'].append({
                    'plan_id': p['id'], 'name': p['name'],
                    'symbol': p['symbol'], 'symbol_name': p.get('symbol_name'),
                    'index_name': idx_name,
                    'valuation_percentile': pct,
                    'tier': tier,
                    'verdict': ('高估区：建议逐步卖出剩余持仓并收网' if tier == 'exit'
                                else '偏高区：只卖不买，暂停新开网格'),
                })

        todos.sort(key=lambda t: t['dist_pct'])
        return {
            'todos': todos,
            'alerts': alerts,
            'health': health,
            'portfolio': self._portfolio_brief(prices),
        }

    # ---------- 计划健康（执行感知版 planHealth） ----------
    def _plan_health(self, plan: Dict, levels: List[Dict], cur: Optional[float]) -> Dict:
        low = float(levels[-1]['buy_price'])
        top = float(plan['base_price'])
        h = {'plan_id': plan['id'], 'name': plan['name'], 'symbol': plan['symbol'],
             'symbol_name': plan.get('symbol_name'), 'status': plan['status'],
             'cur': cur, 'low': low, 'top': top, 'next_buy': None, 'next_sell': None}
        if cur is None or cur <= 0:
            return h

        states = self.grid_level_states(plan['id'], levels)
        # 下一买：低于现价的档位里价格最高的"待买"档
        buy_cands = [l for l, s in zip(levels, states)
                     if s == 'wait' and float(l['buy_price']) < cur]
        if buy_cands:
            l = max(buy_cands, key=lambda x: float(x['buy_price']))
            h['next_buy'] = self._todo(plan, l, 'buy', cur)
        # 下一卖：高于现价的档位里价格最低的"持有"档
        sell_cands = [l for l, s in zip(levels, states)
                      if s == 'hold' and float(l['sell_price']) > cur]
        if sell_cands:
            l = min(sell_cands, key=lambda x: float(x['sell_price']))
            h['next_sell'] = self._todo(plan, l, 'sell', cur)
        return h

    def grid_level_states(self, plan_id: int, levels: List[Dict]) -> List[str]:
        trades = self.trade.list_trades(plan_id=plan_id)
        # list_trades 返回 dict，derive 用属性访问——这里转换成轻量命名元组式对象
        return derive_level_states(levels, [_TradeRow(t) for t in trades])

    @staticmethod
    def _todo(plan: Dict, level: Dict, direction: str, cur: float) -> Dict:
        if direction == 'buy':
            price, shares = float(level['buy_price']), int(level['shares'])
            amount = float(level['amount'])
        else:
            price, shares = float(level['sell_price']), int(level['sell_shares'])
            amount = round(price * shares, 2)
        return {
            'plan_id': plan['id'], 'plan_name': plan['name'],
            'symbol': plan['symbol'], 'symbol_name': plan.get('symbol_name'),
            'direction': direction, 'level': level['level'],
            'price': price, 'shares': shares, 'amount': amount,
            'cur': cur, 'dist_pct': round(abs(cur - price) / cur * 100, 2),
        }

    @staticmethod
    def _alert_base(plan: Dict, cur: float, h: Dict) -> Dict:
        return {'plan_id': plan['id'], 'name': plan['name'],
                'symbol': plan['symbol'], 'symbol_name': plan.get('symbol_name'),
                'cur': cur, 'low': h['low'], 'top': h['top'],
                'beyond_pct': round((cur / h['low'] - 1) * 100, 2) if cur < h['low']
                else round((cur / h['top'] - 1) * 100, 2)}

    # ---------- 依赖数据 ----------
    def _fetch_prices(self, symbols: List[str]) -> Dict[str, float]:
        try:
            quotes = self.etf.quotes(sorted(set(symbols)))
            return {s: q['close'] for s, q in quotes.items() if q and q.get('close')}
        except Exception as e:  # noqa: BLE001 — 行情失败降级为无价格
            logger.warning('今日视图行情获取失败: %s', e)
            return {}

    def _readiness_map(self) -> Dict[str, Dict]:
        try:
            return {r['name']: r for r in self.readiness.assess_all()}
        except Exception as e:  # noqa: BLE001 — 无估值数据时跳过估值预警
            logger.warning('今日视图雷达数据获取失败: %s', e)
            return {}

    def _portfolio_brief(self, prices: Dict[str, float]) -> Dict:
        try:
            ov = self.portfolio.overview(prices)
        except Exception as e:  # noqa: BLE001
            logger.warning('今日视图组合摘要获取失败: %s', e)
            return {}
        return {'principal': ov['principal'], 'cash': ov['cash'],
                'total_market_value': None,
                'grid_full_capital': ov['grid_full_capital'],
                'safety_ratio': ov['safety_ratio'], 'safety_warn': ov['safety_warn']}


class _TradeRow:
    """把 trade dict 包装成 derive_level_states 需要的属性访问形态"""

    __slots__ = ('direction', 'shares', 'grid_level')

    def __init__(self, d: Dict):
        self.direction = d['direction']
        self.shares = d['shares']
        self.grid_level = d.get('grid_level')
