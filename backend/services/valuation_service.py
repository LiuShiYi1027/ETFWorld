"""
估值数据服务：更新/回填Tushare估值数据，计算分位点
"""
import logging
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict

from sqlalchemy import func

from backend.config.settings import SUPPORTED_INDICES, PERCENTILE_PERIODS, VALUATION_ZONES
from backend.models.database import (
    ValuationTable, ValuationPercentileTable, IndexInfoTable, UpdateLogTable
)
from backend.utils.db import get_session
from backend.services.tushare_client import TushareClient
from backend.services.watchlist_service import WatchlistService

logger = logging.getLogger(__name__)

_watchlist = WatchlistService()


def _parse_date(s: str) -> date:
    return datetime.strptime(s, '%Y%m%d').date()


def _pool() -> List[Dict]:
    """监控池清单（入库可增删；SUPPORTED_INDICES 仅为默认播种来源）"""
    return _watchlist.list_indices()


class ValuationService:
    """估值数据服务"""

    def __init__(self, client: TushareClient = None):
        self.client = client or TushareClient()

    def init_index_info(self):
        """初始化指数基础信息"""
        with get_session() as session:
            for idx in _pool():
                existing = session.query(IndexInfoTable).filter_by(ts_code=idx['ts_code']).first()
                if not existing:
                    session.add(IndexInfoTable(
                        ts_code=idx['ts_code'],
                        name=idx['name'],
                        market=idx['ts_code'].split('.')[-1],
                        category=idx['category'],
                    ))
            logger.info("指数基础信息初始化完成")

    def _fetch(self, idx: Dict, trade_date: str = None,
               start_date: str = None, end_date: str = None):
        """按 source 路由拉取数据，返回归一化记录列表

        宽基(index_dailybasic): 有 pe / pe_ttm / pb / turnover_rate
        申万行业(sw_daily): 只有 pe / pb / close，pe 映射到 pe_ttm
        """
        source = idx.get('source', 'index')
        if source == 'sw':
            df = self.client.get_sw_daily(
                ts_code=idx['ts_code'], trade_date=trade_date,
                start_date=start_date, end_date=end_date)
        else:
            df = self.client.get_index_dailybasic(
                ts_code=idx['ts_code'], trade_date=trade_date,
                start_date=start_date, end_date=end_date)

        if df is None or df.empty:
            return []

        records = []
        for _, row in df.iterrows():
            if source == 'sw':
                rec = dict(
                    ts_code=row['ts_code'],
                    trade_date=str(row['trade_date']),
                    close_price=row.get('close'),
                    total_mv=row.get('total_mv'),
                    float_mv=row.get('float_mv'),
                    turnover_rate=None,
                    pe=row.get('pe'),
                    pe_ttm=row.get('pe'),   # sw 无 pe_ttm，用 pe 代替
                    pb=row.get('pb'),
                )
            else:
                rec = dict(
                    ts_code=row['ts_code'],
                    trade_date=str(row['trade_date']),
                    close_price=None,
                    total_mv=row.get('total_mv'),
                    float_mv=row.get('float_mv'),
                    turnover_rate=row.get('turnover_rate'),
                    pe=row.get('pe'),
                    pe_ttm=row.get('pe_ttm'),
                    pb=row.get('pb'),
                )
            records.append(rec)
        return records

    def _save_rows(self, session, records) -> int:
        """将归一化记录写入估值表，按(ts_code, trade_date)去重"""
        count = 0
        for rec in records:
            trade_date = _parse_date(rec['trade_date'])
            existing = session.query(ValuationTable).filter_by(
                ts_code=rec['ts_code'], trade_date=trade_date).first()
            if existing:
                continue
            session.add(ValuationTable(
                ts_code=rec['ts_code'],
                trade_date=trade_date,
                close_price=rec.get('close_price'),
                total_mv=rec.get('total_mv'),
                float_mv=rec.get('float_mv'),
                turnover_rate=rec.get('turnover_rate'),
                pe=rec.get('pe'),
                pe_ttm=rec.get('pe_ttm'),
                pb=rec.get('pb'),
                data_source='tushare',
            ))
            count += 1
        return count

    def update_latest(self, trade_date: str = None) -> Dict:
        """更新最新一天的估值数据"""
        if not self.client.is_connected():
            return {'status': 'failed', 'error': 'Tushare Token未配置'}

        prefetched = None
        if not trade_date:
            # 交易日历只说明当天开市，不代表日终估值已经发布。
            # 从最近候选日向前探测，选第一天真正有数据的日期。
            probe_index = _pool()[0]
            for candidate in self.client.get_recent_trade_dates(limit=5):
                records = self._fetch(probe_index, trade_date=candidate)
                if records:
                    trade_date, prefetched = candidate, records
                    break
        if not trade_date:
            return {'status': 'failed', 'error': '最近交易日尚未发布估值数据，请稍后重试'}

        success, failed = 0, 0
        with get_session() as session:
            for position, idx in enumerate(_pool()):
                records = prefetched if position == 0 and prefetched is not None else self._fetch(idx, trade_date=trade_date)
                if records:
                    success += self._save_rows(session, records)
                else:
                    failed += 1
            session.add(UpdateLogTable(
                trade_date=_parse_date(trade_date),
                status='success' if failed == 0 else ('partial' if success else 'failed'),
                indices_count=len(_pool()),
                success_count=success,
                failed_count=failed,
            ))

        # 更新当日分位点
        self.calc_percentiles(trade_date=_parse_date(trade_date))
        return {'status': 'success', 'trade_date': trade_date,
                'saved': success, 'failed': failed}

    def backfill(self, start_date: str, end_date: str) -> Dict:
        """回填历史估值数据"""
        if not self.client.is_connected():
            return {'status': 'failed', 'error': 'Tushare Token未配置'}

        total = 0
        # 每个指数独立 session，避免同批 autoflush 导致去重误判与计数失真
        for idx in _pool():
            records = self._fetch(idx, start_date=start_date, end_date=end_date)
            with get_session() as session:
                saved = self._save_rows(session, records)
            total += saved
            logger.info("回填 %s(%s): %d 条", idx['ts_code'], idx['name'], saved)
        return {'status': 'success', 'saved': total}

    def backfill_index(self, idx: Dict, years: int = 5) -> Dict:
        """回填单个指数的历史估值（监控池新增时自动调用）"""
        if not self.client.is_connected():
            return {'status': 'failed', 'error': 'Tushare Token未配置'}
        start = (datetime.now() - timedelta(days=int(years * 365.25))).strftime('%Y%m%d')
        records = self._fetch(idx, start_date=start)
        with get_session() as session:
            saved = self._save_rows(session, records)
        self.calc_percentiles(ts_codes=[idx['ts_code']])
        logger.info("单指数回填 %s(%s): %d 条", idx['ts_code'], idx['name'], saved)
        return {'status': 'success', 'saved': saved}

    def calc_percentiles(self, trade_date: date = None, ts_codes: List[str] = None) -> int:
        """计算指定日期各指数PE/PB历史分位点"""
        ts_codes = ts_codes or [i["ts_code"] for i in _pool()]
        count = 0
        with get_session() as session:
            for ts_code in ts_codes:
                target_date = trade_date or session.query(
                    func.max(ValuationTable.trade_date)
                ).filter(ValuationTable.ts_code == ts_code).scalar()
                if not target_date:
                    continue

                current = session.query(ValuationTable).filter_by(
                    ts_code=ts_code, trade_date=target_date).first()
                if not current or current.pe_ttm is None:
                    continue

                for period, years in PERCENTILE_PERIODS.items():
                    q = session.query(ValuationTable.pe_ttm, ValuationTable.pb).filter(
                        ValuationTable.ts_code == ts_code,
                        ValuationTable.trade_date <= target_date,
                    )
                    if years:
                        q = q.filter(ValuationTable.trade_date >=
                                     target_date - timedelta(days=365 * years))
                    rows = q.all()
                    if len(rows) < 30:  # 样本太少不计算
                        continue

                    pes = sorted(float(r[0]) for r in rows if r[0] is not None)
                    pbs = sorted(float(r[1]) for r in rows if r[1] is not None)

                    def pct(sorted_vals, v):
                        if not sorted_vals or v is None:
                            return None
                        below = sum(1 for x in sorted_vals if x <= float(v))
                        return round(below / len(sorted_vals) * 100, 2)

                    existing = session.query(ValuationPercentileTable).filter_by(
                        ts_code=ts_code, trade_date=target_date, period=period).first()
                    values = dict(
                        pe_percentile=pct(pes, current.pe_ttm),
                        pb_percentile=pct(pbs, current.pb),
                        sample_count=len(rows),
                    )
                    if existing:
                        for k, v in values.items():
                            setattr(existing, k, v)
                    else:
                        session.add(ValuationPercentileTable(
                            ts_code=ts_code, trade_date=target_date,
                            period=period, **values))
                    count += 1
        return count

    @staticmethod
    def zone_of(percentile: Optional[float]) -> Optional[Dict]:
        """根据PE分位点返回估值区间"""
        if percentile is None:
            return None
        for low, high, label, color in VALUATION_ZONES:
            if low <= percentile < high or (high == 100 and percentile == 100):
                return {'label': label, 'color': color}
        return None

    def get_overview(self) -> List[Dict]:
        """各指数最新估值概览（含分位点与估值区间）"""
        result = []
        with get_session() as session:
            for idx in _pool():
                ts_code = idx['ts_code']
                latest = session.query(ValuationTable).filter_by(ts_code=ts_code) \
                    .order_by(ValuationTable.trade_date.desc()).first()
                item = {
                    'ts_code': ts_code,
                    'name': idx['name'],
                    'category': idx['category'],
                    'trade_date': None, 'pe_ttm': None, 'pb': None,
                    'percentiles': {}, 'zone': None,
                }
                if latest:
                    item.update(
                        trade_date=latest.trade_date.isoformat(),
                        pe_ttm=float(latest.pe_ttm) if latest.pe_ttm is not None else None,
                        pb=float(latest.pb) if latest.pb is not None else None,
                    )
                    pcts = session.query(ValuationPercentileTable).filter_by(
                        ts_code=ts_code, trade_date=latest.trade_date).all()
                    for p in pcts:
                        item['percentiles'][p.period] = {
                            'pe': float(p.pe_percentile) if p.pe_percentile is not None else None,
                            'pb': float(p.pb_percentile) if p.pb_percentile is not None else None,
                            'samples': p.sample_count,
                        }
                    ref = item['percentiles'].get('5y') or item['percentiles'].get('all')
                    if ref:
                        item['zone'] = self.zone_of(ref.get('pe'))
                result.append(item)
        return result

    def get_history(self, ts_code: str, limit: int = 500) -> List[Dict]:
        """获取某指数估值历史走势"""
        with get_session() as session:
            rows = session.query(ValuationTable).filter_by(ts_code=ts_code) \
                .order_by(ValuationTable.trade_date.desc()).limit(limit).all()
            return [{
                'trade_date': r.trade_date.isoformat(),
                'pe_ttm': float(r.pe_ttm) if r.pe_ttm is not None else None,
                'pb': float(r.pb) if r.pb is not None else None,
            } for r in reversed(rows)]
