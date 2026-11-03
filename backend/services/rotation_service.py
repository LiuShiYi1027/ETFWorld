"""
轮动计划服务：动量轮动的执行侧（实验室验证策略 → 这里负责执行）

规则对齐实验室 simulate_rotation 的“周期信号”口径：以周期首个可交易日
收盘数据计算并持久化信号，下一可交易日起执行，满仓最强者；全部动量 ≤ 0
- switch：先记卖出（旧持仓）再记买入（新目标），两腿都记完本期才算完成
- enter（空仓→买入）/ exit（清仓）为单腿
成交记 trades 表 rotation_plan_id=计划ID，当前持仓由成交记录推导。
"""
import logging
from datetime import date, datetime
from typing import Dict, List, Optional

from backend.models.database import RotationPlanTable, RotationSignalTable, TradeTable
from backend.utils.db import get_session

logger = logging.getLogger(__name__)


def rotation_target(closes_map: Dict[str, List[float]], window: int):
    """动量目标（纯函数）：closes 升序、需 > window 条。

    返回 (目标 symbol 或 None=空仓, {symbol: 未舍入动量%})。排序前不得
    舍入，避免相近动量被舍入成并列后由品种池顺序决定目标。
    """
    mom = {}
    for sym, closes in closes_map.items():
        if len(closes) > window and closes[-window - 1] > 0:
            mom[sym] = (closes[-1] / closes[-window - 1] - 1) * 100
    if not mom:
        return None, mom
    best = max(mom, key=lambda s: mom[s])
    return (best if mom[best] > 0 else None), mom


def _period_key(d: date, rebalance: str):
    if rebalance == 'monthly':
        return (d.year, d.month)
    iso = d.isocalendar()
    return (iso[0], iso[1])


def _period_id(key, rebalance: str) -> str:
    return f'{key[0]:04d}-{key[1]:02d}' if rebalance == 'monthly' \
        else f'{key[0]:04d}-W{key[1]:02d}'


def _market_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, '%Y%m%d').date()
    return date.fromisoformat(text[:10])


