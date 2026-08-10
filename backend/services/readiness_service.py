"""
网格就绪度评估

回答核心问题：一个指数现在能不能开网格？

判断逻辑（E大网格理念）：
  ① 估值安全垫（最重要）：估值分位越低越安全。分位>50% 直接否决。
  ② 波动充足度：年化波动率越大，网格越赚钱，也决定格距大小。
  ③ 行业不死：由标的选择保证（监控池均为不死行业）。

综合评分 0-100，给出"适合/观望/不建议"结论与建议网格参数。
"""
import logging
import math
import statistics
from typing import Dict, List, Optional

from sqlalchemy import func

from backend.config.settings import SUPPORTED_INDICES  # 仅作监控池默认播种来源
from backend.models.database import ValuationTable, ValuationPercentileTable
from backend.services.watchlist_service import WatchlistService
from backend.utils.db import get_session

logger = logging.getLogger(__name__)

# 估值分位硬门槛：高于此值直接否决
VALUATION_VETO = 50.0

_watchlist = WatchlistService()


def _NAME() -> Dict[str, str]:
    return _watchlist.name_map()


def _CATEGORY() -> Dict:
    return _watchlist.category_map()


def _annualized_volatility(prices: List[float]) -> Optional[float]:
    """由价格(或PE)序列计算年化波动率（日对数收益率标准差 × √252）"""
    series = [p for p in prices if p and p > 0]
    if len(series) < 60:
        return None
    rets = [math.log(series[i] / series[i - 1]) for i in range(1, len(series))]
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    return math.sqrt(var) * math.sqrt(252)


def _suggest_grid(volatility: Optional[float]) -> Dict:
    """根据年化波动率建议格距（波动大→格距大）"""
    if volatility is None:
        return {'grid_step': 5, 'grid_count': 10, 'note': '波动数据不足，用默认参数'}
    v = volatility * 100
    if v < 15:
        step = 3
    elif v < 25:
        step = 5
    elif v < 40:
        step = 8
    else:
        step = 10
    return {'grid_step': step, 'grid_count': 10,
            'note': f'年化波动 {v:.1f}%，建议格距 {step}%'}


def _momentum(closes: List[float]) -> Dict:
    """closes 最新在前。返回 20/60/120 日涨跌幅(%)与距区间(约52周)高点距离(%)"""
    empty = {'ret_20d': None, 'ret_60d': None, 'ret_120d': None, 'dist_52w_high': None}
    if not closes:
        return empty

    def ret(n: int) -> Optional[float]:
        if len(closes) > n and closes[n] > 0:
            return round((closes[0] / closes[n] - 1) * 100, 1)
        return None

    high = max(closes)
    return {
        'ret_20d': ret(20), 'ret_60d': ret(60), 'ret_120d': ret(120),
        'dist_52w_high': round((closes[0] / high - 1) * 100, 1) if high > 0 else None,
    }


