"""
估值数据服务：更新/回填Tushare估值数据，计算分位点
"""
import logging
import threading
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from backend.config.settings import SUPPORTED_INDICES, PERCENTILE_PERIODS, VALUATION_ZONES
from backend.models.database import (
    ValuationTable, ValuationPercentileTable, IndexInfoTable, UpdateLogTable
)
from backend.utils.db import get_session
from backend.services.tushare_client import TushareClient
from backend.services.watchlist_service import WatchlistService

logger = logging.getLogger(__name__)

_watchlist = WatchlistService()

# 估值写路径进程内互斥：启动自动更新、手动更新、监控池回填可能并发触发，
# SQLite 单文件库必须串行写，避免 check-then-insert 撞唯一约束导致整批回滚。
_WRITE_LOCK = threading.RLock()


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

        close_by_date = {}
        if source != 'sw':
            prices = self.client.get_index_daily(
                ts_code=idx['ts_code'], trade_date=trade_date,
                start_date=start_date, end_date=end_date)
            if prices is not None and not prices.empty:
                close_by_date = {
                    str(price['trade_date']): price.get('close')
                    for _, price in prices.iterrows()
                }

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
                    close_price=close_by_date.get(str(row['trade_date'])),
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
        """将归一化记录写入估值表，按(ts_code, trade_date)幂等去重。

        用 SAVEPOINT 包住单行写入：即便另一个写线程抢先插入同一交易日，
        也只跳过这一行，不会让整批更新因唯一约束失败而回滚。
        """
        count = 0
        for rec in records:
            trade_date = _parse_date(rec['trade_date'])
            try:
                with session.begin_nested():
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
            except IntegrityError:
                # 老库可能已有估值但缺少后来接入的指数收盘价；重复回填时补齐，
                # 不能用“已存在”把新增数据字段永久跳过。
                existing = session.query(ValuationTable).filter_by(
                    ts_code=rec['ts_code'], trade_date=trade_date).first()
                if existing and existing.close_price is None and rec.get('close_price') is not None:
                    existing.close_price = rec['close_price']
                    count += 1
        return count

    def update_latest(self, trade_date: str = None) -> Dict:
        """更新最新一天的估值数据；部分指数延迟发布时按只补缺口。"""
        if not self.client.is_connected():
            return {'status': 'failed', 'error': 'Tushare Token未配置'}
        pool = _pool()
        if not pool:
            return {'status': 'failed', 'error': '监控池为空，请先添加指数'}

        with _WRITE_LOCK:
            prefetched = None
            if not trade_date:
                # 交易日历只说明当天开市，不代表日终估值已经发布。
                # 从最近候选日向前探测，选第一天真正有数据的日期。
                probe_index = pool[0]
                for candidate in self.client.get_recent_trade_dates(limit=5):
                    records = self._fetch(probe_index, trade_date=candidate)
                    if records:
                        trade_date, prefetched = candidate, records
                        break
            if not trade_date:
                return {'status': 'failed', 'error': '最近交易日尚未发布估值数据，请稍后重试'}

            success, failed, gap_filled = 0, 0, 0
            target = _parse_date(trade_date)
            with get_session() as session:
                for position, idx in enumerate(pool):
                    records = prefetched if position == 0 and prefetched is not None else self._fetch(idx, trade_date=trade_date)
                    if records:
                        success += self._save_rows(session, records)
                    else:
                        failed += 1
                session.add(UpdateLogTable(
                    trade_date=target,
                    status='success' if failed == 0 else ('partial' if success else 'failed'),
                    indices_count=len(pool),
                    success_count=success,
                    failed_count=failed,
                ))

                # 缺口回补：以每只指数自己的最新日期为起点，而不是全局 max(trade_date)。
                # 申万与宽基发布节奏不同，单日无数据的指数下次更新要把缺口补回来。
                for idx in pool:
                    latest = session.query(func.max(ValuationTable.trade_date)).filter(
                        ValuationTable.ts_code == idx['ts_code']).scalar()
                    if latest and latest < target:
                        start = (latest + timedelta(days=1)).strftime('%Y%m%d')
                        records = self._fetch(idx, start_date=start, end_date=trade_date)
                        if records:
                            gap_filled += self._save_rows(session, records)

            # 各指数按自己的最新日期重算分位（延迟发布的指数不会被全局日期甩下）
            self.calc_percentiles()
            return {'status': 'success', 'trade_date': trade_date,
                    'saved': success, 'failed': failed, 'gap_filled': gap_filled}

    def backfill(self, start_date: str, end_date: str) -> Dict:
        """回填历史估值数据"""
        if not self.client.is_connected():
            return {'status': 'failed', 'error': 'Tushare Token未配置'}

        with _WRITE_LOCK:
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
        with _WRITE_LOCK:
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
                if not current:
                    continue
                current_pe = (float(current.pe_ttm)
                              if current.pe_ttm is not None and current.pe_ttm > 0 else None)
                current_pb = (float(current.pb)
                              if current.pb is not None and current.pb > 0 else None)
                if current_pe is None and current_pb is None:
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
                    pes = sorted(float(r[0]) for r in rows
                                 if r[0] is not None and r[0] > 0)
                    pbs = sorted(float(r[1]) for r in rows
                                 if r[1] is not None and r[1] > 0)
                    # 每个指标分别满足最小样本量；PB-only 指数也能计算 PB 分位。
                    if max(len(pes), len(pbs)) < 30:
                        continue

                    def pct(sorted_vals, v):
                        if len(sorted_vals) < 30 or v is None:
                            return None
                        below = sum(1 for x in sorted_vals if x <= v)
                        return round(below / len(sorted_vals) * 100, 2)

                    existing = session.query(ValuationPercentileTable).filter_by(
                        ts_code=ts_code, trade_date=target_date, period=period).first()
                    values = dict(
                        pe_percentile=pct(pes, current_pe),
                        pb_percentile=pct(pbs, current_pb),
                        sample_count=max(len(pes), len(pbs)),
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
        """根据综合分位点（PE/PB 可用项均值）返回估值区间"""
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
                    # 估值地图与雷达/退出/定投同一口径：用 PE/PB 可用项的综合分位。
                    # 5y 记录存在但 PE/PB 都为空时，要回落到 all，不能拿着空 ref 丢 zone。
                    ref = None
                    for period in ('5y', 'all'):
                        cand = item['percentiles'].get(period)
                        if cand and (cand.get('pe') is not None or cand.get('pb') is not None):
                            ref = cand
                            break
                    if ref:
                        vals = [v for v in (ref.get('pe'), ref.get('pb')) if v is not None]
                        item['zone'] = self.zone_of(round(sum(vals) / len(vals), 2)) if vals else None
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
