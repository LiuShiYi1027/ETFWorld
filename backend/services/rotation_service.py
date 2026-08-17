"""
轮动计划服务：动量轮动的执行侧（实验室验证策略 → 这里负责执行）

规则与实验室 simulate_rotation 同口径：每个调仓周期首日计算品种池近 window
日动量，满仓最强者；全部动量 ≤ 0 时空仓。待办只提醒，不代操作：
- switch：先记卖出（旧持仓）再记买入（新目标），两腿都记完本期才算完成
- enter（空仓→买入）/ exit（清仓）为单腿
成交记 trades 表 rotation_plan_id=计划ID，当前持仓由成交记录推导。
"""
import logging
from datetime import date, datetime
from typing import Dict, List, Optional

from backend.models.database import RotationPlanTable, TradeTable
from backend.utils.db import get_session

logger = logging.getLogger(__name__)


def rotation_target(closes_map: Dict[str, List[float]], window: int):
    """动量目标（纯函数）：closes 升序、需 > window 条。
    返回 (目标 symbol 或 None=空仓, {symbol: 动量%})"""
    mom = {}
    for sym, closes in closes_map.items():
        if len(closes) > window and closes[-window - 1] > 0:
            mom[sym] = round((closes[-1] / closes[-window - 1] - 1) * 100, 2)
    if not mom:
        return None, mom
    best = max(mom, key=lambda s: mom[s])
    return (best if mom[best] > 0 else None), mom


def _period_key(d: date, rebalance: str):
    if rebalance == 'monthly':
        return (d.year, d.month)
    iso = d.isocalendar()
    return (iso[0], iso[1])


class RotationService:
    """轮动计划管理 + 调仓待办"""

    def __init__(self, trade_service=None):
        from backend.services.trade_service import TradeService
        self.trade = trade_service or TradeService()

    # ---------- CRUD ----------
    def create_plan(self, params: Dict) -> Dict:
        pool = params.get('pool') or []
        if not (2 <= len(pool) <= 6):
            raise ValueError('品种池需要 2-6 只 ETF')
        window = int(params.get('window') or 20)
        if not (5 <= window <= 120):
            raise ValueError('动量窗口需在 5-120 之间')
        rebalance = params.get('rebalance') or 'weekly'
        if rebalance not in ('weekly', 'monthly'):
            raise ValueError("rebalance 必须是 weekly / monthly")
        with get_session() as session:
            plan = RotationPlanTable(
                name=params.get('name') or '轮动计划',
                pool=[{'symbol': p['symbol'], 'symbol_name': p.get('symbol_name')}
                      for p in pool],
                window=window, rebalance=rebalance,
                note=params.get('note'),
            )
            session.add(plan)
            session.flush()
            return self._to_dict(plan)

    def list_plans(self) -> List[Dict]:
        with get_session() as session:
            plans = session.query(RotationPlanTable) \
                .order_by(RotationPlanTable.created_at.desc()).all()
            return [self._to_dict(p) for p in plans]

    def get_plan(self, plan_id: int) -> Optional[Dict]:
        with get_session() as session:
            plan = session.get(RotationPlanTable, plan_id)
            return self._to_dict(plan) if plan else None

    def update_status(self, plan_id: int, status: str) -> bool:
        if status not in ('active', 'paused', 'closed'):
            raise ValueError('status 必须是 active / paused / closed')
        with get_session() as session:
            plan = session.get(RotationPlanTable, plan_id)
            if not plan:
                return False
            plan.status = status
            return True

    def delete_plan(self, plan_id: int) -> bool:
        with get_session() as session:
            plan = session.get(RotationPlanTable, plan_id)
            if not plan:
                return False
            session.delete(plan)
            return True

    # ---------- 当前持仓（由该计划的成交推导） ----------
    def current_holding(self, plan_id: int) -> Optional[Dict]:
        with get_session() as session:
            trades = session.query(TradeTable) \
                .filter_by(rotation_plan_id=plan_id) \
                .order_by(TradeTable.trade_date, TradeTable.id).all()
        net: Dict[str, float] = {}
        for t in trades:
            net[t.symbol] = net.get(t.symbol, 0.0) + \
                (float(t.shares) if t.direction == 'buy' else -float(t.shares))
        held = {s: v for s, v in net.items() if v > 1e-6}
        if not held:
            return None
        sym = max(held, key=lambda s: held[s])
        name = next((t.symbol_name for t in reversed(trades)
                     if t.symbol == sym and t.symbol_name), sym)
        return {'symbol': sym, 'symbol_name': name, 'shares': round(held[sym], 2)}

    # ---------- 调仓待办 ----------
    def due_todos(self, closes_of, today: Optional[date] = None) -> List[Dict]:
        """closes_of(symbol, days) → 升序收盘价列表（由调用方注入行情）。
        本期双腿（卖+买）都记完成交后待办消失。"""
        today = today or date.today()
        todos = []
        for plan in self.list_plans():
            if plan['status'] != 'active':
                continue
            key = _period_key(today, plan['rebalance'])
            closes_map = {}
            for p in plan['pool']:
                try:
                    closes_map[p['symbol']] = closes_of(p['symbol'], plan['window'] + 10)
                except Exception as e:  # noqa: BLE001
                    logger.warning('轮动行情获取失败 %s: %s', p['symbol'], e)
            target, mom = rotation_target(closes_map, plan['window'])
            holding = self.current_holding(plan['id'])

            # 本期已记的方向
            period_trades = [t for t in self.trade.list_trades(rotation_plan_id=plan['id'])
                             if _period_key(datetime.strptime(t['trade_date'], '%Y-%m-%d').date(),
                                            plan['rebalance']) == key]
            done_dirs = {t['direction'] for t in period_trades}

            cur_sym = holding['symbol'] if holding else None
            if target == cur_sym:
                continue  # 目标与持仓一致（含双空仓），无需动作
            need_sell = cur_sym is not None
            need_buy = target is not None
            sell_pending = need_sell and 'sell' not in done_dirs
            buy_pending = need_buy and 'buy' not in done_dirs
            if not (sell_pending or buy_pending):
                continue  # 本期双腿已完成
            pool_names = {p['symbol']: p.get('symbol_name') or p['symbol']
                          for p in plan['pool']}
            todos.append({
                'rotation_plan_id': plan['id'], 'name': plan['name'],
                'action': 'switch' if (need_sell and need_buy)
                          else ('exit' if need_sell else 'enter'),
                'holding': holding,
                'target': ({'symbol': target, 'symbol_name': pool_names.get(target),
                            'momentum': mom.get(target)} if target else None),
                'momentum': mom,
                'sell_done': 'sell' in done_dirs,
                'buy_done': 'buy' in done_dirs,
                'period_label': '本月' if plan['rebalance'] == 'monthly' else '本周',
                'window': plan['window'],
            })
        return todos

    @staticmethod
    def _to_dict(p: RotationPlanTable) -> Dict:
        return {
            'id': p.id, 'name': p.name, 'pool': p.pool,
            'window': p.window, 'rebalance': p.rebalance,
            'status': p.status, 'note': p.note,
            'created_at': p.created_at.isoformat() if p.created_at else None,
            'updated_at': p.updated_at.isoformat() if p.updated_at else None,
        }
