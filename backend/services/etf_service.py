"""
ETF 标的查找服务

指数不能直接交易，需要找到跟踪它的 ETF。难点：申万行业指数基本没有
直接跟踪的 ETF，市面行业 ETF 多跟踪中证/国证行业指数，所以按"行业主题
名称"模糊匹配（匹配基金名称或跟踪基准），并按成交额（流动性）排序，
避免推荐没人交易的迷你基金。
"""
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from backend.config.settings import BASE_DIR
from backend.services.tushare_init import get_pro

logger = logging.getLogger(__name__)

CACHE_FILE = BASE_DIR / 'data' / 'etf_basic.json'

# 行业名 → 额外同义关键词（提升召回，ETF命名常与申万行业名不一致）
ALIASES = {
    '证券': ['券商', '证券公司'],
    '白酒': ['酒'],
    '非银金融': ['证券', '保险', '券商'],
    '食品饮料': ['食品', '饮料'],
    '医药生物': ['医药'],
    '国防军工': ['军工', '国防'],
    '计算机': ['软件', '云计算'],
    '家用电器': ['家电'],
}

# 匹配 benchmark 时需剔除的高频干扰词（否则"证券"会命中"上海证券交易所"）
_BENCH_NOISE = ['上海证券交易所', '深圳证券交易所', '证券交易所', '交易所']


def _clean_industry_name(name: str) -> str:
    """清洗指数名作为搜索关键词：去掉罗马数字后缀、(深)等"""
    name = re.sub(r'[ⅠⅡⅢⅣⅤ]', '', name)
    name = re.sub(r'\(.*?\)', '', name)
    return name.strip()


def _clean_benchmark(bench: Optional[str]) -> str:
    """清洗基准文本，去掉会造成误匹配的干扰词"""
    if not bench:
        return ''
    for noise in _BENCH_NOISE:
        bench = bench.replace(noise, '')
    return bench


