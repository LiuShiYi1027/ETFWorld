"""
今日视图服务：待办清单 + 四盏预警灯 + 组合摘要

把原先在前端驾驶舱的 planHealth 逻辑移到服务端，并升级为"执行感知"：
- 下一买档：在"可买"档位（wait，或已完成一轮的 sold）里取距现价最近者，
  价格已穿越档位时优先于更深的未触发档，避免跳空后待办指向错误档位
- 下一卖档：在"持有"档位里取距现价最近者（向上临近或已穿越都算候选）
- 距现价 ≤ near_pct（默认 2%）的档位进入今日待办（仅 active 计划；
  paused/broken 保留健康度与预警灯，但不产生买卖待办）

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
                 readiness_service, portfolio_service, dca_service=None,
                 rotation_service=None):
        self.grid = grid_service
        self.trade = trade_service
        self.etf = etf_service
        self.readiness = readiness_service
        self.portfolio = portfolio_service
        self.dca = dca_service
        self.rotation = rotation_service

    def today(self, near_pct: float = 2.0, prices: Optional[Dict[str, float]] = None) -> Dict:
        plans = [p for p in self.grid.list_plans() if p['status'] in ('active', 'paused', 'broken')]
        if prices is None:
            quotes = self._fetch_quotes([p['symbol'] for p in plans])
            prices = {s: q['close'] for s, q in quotes.items()
                      if q and q.get('close') is not None}
        else:
            # 测试/调用方显式注入现价时视为可用；也允许直接注入完整 quote。
            quotes = {s: (q if isinstance(q, dict) else {'close': q, 'stale': False})
                      for s, q in prices.items()}
            prices = {s: q.get('close') for s, q in quotes.items()
                      if q and q.get('close') is not None}

        readiness = self._readiness_map()
        todos, health = [], []
        alerts = {'broken': [], 'high': [], 'valuation': [], 'exit': [],
                  'corporate_action': []}

        for p in plans:
            corporate = self._corporate_action_check(p)
            if corporate and corporate.get('pause_required'):
                was_active = p['status'] == 'active'
                if was_active:
                    self.grid.update_status(p['id'], 'paused')
                    p['status'] = 'paused'
                alerts['corporate_action'].append({
                    'plan_id': p['id'], 'name': p['name'],
                    'symbol': p['symbol'], 'symbol_name': p.get('symbol_name'),
                    'needs_review': True, 'paused_from_active': was_active,
                    **corporate,
                })

            levels = p.get('levels') or []
            if not levels:
                continue
            quote = quotes.get(p['symbol']) or {}
            stale = bool(quote.get('stale', False))
            cur = prices.get(p['symbol'])
            h = self._plan_health(p, levels, cur)
            h.update({
                'quote_date': quote.get('trade_date'),
                'as_of_date': quote.get('as_of_date'),
                'stale': stale, 'stale_reason': quote.get('stale_reason'),
            })
            if stale:
                h['next_buy'] = None
                h['next_sell'] = None
            health.append(h)
            if cur is None:
                continue

            # 陈旧行情只保留健康快照，不生成可执行待办或价格破位预警。
            if p['status'] == 'active' and not stale:
                for cand in (h['next_buy'], h['next_sell']):
                    if cand and cand['dist_pct'] <= near_pct:
                        todos.append(cand)

            if not stale:
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
        dca_todos = []
        if self.dca is not None:
            try:
                dca_todos = self.dca.due_todos(readiness)
            except Exception as e:  # noqa: BLE001 — 定投待办失败不阻塞主视图
                logger.warning('定投待办计算失败: %s', e)
        rotation_todos = []
        if self.rotation is not None:
            try:
                rotation_todos = self.rotation.due_todos(self.etf.daily_bars)
            except Exception as e:  # noqa: BLE001 — 轮动待办失败不阻塞主视图
                logger.warning('轮动待办计算失败: %s', e)
        return {
            'todos': todos,
            'dca_todos': dca_todos,
            'rotation_todos': rotation_todos,
            'alerts': alerts,
            'health': health,
            'quotes': quotes,
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
        # 下一买：wait 或已完成一轮的 sold 都可买；取距现价最近者，
        # 已穿越的浅档优先于更深的未触发档（跳空时待办指向正确档位）
        buy_cands = [l for l, s in zip(levels, states) if s in ('wait', 'sold')]
        if buy_cands:
            l = min(buy_cands, key=lambda x: abs(cur - float(x['buy_price'])))
            h['next_buy'] = self._todo(plan, l, 'buy', cur)
        # 下一卖：持有档里取距现价最近者（向上临近或已穿越都算候选）
        sell_cands = [l for l, s in zip(levels, states) if s == 'hold']
        if sell_cands:
            l = min(sell_cands, key=lambda x: abs(cur - float(x['sell_price'])))
            level_id = l['level']
            remaining = sum(float(t['shares']) * (1 if t['direction'] == 'buy' else -1)
                            for t in self.trade.list_trades(plan_id=plan['id'])
                            if t.get('grid_level') == level_id)
            # 部分卖出后仍持有：只能卖实际剩余份额，不能再次按计划卖出份额超卖。
            l = {**l, 'sell_shares': min(int(l['sell_shares']), max(0, int(remaining)))}
            h['next_sell'] = self._todo(plan, l, 'sell', cur) if l['sell_shares'] > 0 else None
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
    def _fetch_quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        try:
            return self.etf.quotes(sorted(set(symbols)))
        except Exception as e:  # noqa: BLE001 — 行情失败降级为无价格
            logger.warning('今日视图行情获取失败: %s', e)
            return {}

    def _corporate_action_check(self, plan: Dict) -> Optional[Dict]:
        """适配 ETFService.corporate_action_check(symbol, since_date)。

        因子缺失或检测异常均按 pause_required 处理；接口尚未注入时返回
        None，不伪造“无公司行动”结论。
        """
        checker = getattr(self.etf, 'corporate_action_check', None)
        if not callable(checker):
            return None
        since_date = (plan.get('created_at') or '')[:10]
        if not since_date:
            return {'status': 'unavailable', 'has_changed': None,
                    'pause_required': True, 'changes': [],
                    'message': '缺少计划创建日期，无法核对复权因子'}
        try:
            return checker(plan['symbol'], since_date)
        except Exception as e:  # noqa: BLE001 — 检测不可用时不得继续执行旧网格
            logger.warning('公司行动检测失败 %s: %s', plan['symbol'], e)
            return {'status': 'unavailable', 'has_changed': None,
                    'pause_required': True, 'changes': [],
                    'message': f'公司行动检测不可用：{e}'}

    def _fetch_prices(self, symbols: List[str]) -> Dict[str, float]:
        quotes = self._fetch_quotes(symbols)
        return {s: q['close'] for s, q in quotes.items()
                if q and q.get('close') is not None}

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

    __slots__ = ('direction', 'shares', 'grid_level', 'price', 'fee')

    def __init__(self, d: Dict):
        self.direction = d['direction']
        self.shares = d['shares']
        self.grid_level = d.get('grid_level')
        self.price = d['price']
        self.fee = d.get('fee', 0)
