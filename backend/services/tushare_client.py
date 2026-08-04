"""
Tushare数据客户端
"""
import tushare as ts
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional, Dict, List
import logging

logger = logging.getLogger(__name__)


class TushareClient:
    """Tushare数据客户端"""

    def __init__(self, token: str = None):
        """
        初始化Tushare客户端

        统一通过 tushare_init.get_pro() 获取 pro 对象，
        确保自定义 __http_url 已正确设置。

        Args:
            token: Tushare API Token（可选，默认读取配置）
        """
        from backend.services.tushare_init import get_pro
        self.pro = get_pro(token)

    def is_connected(self) -> bool:
        """检查连接状态"""
        return self.pro is not None

    def get_index_dailybasic(
        self,
        ts_code: str = None,
        trade_date: str = None,
        start_date: str = None,
        end_date: str = None,
        fields: str = None
    ) -> pd.DataFrame:
        """
        获取指数每日基本面数据

        Args:
            ts_code: 指数代码
            trade_date: 交易日期 YYYYMMDD
            start_date: 开始日期
            end_date: 结束日期
            fields: 字段列表

        Returns:
            DataFrame包含估值数据
        """
        if not self.is_connected():
            logger.error("Tushare未连接，无法获取数据")
            return pd.DataFrame()

        try:
            df = self.pro.index_dailybasic(
                ts_code=ts_code,
                trade_date=trade_date,
                start_date=start_date,
                end_date=end_date,
                fields=fields
            )

            if df is not None and not df.empty:
                logger.info(f"成功获取指数估值数据: {ts_code}, {trade_date}, 记录数: {len(df)}")
            else:
                logger.warning(f"未获取到数据: {ts_code}, {trade_date}")

            return df

        except Exception as e:
            logger.error(f"获取指数估值数据失败: {e}")
            return pd.DataFrame()

    def get_sw_daily(
        self,
        ts_code: str = None,
        trade_date: str = None,
        start_date: str = None,
        end_date: str = None,
    ) -> pd.DataFrame:
        """
        获取申万行业指数日线（含PE/PB估值）

        sw_daily 返回字段: trade_date, ts_code, close, pe, pb,
        total_mv, float_mv 等。注意只有 pe（无 pe_ttm）。
        """
        if not self.is_connected():
            logger.error("Tushare未连接，无法获取数据")
            return pd.DataFrame()

        try:
            df = self.pro.sw_daily(
                ts_code=ts_code,
                trade_date=trade_date,
                start_date=start_date,
                end_date=end_date,
            )
            if df is not None and not df.empty:
                logger.info(f"成功获取申万行业估值: {ts_code}, 记录数: {len(df)}")
            else:
                logger.warning(f"未获取到申万数据: {ts_code}, {trade_date}")
            return df
        except Exception as e:
            logger.error(f"获取申万行业估值失败: {e}")
            return pd.DataFrame()

    def get_index_daily(
        self,
        ts_code: str = None,
        trade_date: str = None,
        start_date: str = None,
        end_date: str = None,
        fields: str = None
    ) -> pd.DataFrame:
        """
        获取指数日线行情

        Args:
            ts_code: 指数代码
            trade_date: 交易日期 YYYYMMDD
            start_date: 开始日期
            end_date: 结束日期
            fields: 字段列表

        Returns:
            DataFrame包含行情数据
        """
        if not self.is_connected():
            logger.error("Tushare未连接，无法获取数据")
            return pd.DataFrame()

        try:
            df = self.pro.index_daily(
                ts_code=ts_code,
                trade_date=trade_date,
                start_date=start_date,
                end_date=end_date,
                fields=fields
            )

            if df is not None and not df.empty:
                logger.info(f"成功获取指数行情数据: {ts_code}, 记录数: {len(df)}")

            return df

        except Exception as e:
            logger.error(f"获取指数行情数据失败: {e}")
            return pd.DataFrame()

    def get_index_basic(self, ts_code: str = None, market: str = None, name: str = None) -> pd.DataFrame:
        """
        获取指数基本信息

        Args:
            ts_code: 指数代码
            market: 市场 (SSE上交所/SZSE深交所)
            name: 指数名称

        Returns:
            DataFrame包含指数基本信息
        """
        if not self.is_connected():
            return pd.DataFrame()

        try:
            df = self.pro.index_basic(ts_code=ts_code, market=market, name=name)
            return df
        except Exception as e:
            logger.error(f"获取指数基本信息失败: {e}")
            return pd.DataFrame()

    def get_trade_dates(
        self,
        start_date: str = None,
        end_date: str = None,
        exchange: str = 'SSE'
    ) -> pd.DataFrame:
        """
        获取交易日历

        Args:
            start_date: 开始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD
            exchange: 交易所

        Returns:
            DataFrame包含交易日历
        """
        if not self.is_connected():
            return pd.DataFrame()

        try:
            df = self.pro.trade_cal(
                exchange=exchange,
                start_date=start_date,
                end_date=end_date,
                is_open=1  # 只返回开盘日
            )
            return df
        except Exception as e:
            logger.error(f"获取交易日历失败: {e}")
            return pd.DataFrame()

    def get_latest_trade_date(self) -> Optional[str]:
        """
        获取最近的交易日

        Returns:
            最近交易日期字符串 YYYYMMDD，失败返回None
        """
        if not self.is_connected():
            return None

        try:
            dates = self.get_recent_trade_dates(limit=1)
            if dates:
                latest_date = dates[0]
                logger.info(f"最近交易日: {latest_date}")
                return latest_date

            return None

        except Exception as e:
            logger.error(f"获取最近交易日失败: {e}")
            return None

    def get_recent_trade_dates(self, limit: int = 5) -> List[str]:
        """返回最近已完成的交易日，按新到旧排序。"""
        now = datetime.now()
        # 日终估值通常在收盘后发布；18:00 前不探测当天数据。
        end = now if now.hour >= 18 else now - timedelta(days=1)
        start = end - timedelta(days=30)
        df = self.get_trade_dates(
            start_date=start.strftime('%Y%m%d'),
            end_date=end.strftime('%Y%m%d'),
        )
        if df is None or df.empty or 'cal_date' not in df:
            return []
        return sorted(set(df['cal_date'].astype(str)), reverse=True)[:limit]

    def batch_get_index_valuation(
        self,
        ts_codes: List[str],
        trade_date: str = None
    ) -> Dict[str, Dict]:
        """
        批量获取多个指数的估值数据

        Args:
            ts_codes: 指数代码列表
            trade_date: 交易日期 YYYYMMDD

        Returns:
            字典，key为指数代码，value为估值数据
        """
        if trade_date is None:
            trade_date = self.get_latest_trade_date()

        if trade_date is None:
            logger.error("无法获取交易日")
            return {}

        results = {}

        for ts_code in ts_codes:
            try:
                df = self.get_index_dailybasic(ts_code=ts_code, trade_date=trade_date)

                if df is not None and not df.empty:
                    row = df.iloc[0]

                    results[ts_code] = {
                        'ts_code': row.get('ts_code'),
                        'trade_date': row.get('trade_date'),
                        'total_mv': row.get('total_mv'),  # 总市值
                        'float_mv': row.get('float_mv'),  # 流通市值
                        'total_share': row.get('total_share'),  # 总股本
                        'float_share': row.get('float_share'),  # 流通股本
                        'free_share': row.get('free_share'),  # 自由流通股本
                        'turnover_rate': row.get('turnover_rate'),  # 换手率
                        'turnover_rate_f': row.get('turnover_rate_f'),  # 换手率(自由流通)
                        'pe': row.get('pe'),  # 市盈率
                        'pe_ttm': row.get('pe_ttm'),  # 市盈率TTM
                        'pb': row.get('pb'),  # 市净率
                    }
                    logger.info(f"成功获取 {ts_code} 估值数据")
                else:
                    logger.warning(f"未获取到 {ts_code} 的估值数据")
                    results[ts_code] = None

            except Exception as e:
                logger.error(f"获取 {ts_code} 估值数据失败: {e}")
                results[ts_code] = None

        return results


# 测试代码
if __name__ == '__main__':
    # 设置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # 创建客户端（需要配置Tushare Token）
    client = TushareClient()

    if client.is_connected():
        # 测试获取最近交易日
        latest_date = client.get_latest_trade_date()
        print(f"最近交易日: {latest_date}")

        # 测试获取指数估值
        if latest_date:
            from backend.config.settings import SUPPORTED_INDICES
            ts_codes = [idx['ts_code'] for idx in SUPPORTED_INDICES]

            results = client.batch_get_index_valuation(ts_codes, latest_date)

            print("\n估值数据:")
            for ts_code, data in results.items():
                if data:
                    print(f"{ts_code}: PE={data.get('pe_ttm')}, PB={data.get('pb')}")
