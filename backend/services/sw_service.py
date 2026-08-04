"""
申万行业浏览/搜索服务

提供 511 个申万行业（L1/L2/L3）的检索与实时估值查询，
方便挑选网格标的。分类表缓存到本地 JSON，避免重复请求。
"""
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from backend.config.settings import BASE_DIR
from backend.services.tushare_init import get_pro

logger = logging.getLogger(__name__)

CACHE_FILE = BASE_DIR / 'data' / 'sw_classify.json'


class SWService:
    """申万行业分类与估值"""

    def __init__(self):
        self._classify: Optional[List[Dict]] = None

    # ---------- 分类表 ----------

    def _load_classify(self, force: bool = False) -> List[Dict]:
        """加载申万 L1/L2/L3 分类表（优先读本地缓存）"""
        if self._classify is not None and not force:
            return self._classify

        if CACHE_FILE.exists() and not force:
            self._classify = json.loads(CACHE_FILE.read_text(encoding='utf-8'))
            return self._classify

        pro = get_pro()
        if pro is None:
            return []

        records = []
        for level in ('L1', 'L2', 'L3'):
            df = pro.index_classify(level=level, src='SW2021')
            if df is None or df.empty:
                continue
            for _, r in df.iterrows():
                records.append({
                    'ts_code': r['index_code'],
                    'name': r['industry_name'],
                    'level': level,
                    'parent_code': r.get('parent_code'),
                })

        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=1),
                              encoding='utf-8')
        self._classify = records
        logger.info("申万分类表已缓存: %d 个行业", len(records))
        return records

    def refresh(self) -> int:
        """强制刷新分类表缓存"""
        return len(self._load_classify(force=True))

    def list_industries(self, level: str = None) -> List[Dict]:
        """列出行业，可按层级(L1/L2/L3)过滤"""
        data = self._load_classify()
        if level:
            return [d for d in data if d['level'] == level.upper()]
        return data

    def search(self, keyword: str) -> List[Dict]:
        """按名称关键词搜索行业"""
        data = self._load_classify()
        kw = keyword.strip()
        return [d for d in data if kw in d['name']]

    # ---------- 估值 ----------

    def get_valuation(self, ts_code: str) -> Optional[Dict]:
        """查询单个申万行业的最新估值"""
        pro = get_pro()
        if pro is None:
            return None
        df = pro.sw_daily(ts_code=ts_code)
        if df is None or df.empty:
            return None
        # 取最新一天
        df = df.sort_values('trade_date')
        r = df.iloc[-1]
        return {
            'ts_code': ts_code,
            'name': r.get('name'),
            'trade_date': str(r.get('trade_date')),
            'close': float(r['close']) if r.get('close') is not None else None,
            'pe': float(r['pe']) if r.get('pe') is not None else None,
            'pb': float(r['pb']) if r.get('pb') is not None else None,
        }

    def search_with_valuation(self, keyword: str) -> List[Dict]:
        """搜索行业并附带最新估值"""
        result = []
        for item in self.search(keyword):
            val = self.get_valuation(item['ts_code'])
            result.append({**item, **(val or {})})
        return result
