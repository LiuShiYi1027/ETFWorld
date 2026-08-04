"""
Tushare 初始化（唯一入口）

⭐️ 本项目所有 Tushare 调用都必须通过这里获取 pro 对象，
   关键是设置自定义 __http_url，否则会提示 "Token 不对"。

标准调用方式：
    from backend.services.tushare_init import get_pro, pro_bar

    pro = get_pro()
    df = pro.index_basic(limit=5)

    # pro_bar 接口必须显式传 api=pro
    df = pro_bar(ts_code="000001.SZ", limit=3)
"""
import logging

import tushare as ts
from backend.config import settings

logger = logging.getLogger(__name__)


def get_pro(token: str = None, api_url: str = None):
    """
    获取已正确配置的 Tushare pro 对象。

    必须设置 pro._DataApi__http_url，否则接口会报 Token 错误。
    Token/URL 未配置时返回 None。
    """
    token = token or settings.TUSHARE_TOKEN
    if not token:
        logger.warning("Tushare Token 未配置，请设置环境变量 TUSHARE_TOKEN")
        return None

    pro = ts.pro_api(token)
    # ⭐️ 关键：指向自定义数据源，缺这一行会提示 "Token 不对"
    endpoint = api_url or settings.TUSHARE_API_URL
    pro._DataApi__http_url = endpoint
    logger.info("Tushare pro 初始化成功（endpoint=%s）", endpoint)
    return pro


def pro_bar(api=None, **kwargs):
    """
    pro_bar 接口封装：自动补上 api=pro。

    用法: pro_bar(ts_code="000001.SZ", limit=3)
    """
    if api is None:
        api = get_pro()
    return ts.pro_bar(api=api, **kwargs)


# 自测
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    pro = get_pro()
    if pro:
        print(pro.index_basic(limit=5))
        print(pro_bar(api=pro, ts_code="000001.SZ", limit=3))
