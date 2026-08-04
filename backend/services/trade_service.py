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
            # 卖出不能超过当前持仓，否则会产生负持仓与错误的收益统计
            if params['direction'] == 'sell':
                held = self._net_shares(session, params['symbol'])
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
                note=params.get('note'),
            )
            session.add(trade)
            session.flush()
            return self._to_dict(trade)

    @staticmethod
    def _net_shares(session, symbol: str) -> float:
        """某标的当前净持仓份额 = 累计买入 − 累计卖出"""
        def _sum(direction):
            return float(session.query(func.coalesce(func.sum(TradeTable.shares), 0))
                         .filter(TradeTable.symbol == symbol,
                                 TradeTable.direction == direction).scalar() or 0)
        return _sum('buy') - _sum('sell')

    def list_trades(self, symbol: str = None, plan_id: int = None) -> List[Dict]:
        with get_session() as session:
            q = session.query(TradeTable)
            if symbol:
                q = q.filter(TradeTable.symbol == symbol)
            if plan_id:
                q = q.filter(TradeTable.plan_id == plan_id)
            trades = q.order_by(TradeTable.trade_date.desc(), TradeTable.id.desc()).all()
            return [self._to_dict(t) for t in trades]

    def delete_trade(self, trade_id: int) -> bool:
        with get_session() as session:
            t = session.get(TradeTable, trade_id)
            if not t:
                return False
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
            'note': t.note,
        }
