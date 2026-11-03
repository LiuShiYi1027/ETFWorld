"""
组合层服务：底仓 / 网格 / 现金三账户视图 + 资金流水

口径定义（与前端组合页一致）：
- 本金 = Σ入金 − Σ出金（FundFlowTable）
- 现金 = 本金 − 全部持仓净成本 + 已实现净额（移动加权平均口径，由成交记录推导；
  止盈/止损的盈亏是真实现金流，必须计入弹药水位）
- 底仓 = plan_id 为空的手工持仓；网格持仓 = plan_id 非空的持仓
- 留存底仓 = 网格卖出净回款（扣卖出费用）已覆盖该档全部买入成本（含买入费用）后，
  该档仍剩余的份额；只有这部分份额在留存视图中成本为 0
- 安全线 = ACTIVE + PAUSED 计划"剩余补网资金"合计 ÷ 本金（已买入/留存档不再重复计提）
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import func

from backend.config import settings
from backend.models.database import FundFlowTable, GridPlanTable, TradeTable
from backend.utils.db import get_session
from backend.utils.matching import _strip_sw_suffix, match_index_name

logger = logging.getLogger(__name__)

# 剩余补网资金计入安全线的计划状态（BROKEN 不再挂新单，CLOSED 已归档，均不计入）
_RESERVED_STATUS = ('active', 'paused')


def _round(d: Dict) -> Dict:
    for k in ('shares', 'cost', 'realized_pnl', 'total_fee'):
        d[k] = round(d[k], 2)
    d['avg_cost'] = round(d['cost'] / d['shares'], 4) if d['shares'] > 0 else None
    return d


class PortfolioService:
    """组合三账户与资金流水"""

    # ---------- 资金流水 ----------
    def add_flow(self, params: Dict) -> Dict:
        direction = params.get('direction')
        if direction not in ('deposit', 'withdraw'):
            raise ValueError("direction 必须是 deposit(入金) 或 withdraw(出金)")
        amount = float(params['amount'])
        if amount <= 0:
            raise ValueError('金额必须大于 0')
        flow_date = datetime.strptime(params['flow_date'], '%Y-%m-%d').date()
        with get_session() as session:
            flow = FundFlowTable(flow_date=flow_date, direction=direction,
                                 amount=amount, note=params.get('note'))
            session.add(flow)
            session.flush()
            return self._flow_dict(flow)

    def list_flows(self) -> List[Dict]:
        with get_session() as session:
            flows = session.query(FundFlowTable) \
                .order_by(FundFlowTable.flow_date.desc(), FundFlowTable.id.desc()).all()
            return [self._flow_dict(f) for f in flows]

    def delete_flow(self, flow_id: int) -> bool:
        with get_session() as session:
            f = session.get(FundFlowTable, flow_id)
            if not f:
                return False
            session.delete(f)
            return True

    def principal(self) -> float:
        """本金 = Σ入金 − Σ出金"""
        with get_session() as session:
            def _sum(direction):
                return float(session.query(func.coalesce(func.sum(FundFlowTable.amount), 0))
                             .filter(FundFlowTable.direction == direction).scalar() or 0)
            return round(_sum('deposit') - _sum('withdraw'), 2)

    # ---------- 三账户总览 ----------
    def overview(self, prices: Optional[Dict[str, float]] = None,
                 watchlist_rows: Optional[List[Dict]] = None) -> Dict:
        """
        组合总览。prices 为 {symbol: 现价}，由调用方注入（API 层负责取行情）；
        缺价的标的 market_value 为 None，总计按可得部分计算并标记 missing_prices。
        watchlist_rows（[{name, category}]）注入时附带行业分布 industries 与集中度警告。
        """
        prices = prices or {}
        with get_session() as session:
            trades = session.query(TradeTable) \
                .order_by(TradeTable.trade_date, TradeTable.id).all()
            plans = {p.id: p for p in session.query(GridPlanTable).all()}

        # ---- 按 (symbol, plan_id) 聚合持仓（移动加权成本，与 trade_service 同口径） ----
        groups: Dict = {}
        retained: Dict = {}  # (symbol, plan_id) → 已收回全部档位成本后的剩余份额
        # [买入份额, 卖出份额, 买入成本(含费), 卖出净回款(扣费)]
        level_net: Dict = {}
        for t in trades:
            key = (t.symbol, t.plan_id)
            pos = groups.setdefault(key, {
                'symbol': t.symbol, 'symbol_name': t.symbol_name, 'plan_id': t.plan_id,
                'shares': 0.0, 'cost': 0.0, 'realized_pnl': 0.0, 'total_fee': 0.0})
            price, shares, fee = float(t.price), float(t.shares), float(t.fee or 0)
            pos['total_fee'] += fee
            if t.direction == 'buy':
                pos['shares'] += shares
                pos['cost'] += price * shares + fee
            else:
                avg_cost = pos['cost'] / pos['shares'] if pos['shares'] > 0 else 0
                pos['realized_pnl'] += (price - avg_cost) * shares - fee
                pos['cost'] -= avg_cost * shares
                pos['shares'] -= shares
            if t.plan_id is not None and t.grid_level is not None:
                lv = level_net.setdefault(
                    (t.symbol, t.plan_id, t.grid_level), [0.0, 0.0, 0.0, 0.0])
                if t.direction == 'buy':
                    lv[0] += shares
                    lv[2] += price * shares + fee
                else:
                    lv[1] += shares
                    lv[3] += price * shares - fee

        # “免费”仅指本金及费用已由该档净卖出回款收回；未覆盖成本的亏损/部分卖出残余
        # 仍是普通网格持仓。总成本与已实现收益继续使用移动加权口径，不因展示拆列而重算。
        for (symbol, plan_id, _level), (bought, sold, buy_cost, net_proceeds) in level_net.items():
            if sold > 0 and bought - sold > 1e-6 and net_proceeds + 1e-6 >= buy_cost:
                key = (symbol, plan_id)
                retained[key] = retained.get(key, 0.0) + (bought - sold)

        core, grid, retained_items = [], [], []
        missing_prices = set()
        for (symbol, plan_id), pos in groups.items():
            if pos['shares'] <= 0 and pos['realized_pnl'] == 0:
                continue
            _round(pos)
            cur = prices.get(symbol)
            pos['market_value'] = round(cur * pos['shares'], 2) if cur else None
            pos['unrealized_pnl'] = round(pos['market_value'] - pos['cost'], 2) if cur else None
            if not cur and pos['shares'] > 0:
                missing_prices.add(symbol)
            if plan_id is None:
                pos['plan_name'] = None
                core.append(pos)
                continue
            plan = plans.get(plan_id)
            pos['plan_name'] = plan.name if plan else f'#{plan_id}'
            pos['plan_status'] = plan.status if plan else None
            # 留存是已回本份额的展示子集；其成本为 0，但总账仍保留移动加权成本与收益。
            ret_shares = min(retained.get((symbol, plan_id), 0.0), pos['shares'])
            if ret_shares > 0:
                retained_items.append({
                    'symbol': symbol, 'symbol_name': pos['symbol_name'],
                    'plan_id': plan_id, 'plan_name': pos['plan_name'],
                    'shares': round(ret_shares, 2),
                    'cost': 0.0,
                    'market_value': round(cur * ret_shares, 2) if cur else None,
                })
            pos['retained_shares'] = round(ret_shares, 2)
            grid.append(pos)

        def _mv(items):
            vals = [i['market_value'] for i in items if i['market_value'] is not None]
            return round(sum(vals), 2) if vals else None

        # 安全线分子 = 剩余补网资金：已买入(hold)/留存(keep)档的钱已经花出去，
        # 不再重复计提；wait 与已完成一轮可再买的 sold 档仍需预留弹药。
        from backend.services.grid_service import derive_level_states
        plan_trades: Dict[int, list] = {}
        for t in trades:
            if t.plan_id is not None:
                plan_trades.setdefault(t.plan_id, []).append(t)
        full_capital = 0.0
        for p in plans.values():
            if p.status in _RESERVED_STATUS and p.levels:
                states = derive_level_states(p.levels, plan_trades.get(p.id, []))
                full_capital += sum(float(l.get('amount') or 0)
                                    for l, s in zip(p.levels, states)
                                    if s in ('wait', 'sold'))

        principal = self.principal()
        total_cost = round(sum(p['cost'] for p in core + grid if p['shares'] > 0), 2)
        total_realized = round(sum(p['realized_pnl'] for p in core + grid), 2)
        # 现金 = 本金 − 持仓成本 + 已实现净额（移动加权口径下恒等于
        # 本金 − 累计买入流出 + 累计卖出回流，止盈/止损不再凭空消失）
        cash = round(principal - total_cost + total_realized, 2)
        safety_ratio = round(full_capital / principal, 4) if principal > 0 else None

        result = {
            'principal': principal,
            'cash': cash,
            'total_cost': total_cost,
            'total_realized': total_realized,
            'accounts': {
                'core': {'cost': round(sum(p['cost'] for p in core if p['shares'] > 0), 2),
                         'market_value': _mv(core), 'positions': core},
                'grid': {'cost': round(sum(p['cost'] for p in grid if p['shares'] > 0), 2),
                         'market_value': _mv(grid), 'positions': grid},
                'retained': {'shares': round(sum(i['shares'] for i in retained_items), 2),
                             'cost': 0.0,
                             'market_value': _mv(retained_items), 'items': retained_items},
            },
            'grid_full_capital': round(full_capital, 2),
            'safety_ratio': safety_ratio,
            'safety_warn': safety_ratio is not None and safety_ratio > 0.70,
            'missing_prices': sorted(missing_prices),
        }
        if watchlist_rows:
            industries, warn = self._industry_breakdown(core + grid, watchlist_rows)
            result['industries'] = industries
            result['concentration_warn'] = warn
        return result

    # ---------- 行业集中度 ----------
    @staticmethod
    def _industry_breakdown(positions: List[Dict],
                            watchlist_rows: List[Dict]) -> (List[Dict], bool):
        """持仓（底仓+网格，含留存）按市值（缺价按成本）聚合行业桶。

        行业归属 = symbol_name 子串匹配监控指数：行业一/二级取指数名（去罗马后缀），
        宽基/红利取 category；匹配不到进「其他」。
        """
        names = [r['name'] for r in watchlist_rows]
        cat_map = {r['name']: r.get('category') for r in watchlist_rows}
        buckets: Dict[str, float] = {}
        total = 0.0
        for pos in positions:
            if pos['shares'] <= 0:
                continue
            v = pos['market_value'] if pos['market_value'] is not None else pos['cost']
            idx = match_index_name(pos.get('symbol_name') or '', names)
            if idx is None:
                label = '其他'
            else:
                cat = cat_map.get(idx)
                label = _strip_sw_suffix(idx) if cat in ('行业一级', '行业二级') \
                    else (cat or '其他')
            buckets[label] = buckets.get(label, 0.0) + v
            total += v
        if total <= 0:
            return [], False
        industries = sorted(
            ({'name': k, 'market_value': round(v, 2), 'pct': round(v / total * 100, 1)}
             for k, v in buckets.items()),
            key=lambda b: -b['market_value'])
        warn = industries[0]['pct'] > settings.CONCENTRATION_WARN_PCT
        return industries, warn

    # ---------- 资金分配建议 ----------
    @staticmethod
    def allocation_advice(ov: Dict, readiness_rows: List[Dict]) -> Dict:
        """基于组合口径 + 雷达评分给出下一笔钱的去向建议（纯函数，依赖注入）。

        返回 {level('warn'|'info'|'ok'), headline, detail, candidates[]}
        """
        principal = ov.get('principal') or 0
        cash = ov.get('cash') or 0

        if ov.get('safety_warn'):
            return {'level': 'warn',
                    'headline': '安全线超限：不宜新开网格',
                    'detail': '满格资金已超过本金的 70%。优先考虑入金补足安全垫，'
                              '或暂停/缩减高水位计划，再谈新开仓。',
                    'candidates': []}

        cash_ratio = cash / principal if principal > 0 else None
        if cash_ratio is not None and cash_ratio < 0.10 and ov.get('total_cost'):
            return {'level': 'warn',
                    'headline': '现金水位偏低：留住备用金',
                    'detail': f'现金仅占本金的 {cash_ratio * 100:.0f}%。网格靠现金吃饭，'
                              '新增买入谨慎，优先保证在途计划的补网能力。',
                    'candidates': []}

        if cash_ratio is not None and cash_ratio > 0.30:
            held_names = [p.get('symbol_name') or ''
                          for p in ov['accounts']['core']['positions']
                          + ov['accounts']['grid']['positions']
                          if p['shares'] > 0]
            candidates = []
            for r in readiness_rows:
                if r.get('level') != 'go':
                    continue
                short = _strip_sw_suffix(r.get('name') or '')
                if any(short and short in n for n in held_names):
                    continue  # 已持仓的品种不重复推荐
                candidates.append({'ts_code': r.get('ts_code'), 'name': r.get('name'),
                                   'score': r.get('score'),
                                   'valuation_percentile': r.get('valuation_percentile')})
            candidates.sort(key=lambda c: -(c['score'] or 0))
            if candidates:
                return {'level': 'info',
                        'headline': f'现金占比 {cash_ratio * 100:.0f}%，可分批布局低估品种',
                        'detail': f'参考雷达「适合开启」且未持仓的品种（按评分排序）；'
                                  f'单只新计划满格资金建议 ≤ 本金的 '
                                  f'{settings.MAX_PLAN_CAPITAL_PCT:.0f}%。',
                        'candidates': candidates[:3]}
            return {'level': 'ok',
                    'headline': '现金充裕，但暂无低估候选',
                    'detail': '监控池里没有「适合开启」的品种。现金等待也是策略的一部分，'
                              '可关注机会页评分变化。',
                    'candidates': []}

        return {'level': 'ok',
                'headline': '现金水位合理：按今日待办执行即可',
                'detail': '现金占比处在 10%–30% 的常规区间，无需额外调仓。',
                'candidates': []}

    @staticmethod
    def _flow_dict(f: FundFlowTable) -> Dict:
        return {'id': f.id, 'flow_date': f.flow_date.isoformat(),
                'direction': f.direction, 'amount': float(f.amount), 'note': f.note}
