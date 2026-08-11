"""
定投计划服务：估值增强定投（低估多投、正常少投、高估停投）

与网格计划的差别：没有档位表，每期只有「投多少」一个决策——
基准金额 × 分位倍数（settings.DCA_MULTIPLIERS）。
定投成交记为 trades 表 plan_id=NULL + dca_plan_id=计划ID，持仓自然落入组合层底仓。
"""
import logging
from datetime import date, datetime
from typing import Dict, List, Optional

from backend.config import settings
from backend.models.database import DcaPlanTable, ValuationTable
from backend.services.trade_service import TradeService
from backend.utils.db import get_session
from backend.utils.matching import match_index_name

logger = logging.getLogger(__name__)


def _period_key(d: date, frequency: str):
    """某日期所属定投周期：weekly=ISO(年,周)，monthly=(年,月)"""
    if frequency == 'monthly':
        return (d.year, d.month)
    iso = d.isocalendar()
    return (iso[0], iso[1])


class DcaService:
    """定投计划管理 + 待投计算 + 纪律统计"""

    def __init__(self, trade_service: TradeService = None):
        self.trade = trade_service or TradeService()

    # ---------- 计划 CRUD ----------
    def create_plan(self, params: Dict) -> Dict:
        base_amount = float(params.get('base_amount') or 0)
        if base_amount <= 0:
            raise ValueError('基准金额必须大于 0')
        frequency = params.get('frequency') or 'weekly'
        if frequency not in ('weekly', 'monthly'):
            raise ValueError("frequency 必须是 weekly(每周) 或 monthly(每月)")
        if not params.get('symbol'):
            raise ValueError('请填写标的代码')
        with get_session() as session:
            plan = DcaPlanTable(
                name=params.get('name') or (params.get('symbol_name') or '') + '定投',
                symbol=params['symbol'],
                symbol_name=params.get('symbol_name'),
                base_amount=base_amount,
                frequency=frequency,
                note=params.get('note'),
            )
            session.add(plan)
            session.flush()
            return self._to_dict(plan)

    def list_plans(self) -> List[Dict]:
        with get_session() as session:
            plans = session.query(DcaPlanTable) \
                .order_by(DcaPlanTable.created_at.desc()).all()
            return [self._to_dict(p) for p in plans]

    def get_plan(self, plan_id: int) -> Optional[Dict]:
        with get_session() as session:
            plan = session.get(DcaPlanTable, plan_id)
            return self._to_dict(plan) if plan else None

    def update_status(self, plan_id: int, status: str) -> bool:
        if status not in ('active', 'paused', 'closed'):
            raise ValueError('status 必须是 active / paused / closed')
        with get_session() as session:
            plan = session.get(DcaPlanTable, plan_id)
            if not plan:
                return False
            plan.status = status
            return True

    def delete_plan(self, plan_id: int) -> bool:
        with get_session() as session:
            plan = session.get(DcaPlanTable, plan_id)
            if not plan:
                return False
            session.delete(plan)
            return True

    # ---------- 投入建议（纯函数）----------
    @staticmethod
    def suggest(plan: Dict, valuation_pct: Optional[float]) -> Dict:
        """按综合分位给本期投入建议：倍数/金额/动作/标签"""
        base = float(plan['base_amount'])
        if valuation_pct is None:
            return {'multiplier': 1.0, 'amount': base, 'action': 'invest',
                    'label': '未关联指数，按基础金额'}
        if valuation_pct >= settings.DCA_PROFIT_TAKE_PCT:
            return {'multiplier': 0.0, 'amount': 0.0, 'action': 'profit_take',
                    'label': f'分位 {valuation_pct:.0f}% · 极高估，建议分批止盈'}
        for lo, hi, mult, label in settings.DCA_MULTIPLIERS:
            if lo <= valuation_pct < hi:
                action = 'invest' if mult > 0 else 'pause'
                return {'multiplier': mult, 'amount': round(base * mult, 2),
                        'action': action,
                        'label': f'分位 {valuation_pct:.0f}% · {label}'}
        return {'multiplier': 1.0, 'amount': base, 'action': 'invest',
                'label': '按基础金额'}

    # ---------- 待投计算 ----------
    def due_todos(self, readiness_map: Dict[str, Dict],
                  today: Optional[date] = None) -> List[Dict]:
        """active 计划中本周期未投的，产出待办（已投/暂停/关闭不提醒）"""
        today = today or date.today()
        cur_key = _period_key(today, 'weekly')
        cur_key_m = _period_key(today, 'monthly')
        todos = []
        for plan in self.list_plans():
            if plan['status'] != 'active':
                continue
            freq = plan['frequency']
            key = cur_key_m if freq == 'monthly' else cur_key
            buys = [t for t in self.trade.list_trades(dca_plan_id=plan['id'])
                    if t['direction'] == 'buy']
            invested = any(
                _period_key(datetime.strptime(t['trade_date'], '%Y-%m-%d').date(), freq) == key
                for t in buys)
            if invested:
                continue
            idx_name = match_index_name(plan.get('symbol_name') or '',
                                        list(readiness_map.keys()))
            idx = readiness_map.get(idx_name) if idx_name else None
            pct = idx.get('valuation_percentile') if idx else None
            s = self.suggest(plan, pct)
            pk = (f'{key[0]}-{key[1]:02d}' if freq == 'monthly'
                  else f'{key[0]}W{key[1]:02d}')
            todos.append({
                'dca_plan_id': plan['id'], 'name': plan['name'],
                'symbol': plan['symbol'], 'symbol_name': plan.get('symbol_name'),
                'frequency': freq, 'period_key': pk,
                'period_label': '本月' if freq == 'monthly' else '本周',
                'base_amount': float(plan['base_amount']),
                'multiplier': s['multiplier'], 'amount': s['amount'],
                'action': s['action'], 'label': s['label'],
                'valuation_pct': pct, 'index_name': idx_name,
                'periods_done': len({_period_key(
                    datetime.strptime(t['trade_date'], '%Y-%m-%d').date(), freq)
                    for t in buys}),
            })
        return todos

    # ---------- 回测 ----------
    def valuation_history(self, symbol_name: str):
        """名称匹配监控指数 → (指数名, [(date'YYYYMMDD', pe, pb)] 升序)；匹配不到返回 (None, [])"""
        from backend.services.watchlist_service import WatchlistService
        rows = WatchlistService().list_indices()
        idx_name = match_index_name(symbol_name or '', [r['name'] for r in rows])
        if not idx_name:
            return None, []
        ts_code = next(r['ts_code'] for r in rows if r['name'] == idx_name)
        with get_session() as session:
            q = session.query(ValuationTable.trade_date, ValuationTable.pe_ttm,
                              ValuationTable.pb) \
                .filter_by(ts_code=ts_code) \
                .order_by(ValuationTable.trade_date).all()
        hist = [(d.strftime('%Y%m%d'),
                 float(pe) if pe is not None else None,
                 float(pb) if pb is not None else None) for d, pe, pb in q]
        return idx_name, hist

    def backtest(self, params: Dict, bars: List[tuple]) -> Dict:
        """定投回测：普通定投 vs 估值增强定投同屏对比。

        bars 由调用方注入（API 层取行情）；估值历史从库内读取。
        未关联指数时 enhanced 退化为 1×（与普通定投相同），has_valuation=False 提示前端。
        """
        from backend.services.backtest_service import simulate_dca
        idx_name, hist = self.valuation_history(params.get('symbol_name'))
        base = float(params['base_amount'])
        freq = params.get('frequency') or 'weekly'
        return {
            'index_name': idx_name,
            'has_valuation': bool(hist),
            'n': len(bars),
            'plain': simulate_dca(bars, [], base, freq, enhanced=False),
            'enhanced': simulate_dca(bars, hist, base, freq, enhanced=True),
        }

    # ---------- 纪律统计 ----------
    def plan_summary(self, plan_id: int) -> Optional[Dict]:
        plan = self.get_plan(plan_id)
        if not plan:
            return None
        freq = plan['frequency']
        buys = [t for t in self.trade.list_trades(dca_plan_id=plan_id)
                if t['direction'] == 'buy']
        total_invested = round(sum(t['price'] * t['shares'] for t in buys), 2)
        done_keys = {_period_key(datetime.strptime(t['trade_date'], '%Y-%m-%d').date(), freq)
                     for t in buys}
        start = plan['created_at'][:10] if plan.get('created_at') else None
        elapsed = 0
        if start:
            d0 = datetime.strptime(start, '%Y-%m-%d').date()
            if freq == 'monthly':
                k0, k1 = _period_key(d0, freq), _period_key(date.today(), freq)
                elapsed = (k1[0] - k0[0]) * 12 + (k1[1] - k0[1]) + 1
            else:
                k0, k1 = _period_key(d0, freq), _period_key(date.today(), freq)
                elapsed = (k1[0] * 53 + k1[1]) - (k0[0] * 53 + k0[1]) + 1
        return {
            'plan_id': plan_id,
            'total_invested': total_invested,
            'periods_done': len(done_keys),
            'periods_elapsed': max(elapsed, len(done_keys)),
            'periods_missed': max(0, elapsed - len(done_keys)),
        }

    @staticmethod
    def _to_dict(p: DcaPlanTable) -> Dict:
        return {
            'id': p.id, 'name': p.name, 'symbol': p.symbol,
            'symbol_name': p.symbol_name,
            'base_amount': float(p.base_amount),
            'frequency': p.frequency, 'status': p.status, 'note': p.note,
            'created_at': p.created_at.isoformat() if p.created_at else None,
            'updated_at': p.updated_at.isoformat() if p.updated_at else None,
        }
