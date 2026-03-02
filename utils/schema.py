"""
# @ Author: Alucard
# @ Create Time: 2025-12-22 09:21:11
# @ Modified by: Alucard
# @ Modified time: 2025-12-22 09:37:27
# @ Description:
"""

from typing import Any

from pydantic import BaseModel, Field


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


class StockCalLimit(BaseModel):
    """个人方法计算所需参数"""

    limit: int = Field(
        default=100, ge=1, le=100, description="返回股票数量限制，范围1-100"
    )
    span: str = Field(
        default="hour",
        pattern="^(day|hour)$",
        description="时间跨度，只能是'day'(今日榜)或'hour'(近1小时榜)",
    )
    total_market_value: int = Field(
        default=200, ge=200, description="流通市值上限（亿元），最小值200"
    )
    has_front: bool = Field(
        default=False, description="是否包含前排股，True表示包含，False表示不包含"
    )
