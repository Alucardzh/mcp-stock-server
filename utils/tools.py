#!/usr/bin/env python3
"""
MCP Akshare Stock Data Server using FastMCP.

This module provides MCP-compliant server for Chinese stock data analysis
using Akshare API and FastMCP framework following PEP 723 standards.
"""
__all__ = ["get_stock_history", "get_stock_realtime", "get_stock_basic",
           "calculate_support_resistance_func", "get_market_index",
           "get_stock_symbol_by_name"]
import json
import logging
import pandas as pd
from akshare import (stock_zh_a_hist, stock_zh_a_spot_em, stock_info_a_code_name,
                     stock_zh_index_spot_em, stock_individual_info_em,)
from .validate import validate_stock_symbol, parse_date_range, format_stock_data
from .support_resistance import calculate_support_resistance
# Import our analysis modules


# Configure logging
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)
NAME_CODE = stock_info_a_code_name()
NAME_CODE = dict(zip(NAME_CODE['name'], NAME_CODE['code']))


def get_stock_history(
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
    period: str = "daily",
    adjust: str = "qfq"
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
            start_date, end_date, default_period_days=90)

        logger.info("Fetching historical data for %s", symbol)

        # Format dates for akshare
        start_date_formatted = start_date.replace(
            "-", "") if start_date else ""
        end_date_formatted = end_date.replace("-", "") if end_date else ""

        # Fetch data from Akshare
        data = stock_zh_a_hist(
            symbol=symbol,
            period=period,
            start_date=start_date_formatted,
            end_date=end_date_formatted,
            adjust=adjust
        )

        if data is None or data.empty:
            return json.dumps({
                "success": False,
                "error": f"No historical data found for symbol {symbol}"
            }, ensure_ascii=False, indent=2)

        # Format response
        response = format_stock_data(data)

        if response.success:
            return json.dumps(response.model_dump(exclude_none=True), ensure_ascii=False, indent=2)
        return json.dumps({
            "success": False,
            "error": response.error
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error("Error fetching stock history: %s", e)
        return json.dumps({
            "success": False,
            "error": f"Error fetching stock history: {str(e)}"
        }, ensure_ascii=False, indent=2)


def get_stock_realtime(symbol: str) -> str:
    """Get real-time stock data for a Chinese stock.

    Args:
        symbol: 6-digit stock symbol (e.g., '000001')
    """
    try:
        symbol = validate_stock_symbol(symbol)

        logger.info("Fetching real-time data for %s", symbol)

        # Fetch data from Akshare
        data = stock_zh_a_spot_em()

        if data is None or data.empty:
            return json.dumps({
                "success": False,
                "error": f"No real-time data found for symbol {symbol}"
            }, ensure_ascii=False, indent=2)

        # Find the specific stock
        stock_data = data[data['代码'] == symbol]
        if stock_data.empty:
            return json.dumps({
                "success": False,
                "error": f"Stock {symbol} not found in real-time data"
            }, ensure_ascii=False, indent=2)

        # Format response
        result = {
            "success": True,
            "data": {
                "symbol": symbol,
                "name": (stock_data['名称'].iloc[0]
                         if not stock_data['名称'].empty else None),
                "current_price": (float(stock_data['最新价'].iloc[0])
                                  if '最新价' in stock_data.columns else None),
                "change": (float(stock_data['涨跌额'].iloc[0])
                           if '涨跌额' in stock_data.columns else None),
                "change_percent": (float(stock_data['涨跌幅'].iloc[0])
                                   if '涨跌幅' in stock_data.columns else None),
                "volume": (int(stock_data['成交量'].iloc[0])
                           if '成交量' in stock_data.columns else None),
                "amount": (float(stock_data['成交额'].iloc[0])
                           if '成交额' in stock_data.columns else None),
                "update_time": pd.Timestamp.now().isoformat(),
                "source": "akshare_realtime"
            }
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error("Error fetching real-time data: %s", e)
        return json.dumps({
            "success": False,
            "error": f"Error fetching real-time data: {str(e)}"
        }, ensure_ascii=False, indent=2)


def get_stock_basic(symbol: str) -> str:
    """Get basic information about a Chinese stock.

    Args:
        symbol: 6-digit stock symbol (e.g., '000001')
    """
    try:
        symbol = validate_stock_symbol(symbol)

        logger.info("Fetching basic info for %s", symbol)

        # Fetch individual stock info from Akshare
        # stock_individual_info_em returns detailed information including industry, company info, etc.
        data = stock_individual_info_em(symbol=symbol)

        if data is None or data.empty:
            return json.dumps({
                "success": False,
                "error": f"No basic data found for symbol {symbol}"
            }, ensure_ascii=False, indent=2)
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
                "source": "akshare"
            }
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error("Error fetching basic info: %s", e)
        return json.dumps({
            "success": False,
            "error": f"Error fetching basic info: {str(e)}"
        }, ensure_ascii=False, indent=2)


def calculate_support_resistance_func(
    symbol: str,
    n_levels: int = 5,
    lookback_period: int = 60
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
        start_date, end_date = parse_date_range(
            None, None, default_period_days=180)
        start_date_formatted = start_date.replace("-", "")
        end_date_formatted = end_date.replace("-", "")

        hist_data = stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start_date_formatted,
            end_date=end_date_formatted,
            adjust="qfq"
        )
        if hist_data is None or hist_data.empty:
            return json.dumps({
                "success": False,
                "error": f"No historical data found for {symbol} to calculate support/resistance"
            }, ensure_ascii=False, indent=2)

        # Calculate support and resistance
        # Use correct column name for closing price
        close_col = '收盘' if '收盘' in hist_data.columns else 'close'
        result = calculate_support_resistance(
            prices=hist_data[close_col],
            n_levels=n_levels,
            lookback_period=min(lookback_period, len(hist_data))
        )

        # Add additional metadata
        result.update({
            "success": True,
            "symbol": symbol,
            "calculation_date": pd.Timestamp.now().isoformat(),
            "parameters": {
                "n_levels": n_levels,
                "lookback_period": lookback_period
            }
        })

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error("Error calculating support/resistance: %s", e)
        return json.dumps({
            "success": False,
            "error": f"Error calculating support/resistance: {str(e)}"
        }, ensure_ascii=False, indent=2)


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
            return json.dumps({
                "success": False,
                "error": f"No index data found for code {index_code}"
            }, ensure_ascii=False, indent=2)

        # Find the specific index
        index_data = data[data['代码'] == index_code]
        if index_data.empty:
            return json.dumps({
                "success": False,
                "error": f"Index {index_code} not found in index data"
            }, ensure_ascii=False, indent=2)

        # Format response
        if len(index_data) > 0:
            index = index_data.iloc[0]
            result = {
                "success": True,
                "data": {
                    "code": index_code,
                    "name": index.get('名称', 'Unknown'),
                    "current_point": float(index['最新价']) if '最新价' in index else None,
                    "change": float(index['涨跌额']) if '涨跌额' in index else None,
                    "change_percent": float(index['涨跌幅']) if '涨跌幅' in index else None,
                    "volume": int(index['成交量']) if '成交量' in index else None,
                    "amount": float(index['成交额']) if '成交额' in index else None,
                    "update_time": pd.Timestamp.now().isoformat(),
                    "source": "akshare_index"
                }
            }

            return json.dumps(result, ensure_ascii=False, indent=2)
        return json.dumps({
            "success": False,
            "error": f"No data returned for index {index_code}"
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error("Error fetching index data: %s", e)
        return json.dumps({
            "success": False,
            "error": f"Error fetching index data: {str(e)}"
        }, ensure_ascii=False, indent=2)


def get_stock_symbol_by_name(name: str) -> str:
    """通过名称获取股票代码"""
    try:
        code = [{n: code} for n, code in NAME_CODE.items() if name in n]
        if len(code):
            return json.dumps({
                "success": True,
                "data": code[0]
            }, ensure_ascii=False, indent=2)
        return json.dumps({
            "success": False,
            "error": f"No stock symbol found for name: {name}"
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Error fetching stock symbol by name: %s", e)
        return json.dumps({
            "success": False,
            "error": f"Error fetching stock symbol by name: {str(e)}"
        }, ensure_ascii=False, indent=2)
