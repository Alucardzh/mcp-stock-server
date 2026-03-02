#!/usr/bin/env python3
"""
MCP Akshare Stock Data Server using FastMCP.

This module provides MCP-compliant server for Chinese stock data analysis
using Akshare API and FastMCP framework following PEP 723 standards.
"""

__all__ = [
    "get_stock_history",
    "get_stock_realtime",
    "get_stock_basic",
    "calculate_support_resistance_func",
    "get_market_index",
    "get_stock_symbol_by_name",
]
import functools
import json
import logging
import time
from http.client import RemoteDisconnected
from typing import Any
from urllib.error import URLError

import akshare_proxy_patch
import pandas as pd

akshare_proxy_patch.install_patch("101.201.173.125", "", 50)
from akshare import (
    stock_individual_info_em,
    stock_zh_a_hist,
    stock_zh_a_spot_em,
    stock_zh_index_spot_em,
)

from .support_resistance import calculate_support_resistance
from .validate import (
    format_stock_data,
    get_name_code_mapping,
    parse_date_range,
    validate_stock_symbol,
)

# Import our analysis modules


# Configure logging
logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# 网络请求可重试的异常类型
RETRYABLE_EXCEPTIONS = (
    URLError,
    RemoteDisconnected,
    ConnectionError,
    ConnectionResetError,
    TimeoutError,
    OSError,
)


# 缓存管理器 - 用于缓存实时行情数据
class CachedData:
    """带TTL的缓存数据类"""

    def __init__(self, data: Any, ttl: int = 5):
        self.data = data
        self.expire_time = time.time() + ttl

    def is_expired(self) -> bool:
        """检查缓存是否过期"""
        return time.time() > self.expire_time


# 全局缓存字典
_spot_data_cache: CachedData | None = None


def _get_cached_spot_data(ttl: int = 5) -> pd.DataFrame:
    """获取缓存的实时行情数据

    Args:
        ttl: 缓存生存时间（秒），默认5秒

    Returns:
        实时行情DataFrame
    """
    global _spot_data_cache

    # 如果缓存存在且未过期，直接返回
    if _spot_data_cache is not None and not _spot_data_cache.is_expired():
        return _spot_data_cache.data

    # 否则获取新数据并缓存
    logger.info("Refreshing spot data cache with TTL=%ds", ttl)
    new_data = stock_zh_a_spot_em()
    _spot_data_cache = CachedData(new_data, ttl=ttl)
    return new_data


