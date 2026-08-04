import importlib
import sys


def test_desktop_uses_user_database(monkeypatch, tmp_path):
    monkeypatch.setenv('ETFWORLD_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('DATABASE_URL', raising=False)
    sys.modules.pop('desktop', None)

    desktop = importlib.import_module('desktop')

    assert desktop.DB_PATH.parent == desktop.DATA_DIR
    assert desktop.DB_PATH.name == 'etfworld.db'
    assert desktop.URL.startswith('http://127.0.0.1:')


def test_health_endpoint():
    from backend.api.main import health

    assert health() == {'status': 'ok'}


def test_latest_trade_date_does_not_depend_on_source_order():
    import pandas as pd
    from backend.services.tushare_client import TushareClient

    client = object.__new__(TushareClient)
    client.pro = object()
    client.get_recent_trade_dates = lambda **_: ['20260701', '20260630', '20260602']

    assert client.get_latest_trade_date() == '20260701'


def test_recent_trade_dates_are_sorted_newest_first(monkeypatch):
    import pandas as pd
    from backend.services.tushare_client import TushareClient

    client = object.__new__(TushareClient)
    client.pro = object()
    client.get_trade_dates = lambda **_: pd.DataFrame({
        'cal_date': ['20260701', '20260630', '20260602'],
    })

    assert client.get_recent_trade_dates() == ['20260701', '20260630', '20260602']
