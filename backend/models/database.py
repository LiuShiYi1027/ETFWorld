"""
数据库模型定义（SQLAlchemy ORM）
"""
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, DateTime, Text, JSON, Numeric, Date,
    UniqueConstraint, ForeignKey
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class IndexInfoTable(Base):
    """指数基础信息表"""
    __tablename__ = 'index_info'

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts_code = Column(String(20), unique=True, nullable=False, comment='TS代码')
    name = Column(String(100), nullable=False, comment='指数名称')
    market = Column(String(20), comment='市场(SH/SZ)')
    category = Column(String(50), comment='分类(宽基/行业/主题/红利)')
    etf_codes = Column(JSON, comment='对应ETF代码列表')
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ValuationTable(Base):
    """估值数据表"""
    __tablename__ = 'valuations'

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts_code = Column(String(20), nullable=False, index=True, comment='指数代码')
    trade_date = Column(Date, nullable=False, index=True, comment='交易日期')

    close_price = Column(Numeric(12, 4), comment='收盘价')
    total_mv = Column(Numeric(20, 2), comment='总市值(元)')
    float_mv = Column(Numeric(20, 2), comment='流通市值(元)')
    turnover_rate = Column(Numeric(8, 4), comment='换手率')

    pe = Column(Numeric(12, 4), comment='市盈率')
    pe_ttm = Column(Numeric(12, 4), comment='市盈率TTM')
    pb = Column(Numeric(12, 4), comment='市净率')

    data_source = Column(String(50), default='tushare', comment='数据来源')
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint('ts_code', 'trade_date', name='uix_valuation_ts_date'),
    )


class ValuationPercentileTable(Base):
    """估值分位点数据表"""
    __tablename__ = 'valuation_percentiles'

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts_code = Column(String(20), nullable=False, index=True)
    trade_date = Column(Date, nullable=False)
    period = Column(String(10), nullable=False, comment='统计周期(3y/5y/10y/all)')

    pe_percentile = Column(Numeric(8, 4), comment='PE分位点(0-100)')
    pb_percentile = Column(Numeric(8, 4), comment='PB分位点(0-100)')
    sample_count = Column(Integer, comment='样本数量')

    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint('ts_code', 'trade_date', 'period', name='uix_pct_ts_date_period'),
    )


class GridPlanTable(Base):
    """网格计划表"""
    __tablename__ = 'grid_plans'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, comment='计划名称')
    symbol = Column(String(20), nullable=False, comment='标的代码(ETF)')
    symbol_name = Column(String(100), comment='标的名称')
    version = Column(String(10), default='1.0', comment='网格版本(1.0/2.0)')
    grid_mode = Column(String(10), default='amount', comment='投入方式(amount等金额/shares等份额)')
    shares_per_grid = Column(Numeric(16, 2), comment='每格份额(grid_mode=shares 时生效)')

    base_price = Column(Numeric(12, 4), nullable=False, comment='基准价(第一格买入价)')
    grid_step = Column(Numeric(8, 4), nullable=False, comment='网格大小(%)，如5表示5%')
    grid_count = Column(Integer, nullable=False, comment='网格数量')
    amount_per_grid = Column(Numeric(14, 2), nullable=False, comment='每格买入金额')
    step_increase = Column(Numeric(8, 4), default=0, comment='逐格加码比例(%)，每格金额递增')
    profit_retention = Column(Numeric(8, 4), default=0, comment='留利润比例(%)，2.0特性')

    levels = Column(JSON, comment='网格档位明细')
    status = Column(String(20), default='active', comment='状态(active/paused/closed)')
    note = Column(Text, comment='备注')

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    trades = relationship('TradeTable', back_populates='plan')


class TradeTable(Base):
    """交易记录表"""
    __tablename__ = 'trades'

    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(Integer, ForeignKey('grid_plans.id'), nullable=True, comment='关联网格计划')
    symbol = Column(String(20), nullable=False, index=True, comment='标的代码')
    symbol_name = Column(String(100), comment='标的名称')

    trade_date = Column(Date, nullable=False, comment='交易日期')
    direction = Column(String(10), nullable=False, comment='方向(buy/sell)')
    price = Column(Numeric(12, 4), nullable=False, comment='成交价')
    shares = Column(Numeric(16, 2), nullable=False, comment='成交份额')
    fee = Column(Numeric(12, 2), default=0, comment='手续费')
    grid_level = Column(Integer, comment='对应网格档位')
    note = Column(Text, comment='备注')

    created_at = Column(DateTime, default=datetime.now)

    plan = relationship('GridPlanTable', back_populates='trades')


class FundFlowTable(Base):
    """资金流水表（组合层·本金口径：本金 = Σ入金 − Σ出金）"""
    __tablename__ = 'fund_flows'

    id = Column(Integer, primary_key=True, autoincrement=True)
    flow_date = Column(Date, nullable=False, comment='日期')
    direction = Column(String(10), nullable=False, comment='方向(deposit/withdraw)')
    amount = Column(Numeric(14, 2), nullable=False, comment='金额(元)')
    note = Column(Text, comment='备注')

    created_at = Column(DateTime, default=datetime.now)


class WatchlistTable(Base):
    """监控池：雷达/估值管线跟踪的指数清单（首启从 SUPPORTED_INDICES 播种）"""
    __tablename__ = 'watchlist'

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts_code = Column(String(20), unique=True, nullable=False, comment='指数代码')
    name = Column(String(100), nullable=False, comment='指数名称')
    category = Column(String(50), comment='分类(宽基/行业一级/行业二级等)')
    source = Column(String(20), default='index', comment='数据源(index 宽基/sw 申万)')
    created_at = Column(DateTime, default=datetime.now)


class UpdateLogTable(Base):
    """数据更新日志表"""
    __tablename__ = 'valuation_update_log'

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    status = Column(String(20), nullable=False, comment='状态(success/failed/partial)')
    indices_count = Column(Integer)
    success_count = Column(Integer)
    failed_count = Column(Integer)
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.now)
