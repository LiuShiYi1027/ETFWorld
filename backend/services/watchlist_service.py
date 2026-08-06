"""
监控池服务：雷达/估值管线跟踪的指数清单

清单存在数据库里（首次启动时若为空，从 settings.SUPPORTED_INDICES 播种），
用户在界面上增删，不再改代码。估值更新、雷达评分、ETF 关联全部读这里。
"""
import logging
from typing import Dict, List, Optional

from backend.config.settings import SUPPORTED_INDICES
from backend.models.database import WatchlistTable
from backend.utils.db import get_session

logger = logging.getLogger(__name__)


class WatchlistService:
    """监控池增删查"""

    def list_indices(self) -> List[Dict]:
        with get_session() as session:
            self._seed_if_empty(session)
            rows = session.query(WatchlistTable).order_by(WatchlistTable.id).all()
            return [{'ts_code': r.ts_code, 'name': r.name, 'category': r.category,
                     'source': r.source} for r in rows]

    def add(self, ts_code: str, name: str, category: str = None,
            source: str = 'index') -> Dict:
        """加入监控池。重复代码报 ValueError。调用方负责后续回填历史数据。"""
        with get_session() as session:
            self._seed_if_empty(session)
            if session.query(WatchlistTable).filter_by(ts_code=ts_code).first():
                raise ValueError(f'{ts_code} 已在监控池中')
            row = WatchlistTable(ts_code=ts_code, name=name,
                                 category=category, source=source)
            session.add(row)
            session.flush()
            logger.info('监控池新增: %s %s (%s)', ts_code, name, source)
            return {'ts_code': ts_code, 'name': name, 'category': category,
                    'source': source}

    def remove(self, ts_code: str) -> bool:
        with get_session() as session:
            row = session.query(WatchlistTable).filter_by(ts_code=ts_code).first()
            if not row:
                return False
            session.delete(row)
            logger.info('监控池移除: %s', ts_code)
            return True

    def name_map(self) -> Dict[str, str]:
        return {i['ts_code']: i['name'] for i in self.list_indices()}

    def category_map(self) -> Dict[str, Optional[str]]:
        return {i['ts_code']: i['category'] for i in self.list_indices()}

    @staticmethod
    def _seed_if_empty(session) -> None:
        """空表时从默认清单播种（开源用户开箱即用的 41 只精选池）"""
        if session.query(WatchlistTable).count() == 0:
            for idx in SUPPORTED_INDICES:
                session.add(WatchlistTable(
                    ts_code=idx['ts_code'], name=idx['name'],
                    category=idx.get('category'), source=idx.get('source', 'index')))
            session.flush()
            logger.info('监控池已从默认清单播种 %d 只', len(SUPPORTED_INDICES))
