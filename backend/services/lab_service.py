"""
策略实验室装配层：多策略对擂（统一净值口径）+ 研究笔记

统一口径：每条策略曲线归一为净值（起点=1.0），横轴统一交易日。
- hold：一直持有（首日满仓）
- grid：等金额网格（simulate_grid 的账户%直接转净值）
- dca：估值增强定投（账户 = 未投现金 + 持仓市值，对 budget 归一）
- rotation：动量轮动（simulate_rotation 的账户对 budget 归一）
"""
import logging
from typing import Dict, List, Optional

from backend.models.database import ResearchNoteTable
from backend.services.backtest_service import (simulate_dca, simulate_grid,
                                               simulate_rotation)
from backend.services.dca_service import DcaService
from backend.utils.db import get_session

logger = logging.getLogger(__name__)

_SERIES_STYLE = {
    'hold': {'name': '一直持有', 'color': '#A8A29E'},
    'grid': {'name': '网格', 'color': '#15803D'},
    'dca': {'name': '估值增强定投', 'color': '#2563EB'},
    'rotation': {'name': '动量轮动', 'color': '#B45309'},
}


def _max_dd(nav: List[float]) -> float:
    peak, dd = -1e18, 0.0
    for v in nav:
        peak = max(peak, v)
        if peak > 0:
            dd = max(dd, (peak - v) / peak)
    return round(dd * 100, 2)


