"""
交易记录服务：录入交易、持仓跟踪、收益统计
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import func

from backend.models.database import GridPlanTable, TradeTable
from backend.services.grid_service import match_grid_level
from backend.utils.db import get_session

logger = logging.getLogger(__name__)


class TradeService:
    """交易记录与持仓统计"""

    def add_trade(self, params: Dict) -> Dict:
        with get_session() as session:
            # 卖出不能超过对应账户当前持仓，否则会产生负持仓与错误的收益统计。
            # 必须按账户口径校验：底仓/网格/定投/轮动分开，不能用底仓份额记网格卖出。
            if params['direction'] == 'sell':
                held = self._net_shares(
                    session, params['symbol'],
                    plan_id=params.get('plan_id'),
                    dca_plan_id=params.get('dca_plan_id'),
                    rotation_plan_id=params.get('rotation_plan_id'))
                if float(params['shares']) > held + 1e-6:
                    raise ValueError(
                        f"卖出 {float(params['shares']):.0f} 份超过当前持仓 {held:.0f} 份")
            # 关联计划且未指定档位时，按方向与价格自动匹配最近档位（容差 ±1.5%）
            grid_level = params.get('grid_level')
            if params.get('plan_id') and grid_level is None:
                plan = session.get(GridPlanTable, params['plan_id'])
                if plan and plan.levels:
                    grid_level = match_grid_level(
                        plan.levels, params['direction'], float(params['price']))
            trade = TradeTable(
                plan_id=params.get('plan_id'),
                symbol=params['symbol'],
                symbol_name=params.get('symbol_name'),
                trade_date=datetime.strptime(params['trade_date'], '%Y-%m-%d').date(),
                direction=params['direction'],
                price=params['price'],
                shares=params['shares'],
                fee=params.get('fee', 0),
                grid_level=grid_level,
                dca_plan_id=params.get('dca_plan_id'),
                rotation_plan_id=params.get('rotation_plan_id'),
                note=params.get('note'),
            )
            session.add(trade)
            session.flush()
            return self._to_dict(trade)

    @staticmethod
    def _net_shares(session, symbol: str, plan_id=None, dca_plan_id=None,
                    rotation_plan_id=None) -> float:
        """某账户当前净持仓份额 = 累计买入 − 累计卖出。

        账户口径与组合页一致：plan_id/dca_plan_id/rotation_plan_id 指定哪个算哪个；
        三者都为空时只算底仓，避免跨账户挪用份额。
        """
        def _sum(direction):
            q = session.query(func.coalesce(func.sum(TradeTable.shares), 0)).filter(
                TradeTable.symbol == symbol,
                TradeTable.direction == direction)
            if plan_id is not None:
                q = q.filter(TradeTable.plan_id == plan_id)
            elif dca_plan_id is not None:
                q = q.filter(TradeTable.dca_plan_id == dca_plan_id)
            elif rotation_plan_id is not None:
                q = q.filter(TradeTable.rotation_plan_id == rotation_plan_id)
            else:
                q = q.filter(TradeTable.plan_id.is_(None),
                             TradeTable.dca_plan_id.is_(None),
                             TradeTable.rotation_plan_id.is_(None))
            return float(q.scalar() or 0)
        return _sum('buy') - _sum('sell')

    def list_trades(self, symbol: str = None, plan_id: int = None,
                    dca_plan_id: int = None, rotation_plan_id: int = None) -> List[Dict]:
        with get_session() as session:
            q = session.query(TradeTable)
            if symbol:
                q = q.filter(TradeTable.symbol == symbol)
            if plan_id:
                q = q.filter(TradeTable.plan_id == plan_id)
            if dca_plan_id:
                q = q.filter(TradeTable.dca_plan_id == dca_plan_id)
            if rotation_plan_id:
                q = q.filter(TradeTable.rotation_plan_id == rotation_plan_id)
            trades = q.order_by(TradeTable.trade_date.desc(), TradeTable.id.desc()).all()
            return [self._to_dict(t) for t in trades]

    def delete_trade(self, trade_id: int) -> bool:
        with get_session() as session:
            t = session.get(TradeTable, trade_id)
            if not t:
                return False
            # 删除成交也按账户回放校验：删掉一笔买入可能让历史中出现负持仓，
            # 进而污染移动加权成本与已实现收益。
            q = session.query(TradeTable).filter(TradeTable.symbol == t.symbol,
                                                 TradeTable.id != trade_id)
            if t.plan_id is not None:
                q = q.filter(TradeTable.plan_id == t.plan_id)
            elif t.dca_plan_id is not None:
                q = q.filter(TradeTable.dca_plan_id == t.dca_plan_id)
            elif t.rotation_plan_id is not None:
                q = q.filter(TradeTable.rotation_plan_id == t.rotation_plan_id)
            else:
                q = q.filter(TradeTable.plan_id.is_(None),
                             TradeTable.dca_plan_id.is_(None),
                             TradeTable.rotation_plan_id.is_(None))
            held = 0.0
            for row in q.order_by(TradeTable.trade_date, TradeTable.id).all():
                held += float(row.shares) if row.direction == 'buy' else -float(row.shares)
                if held < -1e-6:
                    raise ValueError('删除该成交会导致持仓为负，已拒绝')
            session.delete(t)
            return True

    def get_positions(self, current_prices: Dict[str, float] = None) -> List[Dict]:
        """
        按标的汇总持仓：持仓份额、成本、已实现收益

        已实现收益 = 卖出总额 - 卖出份额对应的平均成本（移动加权平均法）
        """
        current_prices = current_prices or {}
        with get_session() as session:
            trades = session.query(TradeTable) \
                .order_by(TradeTable.trade_date, TradeTable.id).all()

        positions: Dict[str, Dict] = {}
        for t in trades:
            pos = positions.setdefault(t.symbol, {
                'symbol': t.symbol,
                'symbol_name': t.symbol_name,
                'shares': 0.0,
                'cost': 0.0,           # 当前持仓总成本
                'realized_pnl': 0.0,   # 已实现收益
                'total_fee': 0.0,
                'buy_count': 0,
                'sell_count': 0,
            })
            price, shares, fee = float(t.price), float(t.shares), float(t.fee or 0)
            pos['total_fee'] += fee
            if t.direction == 'buy':
                pos['shares'] += shares
                pos['cost'] += price * shares + fee
                pos['buy_count'] += 1
            else:
                avg_cost = pos['cost'] / pos['shares'] if pos['shares'] > 0 else 0
                pos['realized_pnl'] += (price - avg_cost) * shares - fee
                pos['cost'] -= avg_cost * shares
                pos['shares'] -= shares
                pos['sell_count'] += 1

        result = []
        for sym, pos in positions.items():
            pos['shares'] = round(pos['shares'], 2)
            pos['cost'] = round(pos['cost'], 2)
            pos['realized_pnl'] = round(pos['realized_pnl'], 2)
            pos['total_fee'] = round(pos['total_fee'], 2)
            pos['avg_cost'] = round(pos['cost'] / pos['shares'], 4) if pos['shares'] > 0 else None
            cur = current_prices.get(sym)
            if cur and pos['shares'] > 0:
                pos['market_value'] = round(cur * pos['shares'], 2)
                pos['unrealized_pnl'] = round(pos['market_value'] - pos['cost'], 2)
            else:
                pos['market_value'] = None
                pos['unrealized_pnl'] = None
            result.append(pos)
        return result

    def get_summary(self) -> Dict:
        """整体收益统计"""
        positions = self.get_positions()
        return {
            'total_realized_pnl': round(sum(p['realized_pnl'] for p in positions), 2),
            'total_holding_cost': round(sum(p['cost'] for p in positions if p['shares'] > 0), 2),
            'total_fee': round(sum(p['total_fee'] for p in positions), 2),
            'position_count': sum(1 for p in positions if p['shares'] > 0),
            'positions': positions,
        }

    @staticmethod
    def _to_dict(t: TradeTable) -> Dict:
        return {
            'id': t.id,
            'plan_id': t.plan_id,
            'symbol': t.symbol,
            'symbol_name': t.symbol_name,
            'trade_date': t.trade_date.isoformat(),
            'direction': t.direction,
            'price': float(t.price),
            'shares': float(t.shares),
            'fee': float(t.fee or 0),
            'amount': round(float(t.price) * float(t.shares), 2),
            'grid_level': t.grid_level,
            'dca_plan_id': t.dca_plan_id,
            'rotation_plan_id': t.rotation_plan_id,
            'note': t.note,
        }
