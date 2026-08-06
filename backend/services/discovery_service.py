"""
品种发现服务：数据筛（低估 / 波动 / 流动性）+ 给 AI 的候选包

在申万一级+二级全部行业指数上做全池扫描，回答"现在哪些品种值得研究网格"：
- 低估：5 年综合分位（PE/PB 分位均值，与雷达口径一致）< 50
- 波动充足：近 250 日年化波动率（决定格距大小）
- 可交易：能关联到成交额达标的 ETF

评分与雷达同公式（估值 70 + 波动 30）。扫描耗时较长（每只一次历史调用），
在后台线程跑，进度与结果读模块级状态；历史数据复用估值库，不重复拉取。
"""
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

from backend.services.readiness_service import _annualized_volatility
from backend.services.tushare_init import get_pro

logger = logging.getLogger(__name__)

# 过滤阈值
MAX_PCT = 50.0          # 综合分位上限（雷达否决线）
MIN_VOL = 0.12          # 年化波动下限（低于此值网格跑不起来）
MIN_ETF_AMOUNT_QY = 100000.0  # ETF 成交额下限（千元；= 1 亿）

# 扫描状态（模块级，单实例本地工具足够）
_state: Dict = {'running': False, 'total': 0, 'done': 0, 'current': '',
                'result': None, 'error': None, 'finished_at': None}


def _percentile(history: List[float], current: float) -> Optional[float]:
    """当前值在全历史中的分位（≤当前值的样本占比 × 100）"""
    series = [v for v in history if v and v > 0]
    if len(series) < 30 or current is None or current <= 0:
        return None
    return round(sum(1 for v in series if v <= current) / len(series) * 100, 1)


def _score(pct: Optional[float], vol: Optional[float]) -> float:
    """与雷达同公式：估值安全垫 70 + 波动充足度 30"""
    val_score = (100 - pct) / 100 * 70 if pct is not None else 0
    vol_score = min(vol / 0.40, 1.0) * 30 if vol is not None else 0
    return round(val_score + vol_score, 1)


def scan_state() -> Dict:
    return {k: v for k, v in _state.items()}


def run_scan(sw_service, etf_service,
             fetch_history: Optional[Callable] = None,
             years: int = 5, max_pct: float = MAX_PCT,
             min_vol: float = MIN_VOL,
             min_amount_qy: float = MIN_ETF_AMOUNT_QY) -> List[Dict]:
    """
    全池扫描申万行业指数，返回通过的候选（按评分降序）。
    fetch_history: (ts_code) -> [{'close':..,'pe':..,'pb':..}, ...] 升序，测试可注入
    """
    if _state['running']:
        return (_state['result'] or {}).get('items') or []

    _state.update({'running': True, 'done': 0, 'error': None,
                   'result': None, 'finished_at': None})
    try:
        pool = [d for d in sw_service.list_industries() if d['level'] in ('L1', 'L2')]
        _state['total'] = len(pool)
        if fetch_history is None:
            fetch_history = _default_fetch_history(years)
        try:
            amounts = etf_service._latest_amounts()  # {ts_code:{amount(千元),close}}
        except Exception:  # noqa: BLE001
            amounts = {}

        items = []
        for i, idx in enumerate(pool):
            _state.update({'done': i + 1, 'current': idx['name']})
            try:
                item = _evaluate(idx, fetch_history, etf_service, amounts,
                                 max_pct, min_vol, min_amount_qy)
                if item:
                    items.append(item)
            except Exception as e:  # noqa: BLE001 — 单只失败不阻塞全池
                logger.debug('寻品评估跳过 %s: %s', idx['ts_code'], e)
            time.sleep(0.05)  # 轻限速，照顾数据通道

        items.sort(key=lambda x: x['score'], reverse=True)
        _state['result'] = {
            'items': items,
            'scanned': len(pool),
            'passed': len(items),
            'filters': {'max_pct': max_pct, 'min_vol': min_vol,
                        'min_amount_qy': min_amount_qy},
            'finished_at': datetime.now().isoformat(timespec='seconds'),
        }
        logger.info('品种发现完成: 扫描 %d 只，通过 %d 只', len(pool), len(items))
        return items
    except Exception as e:  # noqa: BLE001
        _state['error'] = str(e)
        logger.exception('品种发现扫描失败')
        return []
    finally:
        _state['running'] = False
        _state['finished_at'] = datetime.now().isoformat(timespec='seconds')


def _default_fetch_history(years: int) -> Callable:
    """默认历史拉取：sw_daily 按 ts_code + 起止日期一次取回（升序）"""
    def fetch(ts_code: str) -> List[Dict]:
        pro = get_pro()
        if pro is None:
            return []
        start = (datetime.now() - timedelta(days=int(years * 365.25))).strftime('%Y%m%d')
        df = pro.sw_daily(ts_code=ts_code, start_date=start)
        if df is None or df.empty:
            return []
        df = df.sort_values('trade_date')
        return [{'close': r.get('close'), 'pe': r.get('pe'), 'pb': r.get('pb')}
                for _, r in df.iterrows()]
    return fetch


def _evaluate(idx: Dict, fetch_history: Callable, etf_service,
              amounts: Dict, max_pct: float, min_vol: float,
              min_amount_qy: float) -> Optional[Dict]:
    """评估单只行业指数，不满足过滤条件返回 None"""
    hist = fetch_history(idx['ts_code'])
    if len(hist) < 250:
        return None
    closes = [float(h['close']) for h in hist if h.get('close')]
    vol = _annualized_volatility(closes[-250:])
    if vol is None or vol < min_vol:
        return None

    latest = hist[-1]
    pe = float(latest['pe']) if latest.get('pe') else None
    pb = float(latest['pb']) if latest.get('pb') else None
    pe_pct = _percentile([float(h['pe']) for h in hist if h.get('pe')], pe) if pe else None
    pb_pct = _percentile([float(h['pb']) for h in hist if h.get('pb')], pb) if pb else None
    avail = [p for p in (pe_pct, pb_pct) if p is not None]
    if not avail:
        return None
    pct = round(sum(avail) / len(avail), 1)
    if pct > max_pct:
        return None

    etf = _match_etf(idx['name'], etf_service, amounts, min_amount_qy)
    if etf is None:
        return None

    return {
        'ts_code': idx['ts_code'], 'name': idx['name'], 'level': idx['level'],
        'close': float(latest['close']) if latest.get('close') else None,
        'pe': pe, 'pb': pb, 'valuation_percentile': pct,
        'volatility': round(vol * 100, 1),
        'score': _score(pct, vol),
        'etf': etf,
    }


def _match_etf(industry_name: str, etf_service, amounts: Dict,
               min_amount_qy: float) -> Optional[Dict]:
    """按行业名找 ETF，要求最新成交额达标。返回最优的一只。"""
    keyword = industry_name.rstrip('ⅠⅡⅢ')
    try:
        cands = etf_service.search(keyword)
    except Exception:  # noqa: BLE001
        return None
    best = None
    for c in cands or []:
        code = c.get('ts_code') or c.get('symbol') or ''
        amt = (amounts.get(code) or {}).get('amount') or 0
        if amt >= min_amount_qy and (best is None or amt > best['amount_qy']):
            best = {'ts_code': code, 'name': c.get('name'),
                    'amount_qy': amt, 'amount_yi': round(amt / 100000, 2)}
    return best