class LabService:
    """策略对擂装配 + 研究笔记"""

    # ---------- 对擂 ----------
    def compare(self, params: Dict, bars_of) -> Dict:
        """
        params.kind = 'single'：单品种对擂（symbol + strategies ∈ hold/grid/dca）
                     'rotation'：轮动沙盒（pool 2-6 只 + window + rebalance）
        bars_of: symbol → [(date, close)]，由调用方注入（API 层取行情）。
        """
        kind = params.get('kind', 'single')
        lookback = int(params.get('lookback_days', 750))
        budget = float(params.get('budget') or 100000)
        if kind == 'rotation':
            return self._compare_rotation(params, bars_of, lookback, budget)
        return self._compare_single(params, bars_of, lookback, budget)

    def _compare_single(self, params, bars_of, lookback, budget) -> Dict:
        symbol = params['symbol']
        bars = bars_of(symbol, lookback)
        if len(bars) < 60:
            raise ValueError('历史数据不足（需≥60个交易日）')
        dates = [d for d, _ in bars]
        prices = [c for _, c in bars]
        strategies = params.get('strategies') or ['hold', 'grid', 'dca']
        series = []

        if 'hold' in strategies:
            nav = [round(p / prices[0], 4) for p in prices]
            series.append({**_SERIES_STYLE['hold'], 'key': 'hold', 'nav': nav,
                           'stats': {'ret': round((nav[-1] - 1) * 100, 2),
                                     'max_dd': _max_dd(nav), 'trades': 1,
                                     'final_value': round(budget * nav[-1], 2)}})

        if 'grid' in strategies:
            g = params.get('grid') or {}
            r = simulate_grid(
                prices, float(g.get('grid_step', 5)), int(g.get('grid_count', 10)),
                float(g.get('amount_per_grid', 10000)))
            nav = [round(1 + v / 100, 4) for v in r['g']]
            series.append({**_SERIES_STYLE['grid'], 'key': 'grid', 'nav': nav,
                           'stats': {'ret': r['grid_ret'], 'max_dd': r['max_dd'],
                                     'trades': r['trades'],
                                     'invested_pct': r['invested_pct'],
                                     'final_value': round(budget * nav[-1], 2)}})

        if 'dca' in strategies:
            d = params.get('dca') or {}
            _, hist = DcaService().valuation_history(params.get('symbol_name'))
            r = simulate_dca(bars, hist, float(d.get('base_amount', 2000)),
                             d.get('frequency') or 'weekly', enhanced=True,
                             budget=budget)  # 对擂口径：投入不得超出预算
            nav = [round(((budget - c) + v) / budget, 4)
                   for c, v in zip(r['cost'], r['value'])]
            series.append({**_SERIES_STYLE['dca'], 'key': 'dca', 'nav': nav,
                           'has_valuation': bool(hist),
                           'stats': {'ret': round((nav[-1] - 1) * 100, 2),
                                     'max_dd': _max_dd(nav),
                                     'trades': r['periods_invested'],
                                     'invested': r['total_invested'],
                                     'final_value': round(budget * nav[-1], 2)}})

        return {'kind': 'single', 'symbol': symbol,
                'symbol_name': params.get('symbol_name'),
                'dates': dates, 'budget': budget, 'series': series}

    def _compare_rotation(self, params, bars_of, lookback, budget) -> Dict:
        pool = params.get('pool') or []
        if not (2 <= len(pool) <= 6):
            raise ValueError('轮动品种池需要 2-6 只 ETF')
        bars_map = {}
        names = {}
        for item in pool:
            sym = item['symbol'] if isinstance(item, dict) else item
            bars = bars_of(sym, lookback)
            if len(bars) < 60:
                raise ValueError(f'{sym} 历史数据不足（需≥60个交易日）')
            bars_map[sym] = bars
            names[sym] = (item.get('symbol_name') if isinstance(item, dict) else None) or sym
        window = int(params.get('window', 20))
        rebalance = params.get('rebalance') or 'weekly'
        r = simulate_rotation(bars_map, window=window,
                              rebalance=rebalance, budget=budget)
        if not r['dates']:
            raise ValueError('品种池交易日交集过短，无法回测')
        nav = [round(v / budget, 4) for v in r['account']]
        series = [{**_SERIES_STYLE['rotation'], 'key': 'rotation', 'nav': nav,
                   'stats': {'ret': r['ret'], 'max_dd': r['max_dd'],
                             'trades': r['switches'],
                             'final_value': r['final_value'],
                             'holding': r['holding']}}]
        # 池内各品种持有曲线作参照
        for sym, bars in bars_map.items():
            px = dict(bars)
            ref = [round(px[d] / px[r['dates'][0]], 4) for d in r['dates']]
            series.append({'key': f'hold_{sym}', 'name': f'持有·{names[sym]}',
                           'color': '#D6D3CB', 'nav': ref,
                           'stats': {'ret': round((ref[-1] - 1) * 100, 2),
                                     'max_dd': _max_dd(ref), 'trades': 1,
                                     'final_value': round(budget * ref[-1], 2)}})
        return {'kind': 'rotation', 'pool': [{'symbol': s, 'symbol_name': names[s]}
                                             for s in bars_map],
                'dates': r['dates'], 'budget': budget, 'series': series,
                'events': r['events'], 'window': window, 'rebalance': rebalance}

    # ---------- 研究笔记 ----------
    def save_note(self, params: Dict) -> Dict:
        title = (params.get('title') or '').strip()
        if not title:
            raise ValueError('标题不能为空')
        if not params.get('spec'):
            raise ValueError('缺少回测参数 spec')
        with get_session() as session:
            note = ResearchNoteTable(title=title, spec=params['spec'],
                                     stats=params.get('stats'),
                                     note=params.get('note'))
            session.add(note)
            session.flush()
            return self._note_dict(note)

    def list_notes(self) -> List[Dict]:
        with get_session() as session:
            rows = session.query(ResearchNoteTable) \
                .order_by(ResearchNoteTable.created_at.desc()).all()
            return [self._note_dict(r) for r in rows]

    def delete_note(self, note_id: int) -> bool:
        with get_session() as session:
            r = session.get(ResearchNoteTable, note_id)
            if not r:
                return False
            session.delete(r)
            return True

    @staticmethod
    def _note_dict(r: ResearchNoteTable) -> Dict:
        return {'id': r.id, 'title': r.title, 'spec': r.spec, 'stats': r.stats,
                'note': r.note,
                'created_at': r.created_at.isoformat() if r.created_at else None}
