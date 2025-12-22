'''
 # @ Author: Alucard
 # @ Create Time: 2025-12-22 09:21:11
 # @ Modified by: Alucard
 # @ Modified time: 2025-12-22 09:37:27
 # @ Description:
 '''

from typing import Any
from pydantic import BaseModel


class StockDataResponse(BaseModel):
    """Standard response for stock data operations."""

    success: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    metadata: dict[str, Any] | None = None


class SupportResistanceLevel(BaseModel):
    """Single support or resistance level."""
    price: float
    level: int = 1
    distance_from_current: float = 0.0


class SupportResistanceResult(BaseModel):
    """Support and resistance calculation result."""
    supports: list[SupportResistanceLevel]
    resistances: list[SupportResistanceLevel]
    current_price: float
    volatility: float
    price_trend: str
    data_points_analyzed: int