def _dated_bars(rows) -> List[tuple]:
    """规范化 (trade_date, close)，拒绝无法审计日期的纯价格序列。"""
    by_date = {}
    for row in rows or []:
        if isinstance(row, dict):
            raw_date = row.get('trade_date') or row.get('date')
            close = row.get('close')
        else:
            raw_date, close = row[0], row[1]
        by_date[_market_date(raw_date)] = float(close)
    return sorted(by_date.items())


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
            session.query(RotationSignalTable).filter_by(
                rotation_plan_id=plan_id).delete()
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
        """closes_of(symbol, days) → 带日期的升序日线（由调用方注入行情）。

        信号固定使用周期首个可交易日收盘，并持久化到 rotation_signals；
        首次打开应用的时间不会改变目标。下一可交易日前只返回等待状态，
        不返回可执行 action。任一池内标的缺少同窗数据时，全池不计算目标。
        """
        today = today or date.today()
        todos = []
        for plan in self.list_plans():
            if plan['status'] != 'active':
                continue
            key = _period_key(today, plan['rebalance'])
            sig, missing, signal_date = self._period_signal(
                plan, key, today, closes_of)
            base = {
                'rotation_plan_id': plan['id'], 'name': plan['name'],
                'period_label': '本月' if plan['rebalance'] == 'monthly' else '本周',
                'window': plan['window'],
                'signal_date': signal_date.isoformat() if signal_date else None,
                'market_date': signal_date.isoformat() if signal_date else None,
            }
            if missing:
                todos.append({
                    **base, 'status': 'data_insufficient', 'action': None,
                    'holding': None, 'target': None, 'momentum': {},
                    'missing_data': missing, 'earliest_trade_date': None,
                    'message': '品种池存在缺数，本期不生成调仓行动',
                })
                continue

            target, mom = sig['target'], sig['momentum']
            pool_names = {p['symbol']: p.get('symbol_name') or p['symbol']
                          for p in plan['pool']}
            target_info = ({'symbol': target, 'symbol_name': pool_names.get(target),
                            'momentum': mom.get(target)} if target else None)
            base.update({
                'signal_date': sig['signal_date'],
                'market_date': sig['market_date'],
                'earliest_trade_date': sig['earliest_trade_date'],
            })
            earliest = date.fromisoformat(sig['earliest_trade_date']) \
                if sig['earliest_trade_date'] else None
            if earliest is None or today < earliest:
                todos.append({
                    **base, 'status': 'awaiting_execution', 'action': None,
                    'holding': None, 'target': target_info, 'momentum': mom,
                    'missing_data': [],
                    'message': '等待信号后的首个可交易日',
                })
                continue

            all_trades = self.trade.list_trades(rotation_plan_id=plan['id'])
            net: Dict[str, float] = {}
            for t in all_trades:
                net[t['symbol']] = net.get(t['symbol'], 0.0) + \
                    (float(t['shares']) if t['direction'] == 'buy' else -float(t['shares']))
            held = {s: v for s, v in net.items() if v > 1e-6}
            cur_sym = max(held, key=lambda s: held[s]) if held else None
            holding = None
            if cur_sym is not None:
                name = next((t.get('symbol_name') for t in reversed(all_trades)
                             if t['symbol'] == cur_sym and t.get('symbol_name')), cur_sym)
                holding = {'symbol': cur_sym, 'symbol_name': name,
                           'shares': round(held[cur_sym], 2)}

            # 本期已记的成交（按成交日期所在周期归集）
            period_trades = [t for t in all_trades
                             if _period_key(datetime.strptime(t['trade_date'], '%Y-%m-%d').date(),
                                            plan['rebalance']) == key]

            cur_sym = holding['symbol'] if holding else None
            if target == cur_sym:
                continue  # 目标与持仓一致（含双空仓），无需动作
            period_sell_symbols = {t['symbol'] for t in period_trades if t['direction'] == 'sell'}
            need_sell = cur_sym is not None
            need_buy = target is not None
            sell_done = bool(period_sell_symbols) and all(
                net.get(s, 0.0) <= 1e-6 for s in period_sell_symbols)
            buy_done = need_buy and net.get(target, 0.0) > 1e-6 and any(
                t['direction'] == 'buy' and t['symbol'] == target for t in period_trades)
            sell_pending = need_sell and not sell_done
            buy_pending = need_buy and not buy_done
            if not (sell_pending or buy_pending):
                continue  # 本期双腿已完成
            todos.append({
                **base, 'status': 'pending',
                'action': 'switch' if ((need_sell or period_sell_symbols) and need_buy)
                          else ('exit' if need_sell else 'enter'),
                'holding': holding, 'target': target_info, 'momentum': mom,
                'sell_done': sell_done, 'buy_done': buy_done, 'missing_data': [],
            })
        return todos

    def _period_signal(self, plan: Dict, key: tuple, today: date, bars_of):
        """读取或建立周期信号；缺数信号不落库，以便数据补齐后重试。"""
        period_id = _period_id(key, plan['rebalance'])
        with get_session() as session:
            row = session.query(RotationSignalTable).filter_by(
                rotation_plan_id=plan['id'], period_key=period_id).one_or_none()
            sig = self._signal_to_dict(row) if row else None

        bars_map, missing = {}, []
        for p in plan['pool']:
            try:
                bars = _dated_bars(bars_of(p['symbol'], plan['window'] + 10))
                bars_map[p['symbol']] = [b for b in bars if b[0] <= today]
            except Exception as e:  # noqa: BLE001
                logger.warning('轮动行情获取失败 %s: %s', p['symbol'], e)
                bars_map[p['symbol']] = []
                missing.append(p['symbol'])

        if sig is not None:
            # 已锁定的动量绝不因后续行情或进程重启而重算，只补下一交易日。
            if sig['earliest_trade_date'] is None:
                signal_date = date.fromisoformat(sig['signal_date'])
                earliest = self._next_trade_date(bars_map, signal_date)
                if earliest is not None:
                    with get_session() as session:
                        row = session.query(RotationSignalTable).filter_by(
                            rotation_plan_id=plan['id'], period_key=period_id).one()
                        row.earliest_trade_date = earliest
                    sig['earliest_trade_date'] = earliest.isoformat()
            return sig, [], date.fromisoformat(sig['signal_date'])

        period_dates = [d for bars in bars_map.values() for d, _ in bars
                        if _period_key(d, plan['rebalance']) == key]
        signal_date = min(period_dates) if period_dates else None
        closes_map = {}
        if signal_date is not None:
            for p in plan['pool']:
                symbol = p['symbol']
                history = [b for b in bars_map[symbol] if b[0] <= signal_date]
                if len(history) <= plan['window'] \
                        or history[-1][0] != signal_date \
                        or any(c <= 0 for _, c in history[-plan['window'] - 1:]):
                    if symbol not in missing:
                        missing.append(symbol)
                else:
                    closes_map[symbol] = [c for _, c in history]
        else:
            missing = [p['symbol'] for p in plan['pool']]
        if missing:
            return None, missing, signal_date

        target, momentum = rotation_target(closes_map, plan['window'])
        earliest = self._next_trade_date(bars_map, signal_date)
        with get_session() as session:
            row = RotationSignalTable(
                rotation_plan_id=plan['id'], period_key=period_id,
                signal_date=signal_date, market_date=signal_date,
                earliest_trade_date=earliest, target=target, momentum=momentum,
            )
            session.add(row)
            session.flush()
            sig = self._signal_to_dict(row)
        return sig, [], signal_date

    @staticmethod
    def _next_trade_date(bars_map: Dict[str, List[tuple]], signal_date: date):
        dates = [d for bars in bars_map.values() for d, _ in bars if d > signal_date]
        return min(dates) if dates else None

    @staticmethod
    def _signal_to_dict(row: RotationSignalTable) -> Dict:
        return {
            'signal_date': row.signal_date.isoformat(),
            'market_date': row.market_date.isoformat(),
            'earliest_trade_date': (row.earliest_trade_date.isoformat()
                                    if row.earliest_trade_date else None),
            'target': row.target, 'momentum': row.momentum,
        }

    @staticmethod
    def _to_dict(p: RotationPlanTable) -> Dict:
        return {
            'id': p.id, 'name': p.name, 'pool': p.pool,
            'window': p.window, 'rebalance': p.rebalance,
            'status': p.status, 'note': p.note,
            'created_at': p.created_at.isoformat() if p.created_at else None,
            'updated_at': p.updated_at.isoformat() if p.updated_at else None,
        }