class ReadinessService:
    """网格就绪度评估"""

    def assess(self, ts_code: str) -> Optional[Dict]:
        """评估单个指数的网格就绪度"""
        with get_session() as session:
            latest_date = session.query(func.max(ValuationTable.trade_date)) \
                .filter(ValuationTable.ts_code == ts_code).scalar()
            if not latest_date:
                return None

            cur = session.query(ValuationTable).filter_by(
                ts_code=ts_code, trade_date=latest_date).first()

            # 估值分位（优先5年，缺则用全部）；全周期分位一并取出，供 AI 研判上下文
            pct_map = {r.period: r for r in session.query(ValuationPercentileTable)
                       .filter_by(ts_code=ts_code, trade_date=latest_date).all()}
            pct = pct_map.get('5y') or pct_map.get('all')

            # 波动率：用近250日收盘价，无收盘价则用PE序列代理
            rows = session.query(ValuationTable.close_price, ValuationTable.pe_ttm) \
                .filter(ValuationTable.ts_code == ts_code) \
                .order_by(ValuationTable.trade_date.desc()).limit(250).all()
            closes = [float(r[0]) for r in rows if r[0] is not None]
            pes = [float(r[1]) for r in rows if r[1] is not None]
            vol = _annualized_volatility(list(reversed(closes))) \
                if len(closes) >= 60 else _annualized_volatility(list(reversed(pes)))
            vol_is_proxy = len(closes) < 60

            # PE/PB 全历史中位数（AI 研判上下文用）
            pe_all = [float(r[0]) for r in session.query(ValuationTable.pe_ttm)
                      .filter(ValuationTable.ts_code == ts_code,
                              ValuationTable.pe_ttm.isnot(None)).all() if r[0] and r[0] > 0]
            pb_all = [float(r[0]) for r in session.query(ValuationTable.pb)
                      .filter(ValuationTable.ts_code == ts_code,
                              ValuationTable.pb.isnot(None)).all() if r[0] and r[0] > 0]

        pe_pct = float(pct.pe_percentile) if pct and pct.pe_percentile is not None else None
        pb_pct = float(pct.pb_percentile) if pct and pct.pb_percentile is not None else None

        # ---- 评分 ----
        reasons = []
        # 估值安全垫 0-70：综合分位 = PE/PB 分位的平均（兼顾盈利与资产两个维度）
        avail = [p for p in (pe_pct, pb_pct) if p is not None]
        valuation_pct = sum(avail) / len(avail) if avail else None

        if valuation_pct is None:
            val_score = 0
            reasons.append('缺少估值分位数据，无法评估安全垫')
        else:
            val_score = (100 - valuation_pct) / 100 * 70
            if valuation_pct < 30:
                reasons.append(f'估值分位 {valuation_pct:.0f}%，处于低估区，安全垫厚 ✓')
            elif valuation_pct < 50:
                reasons.append(f'估值分位 {valuation_pct:.0f}%，偏低，安全垫一般')
            else:
                reasons.append(f'估值分位 {valuation_pct:.0f}%，偏高，下跌风险大 ✗')

        # 波动充足度 0-30
        if vol is None:
            vol_score = 0
            reasons.append('波动数据不足')
        else:
            vol_score = min(vol / 0.40, 1.0) * 30
            tag = '（PE代理估算）' if vol_is_proxy else ''
            reasons.append(f'年化波动率 {vol*100:.1f}%{tag}')

        score = round(val_score + vol_score, 1)

        # ---- 结论（估值分位硬门槛） ----
        if valuation_pct is None:
            verdict, level = '数据不足', 'unknown'
        elif valuation_pct > VALUATION_VETO:
            verdict, level = '不建议（估值偏高）', 'no'
        elif valuation_pct < 30 and score >= 60:
            verdict, level = '适合开启', 'go'
        elif valuation_pct < 40:
            verdict, level = '可小仓试探', 'maybe'
        else:
            verdict, level = '观望', 'wait'

        return {
            'ts_code': ts_code,
            'name': _NAME().get(ts_code, ts_code),
            'category': _CATEGORY().get(ts_code),
            'trade_date': latest_date.isoformat(),
            'close': float(cur.close_price) if cur and cur.close_price is not None else None,
            'pe_ttm': float(cur.pe_ttm) if cur and cur.pe_ttm is not None else None,
            'pb': float(cur.pb) if cur and cur.pb is not None else None,
            'pe_percentile': pe_pct,
            'pb_percentile': pb_pct,
            'valuation_percentile': round(valuation_pct, 1) if valuation_pct is not None else None,
            'volatility': round(vol * 100, 1) if vol is not None else None,
            'volatility_is_proxy': vol_is_proxy,
            'percentiles': {p: {'pe': float(r.pe_percentile) if r.pe_percentile is not None else None,
                                'pb': float(r.pb_percentile) if r.pb_percentile is not None else None}
                            for p, r in pct_map.items()},
            'pe_median': round(statistics.median(pe_all), 2) if pe_all else None,
            'pb_median': round(statistics.median(pb_all), 2) if pb_all else None,
            **_momentum(closes),
            'score': score,
            'verdict': verdict,
            'level': level,
            'reasons': reasons,
            'suggested_grid': _suggest_grid(vol),
        }

    def assess_all(self) -> List[Dict]:
        """评估监控池内所有指数，按就绪度评分从高到低排序。

        无估值数据的指数（新加入、历史回填中/失败）返回占位行而不是消失，
        让监控池管理的结果在雷达表中立即可见。
        """
        result = []
        for idx in _watchlist.list_indices():
            r = self.assess(idx['ts_code'])
            if r is None:
                r = self._no_data_stub(idx)
            result.append(r)
        result.sort(key=lambda x: x['score'], reverse=True)
        return result

    @staticmethod
    def _no_data_stub(idx: Dict) -> Dict:
        """无数据占位行：score=-1 排在最后，前端展示为「数据回填中」"""
        return {
            'ts_code': idx['ts_code'], 'name': idx['name'],
            'category': idx.get('category'),
            'trade_date': None, 'close': None, 'pe_ttm': None, 'pb': None,
            'pe_percentile': None, 'pb_percentile': None,
            'valuation_percentile': None, 'volatility': None,
            'volatility_is_proxy': False, 'percentiles': {},
            'pe_median': None, 'pb_median': None,
            'ret_20d': None, 'ret_60d': None, 'ret_120d': None,
            'dist_52w_high': None,
            'score': -1, 'verdict': '数据回填中', 'level': 'unknown',
            'reasons': ['历史估值数据回填中，完成后自动出现评分'],
            'suggested_grid': None,
        }
