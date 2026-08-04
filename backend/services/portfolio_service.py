"""
组合层服务：底仓 / 网格 / 现金三账户视图 + 资金流水

口径定义（与前端组合页一致）：
- 本金 = Σ入金 − Σ出金（FundFlowTable）
- 现金 = 本金 − 全部持仓净成本（移动加权平均口径，由成交记录推导）
- 底仓 = plan_id 为空的手工持仓；网格持仓 = plan_id 非空的持仓
- 留存底仓 = 网格卖出后留在档位上的份额（该档 买−卖 净额），是网格持仓的子集，
  三账户展示时从网格持仓中拆出单列（成本视为 0 的"免费"份额）
- 安全线 = ACTIVE + PAUSED 计划满格资金合计 ÷ 本金
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import func

from backend.models.database import FundFlowTable, GridPlanTable, TradeTable
from backend.utils.db import get_session

logger = logging.getLogger(__name__)

# 满格资金计入安全线的计划状态（BROKEN 不再挂新单，CLOSED 已归档，均不计入）
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
    def overview(self, prices: Optional[Dict[str, float]] = None) -> Dict:
        """
        组合总览。prices 为 {symbol: 现价}，由调用方注入（API 层负责取行情）；
        缺价的标的 market_value 为 None，总计按可得部分计算并标记 missing_prices。
        """
        prices = prices or {}
        with get_session() as session:
            trades = session.query(TradeTable) \
                .order_by(TradeTable.trade_date, TradeTable.id).all()
            plans = {p.id: p for p in session.query(GridPlanTable).all()}

        # ---- 按 (symbol, plan_id) 聚合持仓（移动加权成本，与 trade_service 同口径） ----
        groups: Dict = {}
        retained: Dict = {}  # (symbol, plan_id) → 留存份额
        level_net: Dict = {}  # (symbol, plan_id, grid_level) → [buy, sell]
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
                lv = level_net.setdefault((t.symbol, t.plan_id, t.grid_level), [0.0, 0.0])
                lv[0 if t.direction == 'buy' else 1] += shares

        # 留存份额 = 有卖出记录的档位的净剩余份额（网格 2.0 的免费底仓）
        for (symbol, plan_id, _level), (bought, sold) in level_net.items():
            if sold > 0 and bought - sold > 0:
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
            # 留存拆出：份额与市值单列，成本记 0（利润沉淀）
            ret_shares = min(retained.get((symbol, plan_id), 0.0), pos['shares'])
            if ret_shares > 0:
                retained_items.append({
                    'symbol': symbol, 'symbol_name': pos['symbol_name'],
                    'plan_id': plan_id, 'plan_name': pos['plan_name'],
                    'shares': round(ret_shares, 2),
                    'market_value': round(cur * ret_shares, 2) if cur else None,
                })
            pos['retained_shares'] = round(ret_shares, 2)
            grid.append(pos)

        def _mv(items):
            vals = [i['market_value'] for i in items if i['market_value'] is not None]
            return round(sum(vals), 2) if vals else None

        full_capital = 0.0
        for p in plans.values():
            if p.status in _RESERVED_STATUS and p.levels:
                full_capital += sum(float(l.get('amount') or 0) for l in p.levels)

        principal = self.principal()
        total_cost = round(sum(p['cost'] for p in core + grid if p['shares'] > 0), 2)
        cash = round(principal - total_cost, 2)
        safety_ratio = round(full_capital / principal, 4) if principal > 0 else None

        return {
            'principal': principal,
            'cash': cash,
            'total_cost': total_cost,
            'accounts': {
                'core': {'cost': round(sum(p['cost'] for p in core if p['shares'] > 0), 2),
                         'market_value': _mv(core), 'positions': core},
                'grid': {'cost': round(sum(p['cost'] for p in grid if p['shares'] > 0), 2),
                         'market_value': _mv(grid), 'positions': grid},
                'retained': {'shares': round(sum(i['shares'] for i in retained_items), 2),
                             'market_value': _mv(retained_items), 'items': retained_items},
            },
            'grid_full_capital': round(full_capital, 2),
            'safety_ratio': safety_ratio,
            'safety_warn': safety_ratio is not None and safety_ratio > 0.70,
            'missing_prices': sorted(missing_prices),
        }

    @staticmethod
    def _flow_dict(f: FundFlowTable) -> Dict:
        return {'id': f.id, 'flow_date': f.flow_date.isoformat(),
                'direction': f.direction, 'amount': float(f.amount), 'note': f.note}