def with_retry(max_retries: int = 3, delay: float = 1.0, backoff: float = 2.0):
    """重试装饰器，用于处理网络请求不稳定的情况

    Args:
        max_retries: 最大重试次数
        delay: 初始延迟时间（秒）
        backoff: 延迟时间的增长倍数
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            current_delay = delay
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except RETRYABLE_EXCEPTIONS as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logger.warning(
                            "Retry %d/%d for %s: %s. Waiting %.1fs...",
                            attempt + 1,
                            max_retries,
                            func.__name__,
                            str(e),
                            current_delay,
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(
                            "All %d retries failed for %s: %s",
                            max_retries,
                            func.__name__,
                            str(e),
                        )
            raise last_exception

        return wrapper

    return decorator


class RateLimiter:
    """速率限制器，防止API调用过于频繁

    使用滑动窗口算法来限制函数调用频率
    """

    def __init__(self, max_calls: int, time_window: float):
        """初始化速率限制器

        Args:
            max_calls: 时间窗口内允许的最大调用次数
            time_window: 时间窗口长度（秒）
        """
        self.max_calls = max_calls
        self.time_window = time_window
        self.calls: list[float] = []

    def __call__(self, func):
        """装饰器函数"""

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            now = time.time()

            # 移除时间窗口外的调用记录
            self.calls = [
                call_time
                for call_time in self.calls
                if call_time > now - self.time_window
            ]

            # 检查是否超过速率限制
            if len(self.calls) >= self.max_calls:
                # 计算需要等待的时间
                oldest_call = self.calls[0]
                wait_time = self.time_window - (now - oldest_call)
                logger.warning(
                    "Rate limit exceeded for %s. %.1fs until next available call.",
                    func.__name__,
                    wait_time,
                )
                return json.dumps(
                    {
                        "success": False,
                        "error": f"Rate limit exceeded. Please wait {wait_time:.1f} seconds before retrying.",
                        "error_type": "RateLimitError",
                    },
                    ensure_ascii=False,
                    indent=2,
                )

            # 记录本次调用
            self.calls.append(now)

            # 执行函数
            return func(*args, **kwargs)

        return wrapper


@RateLimiter(max_calls=20, time_window=60)
@with_retry(max_retries=3, delay=1.0, backoff=2.0)
def get_stock_history(
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
    period: str = "daily",
    adjust: str = "qfq",
) -> str:
    """Get historical stock data for a Chinese stock.

    Args:
        symbol: 6-digit stock symbol (e.g., '000001')
        start_date: Start date in YYYY-MM-DD format (optional)
        end_date: End date in YYYY-MM-DD format (optional)
        period: Data period - daily, weekly, or monthly (default: daily)
        adjust: Price adjustment - qfq, hfq, or none (default: qfq)
    """
    try:
        symbol = validate_stock_symbol(symbol)
        start_date, end_date = parse_date_range(
            start_date, end_date, default_period_days=90
        )
        logger.info("Fetching historical data for %s", symbol)
        # Format dates for akshare
        start_date_formatted = start_date.replace("-", "") if start_date else ""
        end_date_formatted = end_date.replace("-", "") if end_date else ""
        # Fetch data from Akshare
        data = stock_zh_a_hist(
            symbol=symbol,
            period=period,
            start_date=start_date_formatted,
            end_date=end_date_formatted,
            adjust=adjust,
        )
        if data is None or data.empty:
            return json.dumps(
                {
                    "success": False,
                    "error": f"No historical data found for symbol {symbol}",
                },
                ensure_ascii=False,
                indent=2,
            )
        # Format response
        response = format_stock_data(data)
        if response.success:
            return json.dumps(
                response.model_dump(exclude_none=True), ensure_ascii=False, indent=2
            )
        return json.dumps(
            {"success": False, "error": response.error}, ensure_ascii=False, indent=2
        )
    except Exception as e:
        logger.error("Error fetching stock history: %s", e)
        return json.dumps(
            {"success": False, "error": f"Error fetching stock history: {str(e)}"},
            ensure_ascii=False,
            indent=2,
        )


@RateLimiter(max_calls=20, time_window=60)
@with_retry(max_retries=3, delay=1.0, backoff=2.0)
def get_stock_realtime(symbol: str) -> str:
    """Get real-time stock data for a Chinese stock.

    Args:
        symbol: 6-digit stock symbol (e.g., '000001')
    """
    try:
        symbol = validate_stock_symbol(symbol)
        logger.info("Fetching real-time data for %s", symbol)
        # Fetch data from cache (5-second TTL)
        data = _get_cached_spot_data(ttl=5)
        if data is None or data.empty:
            return json.dumps(
                {
                    "success": False,
                    "error": f"No real-time data found for symbol {symbol}",
                },
                ensure_ascii=False,
                indent=2,
            )
        # Find the specific stock
        stock_data = data[data["代码"] == symbol]
        if stock_data.empty:
            return json.dumps(
                {
                    "success": False,
                    "error": f"Stock {symbol} not found in real-time data",
                },
                ensure_ascii=False,
                indent=2,
            )
        # Format response
        result = {
            "success": True,
            "data": {
                "symbol": symbol,
                "name": (
                    stock_data["名称"].iloc[0] if not stock_data["名称"].empty else None
                ),
                "current_price": (
                    float(stock_data["最新价"].iloc[0])
                    if "最新价" in stock_data.columns
                    else None
                ),
                "change": (
                    float(stock_data["涨跌额"].iloc[0])
                    if "涨跌额" in stock_data.columns
                    else None
                ),
                "change_percent": (
                    float(stock_data["涨跌幅"].iloc[0])
                    if "涨跌幅" in stock_data.columns
                    else None
                ),
                "volume": (
                    int(stock_data["成交量"].iloc[0])
                    if "成交量" in stock_data.columns
                    else None
                ),
                "amount": (
                    float(stock_data["成交额"].iloc[0])
                    if "成交额" in stock_data.columns
                    else None
                ),
                "update_time": pd.Timestamp.now().isoformat(),
                "source": "akshare_realtime",
            },
        }
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Error fetching real-time data: %s", e)
        return json.dumps(
            {"success": False, "error": f"Error fetching real-time data: {str(e)}"},
            ensure_ascii=False,
            indent=2,
        )


@RateLimiter(max_calls=30, time_window=60)
@with_retry(max_retries=3, delay=1.0, backoff=2.0)
def get_stock_basic(symbol: str) -> str:
    """Get basic information about a Chinese stock.

    Args:
        symbol: 6-digit stock symbol (e.g., '000001')
    """
    try:
        symbol = validate_stock_symbol(symbol)
        logger.info("Fetching basic info for %s", symbol)
        # Fetch individual stock info from Akshare
        data = stock_individual_info_em(symbol=symbol)
        if data is None or data.empty:
            return json.dumps(
                {"success": False, "error": f"No basic data found for symbol {symbol}"},
                ensure_ascii=False,
                indent=2,
            )
        # # Fetch data from Akshare
        info_dict = {}
        if len(data) >= 2:  # Usually has two columns: item and value
            for _, row in data.iterrows():
                if len(row) >= 2:
                    key = str(row.iloc[0]).strip()
                    value = row.iloc[1]
                    info_dict[key] = value
        # Extract relevant information (matching Chinese field names from akshare)
        # Common field names: 股票代码, 股票简称, 总股本, 流通股, 行业, 上市时间
        result = {
            "success": True,
            "data": {
                "symbol": str(info_dict.get("股票代码", symbol)),
                "name": str(info_dict.get("股票简称", "Unknown")),
                "industry": str(info_dict.get("行业", "Unknown")),
                "list_date": str(info_dict.get("上市时间", "Unknown")),
                "total_shares": info_dict.get("总股本", None),
                "circulating_shares": info_dict.get("流通股", None),
                "total_market_value": info_dict.get("总市值", None),
                "circulating_market_value": info_dict.get("流通市值", None),
                "current_price": info_dict.get("最新价", None),
                "source": "akshare",
            },
        }
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Error fetching basic info: %s", e)
        return json.dumps(
            {"success": False, "error": f"Error fetching basic info: {str(e)}"},
            ensure_ascii=False,
            indent=2,
        )


@RateLimiter(max_calls=10, time_window=60)
@with_retry(max_retries=3, delay=1.0, backoff=2.0)
def calculate_support_resistance_func(
    symbol: str, n_levels: int = 5, lookback_period: int = 60
) -> str:
    """Calculate support and resistance levels for a stock.

    Args:
        symbol: 6-digit stock symbol (e.g., '000001')
        n_levels: Number of support/resistance levels to identify (1-10, default: 5)
        lookback_period: Analysis period in days (30-365, default: 60)
    """
    try:
        symbol = validate_stock_symbol(symbol)
        logger.info("Calculating support/resistance for %s", symbol)
        # Get historical data for calculation
        start_date, end_date = parse_date_range(None, None, default_period_days=180)
        start_date_formatted = start_date.replace("-", "")
        end_date_formatted = end_date.replace("-", "")
        hist_data = stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start_date_formatted,
            end_date=end_date_formatted,
            adjust="qfq",
        )
        if hist_data is None or hist_data.empty:
            return json.dumps(
                {
                    "success": False,
                    "error": f"No historical data found for {symbol} to calculate support/resistance",
                },
                ensure_ascii=False,
                indent=2,
            )
        # Calculate support and resistance
        # Use correct column name for closing price
        close_col = "收盘" if "收盘" in hist_data.columns else "close"
        result = calculate_support_resistance(
            prices=hist_data[close_col],
            n_levels=n_levels,
            lookback_period=min(lookback_period, len(hist_data)),
        )
        result = {
            **result,
            **{
                "success": True,
                "symbol": symbol,
                "calculation_date": pd.Timestamp.now().isoformat(),
                "parameters": {
                    "n_levels": n_levels,
                    "lookback_period": lookback_period,
                },
            },
        }
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Error calculating support/resistance: %s", e)
        return json.dumps(
            {"success": False, "error": f"Error calculating support/resistance: {e}"},
            ensure_ascii=False,
            indent=2,
        )


@RateLimiter(max_calls=30, time_window=60)
@with_retry(max_retries=3, delay=1.0, backoff=2.0)
def get_market_index(index_code: str = "000001") -> str:
    """Get major Chinese market indices data.

    Args:
        index_code: Index code (default: 000001 for Shanghai Composite)
    """
    try:
        logger.info("Fetching market index data for %s", index_code)
        # Fetch index data from Akshare
        data = stock_zh_index_spot_em()
        if data is None or data.empty:
            return json.dumps(
                {
                    "success": False,
                    "error": f"No index data found for code {index_code}",
                },
                ensure_ascii=False,
                indent=2,
            )
        # Find the specific index
        index_data = data[data["代码"] == index_code]
        if index_data.empty:
            return json.dumps(
                {
                    "success": False,
                    "error": f"Index {index_code} not found in index data",
                },
                ensure_ascii=False,
                indent=2,
            )
        # Format response
        if len(index_data) > 0:
            index = index_data.iloc[0]
            result = {
                "success": True,
                "data": {
                    "code": index_code,
                    "name": index.get("名称", "Unknown"),
                    "current_point": float(index["最新价"])
                    if "最新价" in index
                    else None,
                    "change": float(index["涨跌额"]) if "涨跌额" in index else None,
                    "change_percent": float(index["涨跌幅"])
                    if "涨跌幅" in index
                    else None,
                    "volume": int(index["成交量"]) if "成交量" in index else None,
                    "amount": float(index["成交额"]) if "成交额" in index else None,
                    "update_time": pd.Timestamp.now().isoformat(),
                    "source": "akshare_index",
                },
            }
            return json.dumps(result, ensure_ascii=False, indent=2)
        return json.dumps(
            {"success": False, "error": f"No data returned for index {index_code}"},
            ensure_ascii=False,
            indent=2,
        )

    except Exception as e:
        logger.error("Error fetching index data: %s", e)
        return json.dumps(
            {"success": False, "error": f"Error fetching index data: {str(e)}"},
            ensure_ascii=False,
            indent=2,
        )


def get_stock_symbol_by_name(name: str) -> str:
    """通过名称获取股票代码

    Args:
        name: 股票名称（支持部分匹配）

    Returns:
        JSON字符串，包含匹配的股票代码和名称
    """
    try:
        if not name or not isinstance(name, str):
            return json.dumps(
                {"success": False, "error": "Name must be a non-empty string"},
                ensure_ascii=False,
                indent=2,
            )

        # 使用懒加载获取名称-代码映射
        name_code_map = get_name_code_mapping()
        code = [{n: code} for n, code in name_code_map.items() if name in n]
        if len(code):
            return json.dumps(
                {"success": True, "data": code[0]}, ensure_ascii=False, indent=2
            )
        return json.dumps(
            {"success": False, "error": f"No stock symbol found for name: {name}"},
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        logger.error("Error fetching stock symbol by name: %s", e)
        return json.dumps(
            {
                "success": False,
                "error": f"Error fetching stock symbol by name: {str(e)}",
            },
            ensure_ascii=False,
            indent=2,
        )
