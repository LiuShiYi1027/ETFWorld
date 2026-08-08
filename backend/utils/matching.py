"""ETF 名 ↔ 监控指数名 的子串匹配（项目内唯一实现，各 service 统一引用）"""
from typing import List, Optional


def _strip_sw_suffix(name: str) -> str:
    """申万行业名去掉 Ⅰ/Ⅱ/Ⅲ 后缀，便于和 ETF 名做子串匹配"""
    return (name or '').rstrip('ⅠⅡⅢ')


def match_index_name(symbol_name: str, index_names: List[str]) -> Optional[str]:
    """ETF 名 → 监控指数名：最长子串匹配（"沪深300ETF"→"沪深300"，"证券ETF"→"证券Ⅱ"）"""
    if not symbol_name:
        return None
    best = None
    for full in index_names:
        short = _strip_sw_suffix(full)
        if short and short in symbol_name and (best is None or len(short) > len(_strip_sw_suffix(best))):
            best = full
    return best
