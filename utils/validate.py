"""
# @ Author: Alucard
# @ Create Time: 2025-12-22 09:22:25
# @ Modified by: Alucard
# @ Modified time: 2025-12-22 09:37:34
# @ Description:
"""

__all__ = [
    "validate_stock_symbol",
    "parse_date_range",
    "format_stock_data",
    "NAME_CODE",
]

from os import getenv
import logging
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import akshare_proxy_patch

akshare_proxy_patch.install_patch(
    "101.201.173.125",
    auth_token=getenv("AKPROXY_TOKEN", ""),
    retry=30,
    # 封控的域名列表，可自行调整
    hook_domains=[
      "fund.eastmoney.com",
      "push2.eastmoney.com",
      "push2his.eastmoney.com",
      "emweb.securities.eastmoney.com",
      "searchapi.eastmoney.com/api/suggest/get"
    ],
    fast=True
)

from akshare import stock_info_a_code_name

from .schema import StockDataResponse

# 使用懒加载模式，避免模块导入时执行网络请求
_NAME_CODE: dict[str, str] | None = None


def get_name_code_mapping() -> dict[str, str]:
    """懒加载股票名称到代码的映射

    Returns:
        股票名称到代码的字典映射

    Raises:
        ConnectionError: 当网络请求失败时
    """
    global _NAME_CODE

    # 如果已经加载过，直接返回缓存
    if _NAME_CODE is not None:
        return _NAME_CODE

    # 首次调用时加载数据
    logger.info("Loading stock name-code mapping...")
    try:
        df = stock_info_a_code_name()
        _NAME_CODE = dict(zip(df["name"], df["code"]))
        logger.info("Loaded %d stock name-code mappings", len(_NAME_CODE))
        return _NAME_CODE
    except Exception as e:
        logger.error("Failed to load stock name-code mapping: %s", e)
        raise ConnectionError(
            f"Failed to load stock name-code mapping from akshare: {str(e)}"
        ) from e


# 保持向后兼容，通过属性访问
@property
def NAME_CODE() -> dict[str, str]:
    """获取股票名称到代码的映射（懒加载）

    为了向后兼容保留的属性访问方式。
    建议直接使用 get_name_code_mapping() 函数。
    """
    return get_name_code_mapping()


# Configure logging
logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def validate_stock_symbol(symbol: str) -> str:
    """Validate and normalize stock symbol.

    Args:
        symbol: 股票代码或名称

    Returns:
        验证后的6位股票代码

    Raises:
        ValueError: 当股票代码或名称无效时
        ConnectionError: 当无法获取股票名称映射时
    """
    if not symbol or not isinstance(symbol, str):
        raise ValueError("Stock symbol is required and must be a string")

    # Remove whitespace and convert to uppercase
    symbol = symbol.strip().upper()

    # Basic validation for Chinese stock symbols (6 digits)
    if not (len(symbol) == 6 and symbol.isdigit()):
        # 尝试从名称映射中查找
        name_code_map = get_name_code_mapping()
        code = [code for n, code in name_code_map.items() if symbol in n]
        if len(code):
            return code[0]
        else:
            raise ValueError(
                "Invalid stock symbol format. Expected 6 digits for A-share stocks"
            )
    return symbol


def parse_date_range(
    start_date: str | None = None,
    end_date: str | None = None,
    default_period_days: int = 30,
) -> tuple[str, str]:
    """Parse and validate date range."""
    # Default to last N days if no dates provided
    if not start_date and not end_date:
        end_date_obj = date.today()
        start_date_obj = end_date_obj - timedelta(days=default_period_days)
        return start_date_obj.strftime("%Y-%m-%d"), end_date_obj.strftime("%Y-%m-%d")

    # Parse dates
    start_date_obj = None
    end_date_obj = None

    if start_date:
        try:
            start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError(
                f"Invalid start_date format. Use YYYY-MM-DD, got: {start_date}"
            ) from exc

    if end_date:
        try:
            end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError(
                f"Invalid end_date format. Use YYYY-MM-DD, got: {end_date}"
            ) from exc
    if not end_date_obj:
        end_date_obj = date.today()
    # Set defaults
    if not start_date_obj:
        start_date_obj = end_date_obj - timedelta(days=default_period_days)
    return start_date_obj.strftime("%Y-%m-%d"), end_date_obj.strftime("%Y-%m-%d")


def format_stock_data(data: Any, source: str = "akshare") -> StockDataResponse:
    """Format stock data into standardized response."""
    if data is None:
        return StockDataResponse(success=False, error="No data available")

    try:
        # Handle different data types
        if hasattr(data, "to_dict"):
            # Convert DataFrame to dict, handling date/datetime columns
            df_copy = data.copy()
            # Convert date/datetime columns to strings
            for col in df_copy.columns:
                if pd.api.types.is_datetime64_any_dtype(df_copy[col]):
                    df_copy[col] = df_copy[col].astype(str)
                elif pd.api.types.is_object_dtype(df_copy[col]):
                    # Check if values are date/datetime objects
                    try:
                        sample_val = (
                            df_copy[col].dropna().iloc[0]
                            if not df_copy[col].dropna().empty
                            else None
                        )
                        if sample_val and isinstance(sample_val, (date, datetime)):
                            df_copy[col] = df_copy[col].apply(
                                lambda x: str(x) if pd.notna(x) else None
                            )
                    except (IndexError, AttributeError):
                        pass
            df_dict = df_copy.to_dict("records")
            return StockDataResponse(
                success=True,
                data={"records": df_dict, "count": len(df_dict), "source": source},
            )
        elif isinstance(data, list):
            return StockDataResponse(
                success=True,
                data={"records": data, "count": len(data), "source": source},
            )
        elif isinstance(data, dict):
            result_data = data.copy()
            result_data["source"] = source
            return StockDataResponse(success=True, data=result_data)
        else:
            return StockDataResponse(
                success=True, data={"value": data, "source": source}
            )

    except Exception as e:
        logger.error("Failed to format stock data: %s", e)
        return StockDataResponse(
            success=False, error=f"Failed to format stock data: {str(e)}"
        )
