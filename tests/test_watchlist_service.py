"""监控池服务测试：播种、增删、重复拦截"""
import pytest

from backend.models.database import Base
from backend.utils.db import engine, init_db
from backend.services.watchlist_service import WatchlistService


@pytest.fixture(autouse=True)
def _clean_db():
    init_db()
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


def test_seed_from_default_on_first_use():
    wl = WatchlistService()
    items = wl.list_indices()
    assert len(items) == 41  # 默认精选池（8 宽基 + 10 一级 + 23 二级）
    codes = {i['ts_code'] for i in items}
    assert '000300.SH' in codes and '801780.SI' in codes
    assert all(i['source'] in ('index', 'sw') for i in items)


def test_add_and_remove():
    wl = WatchlistService()
    wl.list_indices()  # 触发播种
    wl.add('801980.SI', '煤炭', '行业一级', 'sw')
    codes = {i['ts_code'] for i in wl.list_indices()}
    assert '801980.SI' in codes
    assert wl.remove('801980.SI') is True
    assert '801980.SI' not in {i['ts_code'] for i in wl.list_indices()}
    assert wl.remove('801980.SI') is False  # 已删除


def test_add_duplicate_rejected():
    wl = WatchlistService()
    wl.list_indices()
    with pytest.raises(ValueError):
        wl.add('000300.SH', '沪深300', '宽基', 'index')  # 播种清单里已有


def test_name_map_reflects_changes():
    wl = WatchlistService()
    wl.list_indices()
    wl.add('801980.SI', '煤炭', '行业一级', 'sw')
    assert wl.name_map()['801980.SI'] == '煤炭'
    wl.remove('801980.SI')
    assert '801980.SI' not in wl.name_map()


def test_remove_all_does_not_reseed():
    """回归：用户删空监控池后不得自动复活默认池（播种只发生一次）"""
    wl = WatchlistService()
    for idx in wl.list_indices():
        assert wl.remove(idx['ts_code']) is True
    assert wl.list_indices() == []
    assert wl.list_indices() == []  # 再读也不复活
