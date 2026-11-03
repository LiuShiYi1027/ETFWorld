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
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from backend.config.settings import BASE_DIR
from backend.services.tushare_init import get_pro

logger = logging.getLogger(__name__)

CACHE_FILE = BASE_DIR / 'data' / 'etf_basic.json'
_ADJ_PAGE_SIZE = 5000

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
        self._amount_cache_day: Optional[str] = None
        self._amount_cache_at: Optional[datetime] = None
        # (代码, 起始日, 截止日) → 因子；按自然日失效，refresh 也会清空。
        self._adj_cache: Dict[tuple, Dict[str, float]] = {}
        self._adj_cache_day: Dict[tuple, str] = {}

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
        self._amount_cache_day = None
        self._amount_cache_at = None
        self._adj_cache.clear()
        self._adj_cache_day.clear()
        return len(self._load_basic(force=True))

    def _latest_amounts(self) -> Dict[str, Dict]:
        """一次取全市场最新成交额（千元）与收盘价，用于流动性排序。

        进程内缓存最多 10 分钟并按自然日失效：收盘后发布的行情能及时刷新；
        休市期取不到数据时不缓存空结果，避免之后开市仍一直返回空表。
        """
        today = datetime.now().strftime('%Y-%m-%d')
        cache_fresh = (self._amount_cache_at is not None
                       and datetime.now() - self._amount_cache_at < timedelta(minutes=10))
        if self._amount_cache is not None and self._amount_cache_day == today and cache_fresh:
            return self._amount_cache
        pro = get_pro()
        if pro is None:
            return {}
        # 从最近交易日往前找有数据的一天（覆盖春节/国庆等长休市）
        for back in range(0, 15):
            d = (datetime.now() - timedelta(days=back)).strftime('%Y%m%d')
            df = pro.fund_daily(trade_date=d)
            if df is not None and not df.empty:
                self._amount_cache = {
                    r['ts_code']: {'amount': float(r['amount']) if r.get('amount') == r.get('amount') else 0,
                                   'close': float(r['close']) if r.get('close') == r.get('close') else None,
                                   'trade_date': str(r['trade_date'])}
                    for _, r in df.iterrows()
                }
                self._amount_cache_day = today
                self._amount_cache_at = datetime.now()
                return self._amount_cache
        return {}

    @staticmethod
    def _canonical_benchmark(value: str) -> str:
        value = re.sub(r'\s+', '', _clean_benchmark(value or ''))
        value = re.sub(r'[×xX]\s*\d+(?:\.\d+)?%.*$', '', value)
        return value.removesuffix('指数')

    @classmethod
    def _keyword_match(cls, b: Dict, keyword: str, aliases: List[str],
                       code_query: Optional[str]) -> Optional[Tuple[str, float]]:
        code = b.get('ts_code') or ''
        if code_query and code.split('.')[0].startswith(code_query):
            exact = len(code_query) == 6 and code.split('.')[0] == code_query
            return ('exact_code', 1.0) if exact else ('code_prefix', 0.9)

        name = b.get('name') or ''
        benchmark = _clean_benchmark(b.get('benchmark'))
        canonical_keyword = cls._canonical_benchmark(keyword)
        canonical_benchmark = cls._canonical_benchmark(benchmark)
        if canonical_keyword and canonical_keyword == canonical_benchmark:
            return 'exact_benchmark', 0.98
        if keyword and keyword == name:
            return 'exact_name', 0.95
        if keyword and keyword in benchmark:
            return 'benchmark_match', 0.8
        if keyword and keyword in name:
            return 'name_match', 0.65
        if any(alias in benchmark for alias in aliases):
            return 'fuzzy_benchmark', 0.45
        if any(alias in name for alias in aliases):
            return 'fuzzy_name', 0.35
        return None

    def search(self, keyword: str, limit: int = 20) -> List[Dict]:
        """按代码/名称/基准搜索，并明确区分精确跟踪与模糊行业召回。"""
        kw = _clean_industry_name(keyword)
        if not kw:
            return []
        aliases = ALIASES.get(kw, [])
        m = re.fullmatch(r'(\d{1,6})(?:\.(?:SH|SZ))?', kw, re.IGNORECASE)
        kw_code = m.group(1) if m else None
        matched = []
        for b in self._load_basic():
            match = self._keyword_match(b, kw, aliases, kw_code)
            if match:
                matched.append((b, *match))
        amounts = self._latest_amounts()

        result = []
        for b, match_type, confidence in matched:
            a = amounts.get(b['ts_code'], {})
            amt = a.get('amount', 0) or 0
            result.append({
                **b,
                'close': a.get('close'),
                'amount_wan': round(amt / 10, 1),      # 千元→万元
                'amount_yi': round(amt / 100000, 3),   # 千元→亿元
                'trade_date': a.get('trade_date'),
                'match_type': match_type,
                'match_confidence': confidence,
                'is_exact_tracking': match_type == 'exact_benchmark',
            })
        result.sort(key=lambda x: x['amount_wan'] or 0, reverse=True)
        return result[:limit]

    def find_for_index(self, ts_code: str) -> Dict:
        """为监控指数查找 ETF；模糊行业召回不得标记为已验证跟踪关系。"""
        from backend.services.watchlist_service import WatchlistService
        name = WatchlistService().name_map().get(ts_code, ts_code)
        keyword = _clean_industry_name(name)
        etfs = self.search(keyword)
        best = max(etfs, key=lambda x: x['match_confidence'], default=None)
        verified = any(x['is_exact_tracking'] for x in etfs)
        return {
            'index_code': ts_code,
            'index_name': name,
            'keyword': keyword,
            'etfs': etfs,
            'match_type': best['match_type'] if best else 'unmatched',
            'match_confidence': best['match_confidence'] if best else 0.0,
            'mapping_status': 'verified' if verified else ('fuzzy' if etfs else 'unmatched'),
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

    def daily_raw_bars(self, symbol: str, days: int = 250) -> List[tuple]:
        """取未复权日线，供按当时实际成交价复盘执行纪律。"""
        code = self._resolve_ts_code(symbol)
        return self._daily_raw_rows(code, days) if code else []

    def _daily_raw_rows(self, code: str, days: int) -> List[tuple]:
        pro = get_pro()
        if pro is None:
            return []
        # 交易日 ≈ 日历日 × 0.7，留足缓冲再裁尾。
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

    def daily_bars_with_status(self, symbol: str, days: int = 250) -> Dict:
        """前复权日线及数据状态；因子不可用时绝不返回未标记的原始价。"""
        code = self._resolve_ts_code(symbol)
        rows = self._daily_raw_rows(code, days) if code else []
        if not rows:
            return {'bars': [], 'adjustment_status': 'no_data',
                    'adjustment_message': '日线数据不足'}
        try:
            factors = self._adj_factors(code, rows[0][0], rows[-1][0])
            bars = self._apply_adj(rows, factors)
        except ValueError as e:
            return {'bars': [], 'adjustment_status': 'factor_unavailable',
                    'adjustment_message': str(e)}
        return {'bars': bars, 'adjustment_status': 'adjusted',
                'adjustment_message': None}

    def daily_bars(self, symbol: str, days: int = 250) -> List[tuple]:
        """取前复权日线；因子缺失或覆盖不足时安全报错，不降级冒充复权。"""
        result = self.daily_bars_with_status(symbol, days)
        if result['adjustment_status'] == 'factor_unavailable':
            raise ValueError(result['adjustment_message'])
        return result['bars']

    def _adj_factors(self, ts_code: str, start_date: Optional[str] = None,
                     end_date: Optional[str] = None) -> Dict[str, float]:
        """分页取完整日期范围内的复权因子，按范围和自然日缓存。"""
        key = (ts_code, start_date or '', end_date or '')
        today = datetime.now().strftime('%Y-%m-%d')
        if key in self._adj_cache and self._adj_cache_day.get(key) == today:
            return self._adj_cache[key]
        pro = get_pro()
        if pro is None:
            raise ValueError('复权因子数据源不可用，无法生成前复权价格')

        factors: Dict[str, float] = {}
        offset = 0
        previous_page = None
        try:
            while True:
                params = {'ts_code': ts_code, 'limit': _ADJ_PAGE_SIZE, 'offset': offset}
                if start_date:
                    params['start_date'] = start_date
                if end_date:
                    params['end_date'] = end_date
                df = pro.fund_adj(**params)
                if df is None or df.empty:
                    break
                page = [(str(r['trade_date']), float(r['adj_factor']))
                        for _, r in df.iterrows()
                        if r.get('adj_factor') == r.get('adj_factor')
                        and r.get('adj_factor') and float(r['adj_factor']) > 0]
                signature = tuple(page)
                if signature and signature == previous_page:
                    raise ValueError('复权因子分页重复，无法确认完整覆盖')
                factors.update(page)
                if len(df) < _ADJ_PAGE_SIZE:
                    break
                previous_page = signature
                offset += len(df)
        except ValueError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning('fund_adj 失败 %s: %s', ts_code, e)
            raise ValueError(f'复权因子获取失败: {e}') from e

        self._adj_cache[key] = factors
        self._adj_cache_day[key] = today
        return factors

    @staticmethod
    def _apply_adj(rows: List[tuple], factors: Optional[Dict[str, float]]) -> List[tuple]:
        """前复权：close × 当日因子 ÷ 末日因子；首尾覆盖不足即报错。"""
        if not rows:
            return []
        if not factors:
            raise ValueError('复权因子缺失，拒绝以未复权价格冒充前复权结果')
        if any(f <= 0 for f in factors.values()):
            raise ValueError('复权因子存在非正值，无法生成前复权价格')
        first_day, last_day = rows[0][0], rows[-1][0]
        fdates = sorted(factors)
        if fdates[0] > first_day or fdates[-1] < last_day:
            raise ValueError(
                f'复权因子覆盖不足: 日线 {first_day}~{last_day}，'
                f'因子 {fdates[0]}~{fdates[-1]}')
        from bisect import bisect_right

        def f_at(d):
            i = bisect_right(fdates, d) - 1
            if i < 0:
                raise ValueError(f'{d} 之前无可用复权因子')
            return factors[fdates[i]]

        latest = f_at(last_day)
        return [(d, round(c * f_at(d) / latest, 4)) for d, c in rows]

    def corporate_action_check(self, symbol: str, since_date: str) -> Dict:
        """检查计划创建日后的因子变化；数据不足时保守要求暂停旧网格。"""
        compact = str(since_date or '')[:10].replace('-', '')
        try:
            since = datetime.strptime(compact, '%Y%m%d').strftime('%Y%m%d')
        except ValueError as e:
            raise ValueError('计划创建日期格式无效') from e
        end = datetime.now().strftime('%Y%m%d')
        code = self._resolve_ts_code(symbol)
        base = {'ts_code': code, 'since_date': since, 'as_of_date': end,
                'changes': [], 'latest_factor': None, 'message': None}
        try:
            if not code:
                raise ValueError('无法识别 ETF 代码，公司行动状态未知')
            factors = self._adj_factors(code, end_date=end)
            dates = sorted(factors)
            if not dates:
                raise ValueError('复权因子为空，公司行动状态未知')
            baseline_dates = [d for d in dates if d <= since]
            if not baseline_dates:
                raise ValueError('缺少计划创建日基准复权因子，公司行动状态未知')
            previous = factors[baseline_dates[-1]]
            changes = []
            for d in dates:
                if d <= since:
                    continue
                current = factors[d]
                if current != previous:
                    changes.append({'trade_date': d, 'old_factor': previous,
                                    'new_factor': current})
                previous = current
            return {**base, 'status': 'ok', 'has_changed': bool(changes),
                    'pause_required': bool(changes), 'changes': changes,
                    'latest_factor': factors[dates[-1]]}
        except ValueError as e:
            return {**base, 'status': 'unavailable', 'has_changed': None,
                    'pause_required': True, 'message': str(e)}

    def daily_closes(self, symbol: str, days: int = 250) -> List[float]:
        """取前复权收盘价；因子缺失或覆盖不足时显式报错。"""
        return [c for _, c in self.daily_bars(symbol, days)]

    def quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        """批量取最新收盘价，并提供今日执行侧所需的陈旧行情判断。"""
        amounts = self._latest_amounts()
        by_prefix = {}
        for code, info in amounts.items():
            by_prefix[code.split('.')[0]] = (code, info)
        as_of = max((str(info.get('trade_date') or '') for info in amounts.values()),
                    default='') or None
        out = {}
        for s in symbols:
            key = str(s).split('.')[0]
            hit = by_prefix.get(key)
            code, info = hit if hit else (None, {})
            trade_date = info.get('trade_date')
            if not trade_date or not as_of:
                stale, reason = True, 'missing_trade_date'
            else:
                stale = str(trade_date) != as_of
                reason = 'not_latest_market_date' if stale else None
            out[s] = {'ts_code': code, 'close': info.get('close'),
                      'trade_date': trade_date, 'stale': stale,
                      'stale_reason': reason, 'as_of_date': as_of}
        return out
