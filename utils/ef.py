#!/usr/bin/env python3
"""efinance 封装：东财行情走 efinance 并行通道（akproxy 推荐，更快且更省积分）。

仅封装与 akshare 逐列等价的接口，并把返回列名归一为 akshare 风格，
调用方与既有测试无需感知数据源差异：

- ef_stock_hist ≈ akshare.stock_zh_a_hist     个股K线
- ef_spot_all   ≈ akshare.stock_zh_a_spot_em  沪深京全市场快照

刻意不替换的接口（efinance 无等价或字段缺失）：
- stock_individual_info_em：efinance get_base_info 缺上市时间/总股本/流通股
- stock_zh_index_spot_em / index_zh_a_hist：efinance 不支持指数代码
"""

import pandas as pd

# akshare 风格参数 -> efinance 数值
_KLT_MAP = {"daily": 101, "weekly": 102, "monthly": 103}
_FQT_MAP = {"qfq": 1, "hfq": 2}

# 全市场快照列名：efinance -> akshare(stock_zh_a_spot_em) 风格
_SPOT_RENAME = {
    "股票代码": "代码",
    "股票名称": "名称",
    "昨日收盘": "昨收",
    "动态市盈率": "市盈率-动态",
}


class _SilentBar:
    """tqdm 静默替身：批量K线等多代码场景 efinance 会打进度条，MCP stdio 下必须静默。"""

    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def _load_ef():
    """懒加载 efinance 并静默其进度条。

    必须在 akshare_proxy_patch.install_patch 之后首次调用，
    efinance 内部的 requests.Session 才会是被补丁接管后的版本。
    """
    import efinance as ef
    import efinance.common.getter as _common_getter
    import efinance.stock.getter as _stock_getter

    _common_getter.tqdm = _SilentBar
    _stock_getter.tqdm = _SilentBar
    return ef


def ef_stock_hist(
    symbol: str,
    period: str = "daily",
    start_date: str = "",
    end_date: str = "",
    adjust: str = "qfq",
) -> pd.DataFrame:
    """个股日/周/月K线，参数与 akshare.stock_zh_a_hist 对齐（日期格式 YYYYMMDD）。

    adjust: qfq=前复权, hfq=后复权, 其余(None/none/"")=不复权
    """
    ef = _load_ef()
    df = ef.stock.get_quote_history(
        symbol,
        beg=start_date or "19000101",
        end=end_date or "20500101",
        klt=_KLT_MAP.get(period, 101),
        fqt=_FQT_MAP.get((adjust or "").lower(), 0),
    )
    # 列序对齐 akshare（日期在前，股票名称紧随其后），其余列保持 efinance 原序
    if "股票名称" in df.columns and "日期" in df.columns:
        cols = [c for c in df.columns if c != "股票名称"]
        cols.insert(cols.index("日期") + 1, "股票名称")
        df = df[cols]
    return df


def ef_spot_all() -> pd.DataFrame:
    """沪深京全市场实时快照，线程池并行分页，返回列名与 stock_zh_a_spot_em 对齐。"""
    ef = _load_ef()
    df = ef.stock.get_realtime_quotes()
    return df.rename(columns=_SPOT_RENAME)


_ETF_SPOT_RENAME = {
    "股票代码": "代码",
    "股票名称": "名称",
    "昨日收盘": "昨收",
    "最新交易日": "数据日期",
}


def ef_etf_spot() -> pd.DataFrame:
    """全市场 ETF 实时快照（约1600只），列名与 fund_etf_spot_em 基本对齐。

    与 akshare.fund_etf_spot_em 的差异：无 主力净流入-净额 / 最新份额 列，
    多 最新交易日 列（可直接用作快照日期）。"""
    ef = _load_ef()
    df = ef.stock.get_realtime_quotes(["ETF"])
    return df.rename(columns=_ETF_SPOT_RENAME)
