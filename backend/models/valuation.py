"""
估值数据模型
"""
from datetime import datetime
from typing import Optional
from decimal import Decimal


class Valuation:
    """估值数据模型"""

    def __init__(
        self,
        ts_code: str,
        trade_date: str,
        pe: Optional[float] = None,
        pe_ttm: Optional[float] = None,
        pb: Optional[float] = None,
        total_mv: Optional[float] = None,
        float_mv: Optional[float] = None,
        total_share: Optional[float] = None,
        float_share: Optional[float] = None,
        free_share: Optional[float] = None,
        turnover_rate: Optional[float] = None,
        turnover_rate_f: Optional[float] = None,
        data_source: str = 'tushare',
        calc_time: Optional[datetime] = None
    ):
        self.ts_code = ts_code
        self.trade_date = trade_date
        self.pe = pe
        self.pe_ttm = pe_ttm
        self.pb = pb
        self.total_mv = total_mv
        self.float_mv = float_mv
        self.total_share = total_share
        self.float_share = float_share
        self.free_share = free_share
        self.turnover_rate = turnover_rate
        self.turnover_rate_f = turnover_rate_f
        self.data_source = data_source
        self.calc_time = calc_time or datetime.now()

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            'ts_code': self.ts_code,
            'trade_date': self.trade_date,
            'pe': self.pe,
            'pe_ttm': self.pe_ttm,
            'pb': self.pb,
            'total_mv': self.total_mv,
            'float_mv': self.float_mv,
            'total_share': self.total_share,
            'float_share': self.float_share,
            'free_share': self.free_share,
            'turnover_rate': self.turnover_rate,
            'turnover_rate_f': self.turnover_rate_f,
            'data_source': self.data_source,
            'calc_time': self.calc_time
        }

    @classmethod
    def from_tushare_dict(cls, data: dict) -> 'Valuation':
        """从Tushare数据创建"""
        return cls(
            ts_code=data.get('ts_code'),
            trade_date=data.get('trade_date'),
            pe=data.get('pe'),
            pe_ttm=data.get('pe_ttm'),
            pb=data.get('pb'),
            total_mv=data.get('total_mv'),
            float_mv=data.get('float_mv'),
            total_share=data.get('total_share'),
            float_share=data.get('float_share'),
            free_share=data.get('free_share'),
            turnover_rate=data.get('turnover_rate'),
            turnover_rate_f=data.get('turnover_rate_f'),
            data_source='tushare'
        )

    def __repr__(self) -> str:
        return (f"Valuation(ts_code={self.ts_code}, trade_date={self.trade_date}, "
                f"pe_ttm={self.pe_ttm}, pb={self.pb})")


class IndexInfo:
    """指数基本信息模型"""

    def __init__(
        self,
        ts_code: str,
        name: str,
        market: str,
        category: str = '宽基',
        launch_date: Optional[str] = None,
        base_date: Optional[str] = None,
        base_point: Optional[float] = None,
        etf_codes: Optional[list] = None
    ):
        self.ts_code = ts_code
        self.name = name
        self.market = market  # SH 或 SZ
        self.category = category
        self.launch_date = launch_date
        self.base_date = base_date
        self.base_point = base_point
        self.etf_codes = etf_codes or []

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            'ts_code': self.ts_code,
            'name': self.name,
            'market': self.market,
            'category': self.category,
            'launch_date': self.launch_date,
            'base_date': self.base_date,
            'base_point': self.base_point,
            'etf_codes': self.etf_codes
        }

    @classmethod
    def from_config(cls, config: dict) -> 'IndexInfo':
        """从配置创建"""
        ts_code = config['ts_code']
        # 从ts_code提取市场
        market = 'SH' if ts_code.endswith('.SH') else 'SZ'

        return cls(
            ts_code=ts_code,
            name=config['name'],
            market=market,
            category=config.get('category', '宽基')
        )

    def __repr__(self) -> str:
        return f"IndexInfo(ts_code={self.ts_code}, name={self.name})"


class ValuationPercentile:
    """估值分位点数据模型"""

    def __init__(
        self,
        ts_code: str,
        trade_date: str,
        period: str,
        pe_percentile: Optional[float] = None,
        pb_percentile: Optional[float] = None,
        ps_percentile: Optional[float] = None,
        dy_percentile: Optional[float] = None,
        sample_count: Optional[int] = None
    ):
        self.ts_code = ts_code
        self.trade_date = trade_date
        self.period = period  # 3y, 5y, 10y, all
        self.pe_percentile = pe_percentile
        self.pb_percentile = pb_percentile
        self.ps_percentile = ps_percentile
        self.dy_percentile = dy_percentile
        self.sample_count = sample_count

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            'ts_code': self.ts_code,
            'trade_date': self.trade_date,
            'period': self.period,
            'pe_percentile': self.pe_percentile,
            'pb_percentile': self.pb_percentile,
            'ps_percentile': self.ps_percentile,
            'dy_percentile': self.dy_percentile,
            'sample_count': self.sample_count
        }

    def __repr__(self) -> str:
        return (f"ValuationPercentile(ts_code={self.ts_code}, "
                f"trade_date={self.trade_date}, period={self.period})")