class ETFService:
    """ETF 标的查找"""

    def __init__(self):
        self._basic: Optional[List[Dict]] = None
        self._amount_cache: Optional[Dict[str, Dict]] = None

    def _load_basic(self, force: bool = False) -> List[Dict]:
        """加载全市场 ETF 基础信息（缓存到本地）"""
        if self._basic is not None and not force:
            return self._basic
        if CACHE_FILE.exists() and not force:
            self._basic = json.loads(CACHE_FILE.read_text(encoding='utf-8'))
            return self._basic

        pro = get_pro()
        if pro is None:
            return []
        df = pro.fund_basic(market='E')
        if df is None or df.empty:
            return []

        records = []
        for _, r in df.iterrows():
            # 只保留正在上市交易的
            if r.get('status') and r.get('status') != 'L':
                continue
            records.append({
                'ts_code': r['ts_code'],
                'name': r.get('name'),
                'benchmark': r.get('benchmark'),
                'management': r.get('management'),
                'list_date': r.get('list_date'),
                'm_fee': float(r['m_fee']) if r.get('m_fee') == r.get('m_fee') and r.get('m_fee') is not None else None,
                'c_fee': float(r['c_fee']) if r.get('c_fee') == r.get('c_fee') and r.get('c_fee') is not None else None,
            })
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=1),
                              encoding='utf-8')
        self._basic = records
        logger.info("ETF基础信息已缓存: %d 只", len(records))
        return records

    def refresh(self) -> int:
        self._amount_cache = None
        return len(self._load_basic(force=True))

    def _latest_amounts(self) -> Dict[str, Dict]:
        """一次取全市场最新成交额（千元）与收盘价，用于流动性排序"""
        if self._amount_cache is not None:
            return self._amount_cache
        pro = get_pro()
        if pro is None:
            return {}
        # 从最近交易日往前找有数据的一天
        for back in range(0, 8):
            d = (datetime.now() - timedelta(days=back)).strftime('%Y%m%d')
            df = pro.fund_daily(trade_date=d)
            if df is not None and not df.empty:
                self._amount_cache = {
                    r['ts_code']: {'amount': float(r['amount']) if r.get('amount') == r.get('amount') else 0,
                                   'close': float(r['close']) if r.get('close') == r.get('close') else None,
                                   'trade_date': str(r['trade_date'])}
                    for _, r in df.iterrows()
                }
                return self._amount_cache
        self._amount_cache = {}
        return self._amount_cache

    def search(self, keyword: str, limit: int = 20) -> List[Dict]:
        """按关键词搜索 ETF（匹配名称或跟踪基准），按成交额降序"""
        kw = _clean_industry_name(keyword)
        if not kw:
            return []
        keywords = [kw] + ALIASES.get(kw, [])
        basic = self._load_basic()
        matched = []
        for b in basic:
            name = b['name'] or ''
            bench = _clean_benchmark(b['benchmark'])
            if any(k in name or k in bench for k in keywords):
                matched.append(b)
        amounts = self._latest_amounts()

        result = []
        for b in matched:
            a = amounts.get(b['ts_code'], {})
            amt = a.get('amount', 0) or 0
            result.append({
                **b,
                'close': a.get('close'),
                'amount_wan': round(amt / 10, 1),      # 千元→万元
                'amount_yi': round(amt / 100000, 3),   # 千元→亿元
                'trade_date': a.get('trade_date'),
            })
        result.sort(key=lambda x: x['amount_wan'] or 0, reverse=True)
        return result[:limit]

    def find_for_index(self, ts_code: str) -> Dict:
        """为某个监控指数查找可交易 ETF"""
        from backend.services.watchlist_service import WatchlistService
        name = WatchlistService().name_map().get(ts_code, ts_code)
        keyword = _clean_industry_name(name)
        return {
            'index_code': ts_code,
            'index_name': name,
            'keyword': keyword,
            'etfs': self.search(keyword),
        }

    def _resolve_ts_code(self, symbol: str) -> Optional[str]:
        """把 512880 这类裸代码补全成带交易所后缀的 ts_code"""
        s = str(symbol).strip()
        if not s:
            return None
        if '.' in s:
            return s
        for b in self._load_basic():
            if (b.get('ts_code') or '').split('.')[0] == s:
                return b['ts_code']
        return None

    def daily_bars(self, symbol: str, days: int = 250) -> List[tuple]:
        """取某 ETF 近 days 个交易日的 (trade_date, close) 对（日期升序）。无数据返回 []。"""
        code = self._resolve_ts_code(symbol)
        if not code:
            return []
        pro = get_pro()
        if pro is None:
            return []
        # 交易日 ≈ 日历日 × 0.7，留足缓冲再裁尾
        end = datetime.now().strftime('%Y%m%d')
        start = (datetime.now() - timedelta(days=int(days * 1.7) + 15)).strftime('%Y%m%d')
        try:
            df = pro.fund_daily(ts_code=code, start_date=start, end_date=end)
        except Exception as e:  # noqa: BLE001
            logger.warning("fund_daily 失败 %s: %s", code, e)
            return []
        if df is None or df.empty:
            return []
        rows = [(str(r['trade_date']), float(r['close']))
                for _, r in df.iterrows()
                if r.get('close') == r.get('close') and r.get('close')]
        rows.sort(key=lambda x: x[0])
        return rows[-days:]

    def daily_closes(self, symbol: str, days: int = 250) -> List[float]:
        """取某 ETF 近 days 个交易日的收盘价（升序）。无 Tushare 或无数据返回 []。"""
        return [c for _, c in self.daily_bars(symbol, days)]

    def quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        """批量取若干 ETF 的最新收盘价（用于驾驶舱计划健康）。

        计划里 symbol 多为不带交易所后缀的代码（如 512880），fund_daily 的
        ts_code 带后缀（512880.SH），这里按前缀匹配。无 Tushare 时返回 close=None，
        前端可退化用最后一笔成交价。
        """
        amounts = self._latest_amounts()
        by_prefix = {}
        for code, info in amounts.items():
            by_prefix[code.split('.')[0]] = (code, info)
        out = {}
        for s in symbols:
            key = str(s).split('.')[0]
            hit = by_prefix.get(key)
            if hit:
                code, info = hit
                out[s] = {'ts_code': code, 'close': info.get('close'),
                          'trade_date': info.get('trade_date')}
            else:
                out[s] = {'ts_code': None, 'close': None, 'trade_date': None}
        return out
