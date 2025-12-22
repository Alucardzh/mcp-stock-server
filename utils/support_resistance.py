"""
股票支撑位和压力位计算模块
用于股票技术分析中的支撑位和压力位计算
"""

import numpy as np  # 导入 numpy 库，用于数值计算
import pandas as pd  # 导入 pandas 库，用于数据处理
# 从 schema 模块导入支撑位和压力位的数据模型
from .schema import SupportResistanceLevel, SupportResistanceResult


def calculate_support_resistance(
    prices: pd.Series,
    n_levels: int = 5,
    lookback_period: int = 60
) -> dict[str, list[float]]:
    """
    计算支撑位和压力位

    参数：
    - prices: 价格序列（通常用收盘价）
    - n_levels: 要识别的关键水平数量（默认5个支撑+5个压力）
    - lookback_period: 分析的回看周期

    返回：
    - 包含支撑位和压力位的字典
    """
    if len(prices) < lookback_period:
        lookback_period = len(prices)

    # 使用最近的数据
    recent_prices = prices.iloc[-lookback_period:]

    # 确保转换为 numpy 数组
    price_array = np.asarray(recent_prices, dtype=float)

    # 1. 将价格范围分成多个分位点
    price_min = float(np.min(price_array))
    price_max = float(np.max(price_array))
    price_range = price_max - price_min

    # 2. 使用分位数识别关键价格区域
    # 支撑位：价格在底部区域停留的时间较长
    # 压力位：价格在顶部区域停留的时间较长
    n_bins = 100  # 将价格分成100个区间
    hist, bin_edges = np.histogram(price_array, bins=n_bins)

    # 3. 找到成交量（停留时间）最高的几个区域
    # 这里用价格停留的频率代替成交量
    sorted_indices = np.argsort(hist)[::-1]  # 降序排列

    # 4. 识别支撑位（底部高停留区域）
    support_candidates = []
    resistance_candidates = []

    for idx in sorted_indices[:n_levels * 3]:  # 取前3倍候选
        price_level = (bin_edges[idx] + bin_edges[idx + 1]) / 2

        # 判断是支撑还是压力候选
        price_percentile = (price_level - price_min) / price_range

        if price_percentile < 0.4:  # 底部40%区域
            support_candidates.append({
                'price': price_level,
                'strength': hist[idx] / len(recent_prices),  # 频率作为强度
                'frequency': hist[idx]
            })
        elif price_percentile > 0.6:  # 顶部40%区域
            resistance_candidates.append({
                'price': price_level,
                'strength': hist[idx] / len(recent_prices),
                'frequency': hist[idx]
            })

    # 5. 对候选点进行聚类，避免太接近的水平
    def cluster_levels(candidates: list[dict], n_target: int,
                       merge_threshold: float = 0.02) -> list[float]:
        """聚类相近的价格水平"""
        if not candidates:
            return []

        # 按强度排序
        candidates.sort(key=lambda x: x['strength'], reverse=True)

        clusters = []
        for candidate in candidates:
            price = candidate['price']
            strength = candidate['strength']

            # 检查是否与已有聚类接近
            added_to_existing = False
            for cluster in clusters:
                cluster_price = cluster['price']
                # 如果价格在阈值范围内，则合并
                if abs(price - cluster_price) / cluster_price < merge_threshold:
                    # 加权平均更新聚类价格
                    total_strength = cluster['strength'] + strength
                    cluster['price'] = (
                        cluster['price'] * cluster['strength'] + price * strength) / total_strength
                    cluster['strength'] = total_strength
                    cluster['count'] += 1
                    added_to_existing = True
                    break

            if not added_to_existing:
                clusters.append({
                    'price': price,
                    'strength': strength,
                    'count': 1
                })

        # 按强度排序并返回价格
        clusters.sort(key=lambda x: x['strength'], reverse=True)
        return [cluster['price'] for cluster in clusters[:n_target]]

    # 6. 获取最终水平
    support_levels = cluster_levels(support_candidates, n_levels)
    resistance_levels = cluster_levels(resistance_candidates, n_levels)

    # 7. 确保支撑位低于当前价格，压力位高于当前价格
    current_price = prices.iloc[-1]

    support_levels = [s for s in support_levels if s <
                      current_price * 1.01]  # 允许1%的误差
    resistance_levels = [
        r for r in resistance_levels if r > current_price * 0.99]

    # 添加额外的分析指标
    current_price_float = float(current_price)
    # 计算年化波动率：价格变化百分比的标准差乘以 252 的平方根（一年交易日数）
    volatility = float(prices.pct_change().std() * np.sqrt(252))

    # 确定价格趋势
    if lookback_period < len(prices):  # 如果回看周期小于数据长度
        # 如果当前价格高于回看周期前的价格，趋势为上涨，否则为下跌
        price_trend = "up" if prices.iloc[-1] > prices.iloc[-lookback_period] else "down"
    else:  # 如果回看周期大于等于数据长度
        price_trend = "neutral"  # 趋势为中性
        # 创建支撑位对象列表
    support_level_objects = []  # 初始化支撑位对象列表
    # 遍历所有基础支撑位，i 为索引，price 为价格
    for i, price in enumerate(sorted(support_levels, reverse=True)):
        level = SupportResistanceLevel(  # 创建支撑位级别对象
            price=float(price),  # 支撑位价格，转换为浮点数
            level=i + 1,  # 支撑位级别（从 1 开始）
            distance_from_current=current_price_float -
            float(price)  # 距离当前价格的距离（当前价格减去支撑位价格）
        )
        support_level_objects.append(level)  # 将支撑位对象添加到列表

    # 创建压力位对象列表
    resistance_level_objects = []  # 初始化压力位对象列表
    for i, price in enumerate(sorted(resistance_levels)):  # 遍历所有基础压力位，i 为索引，price 为价格
        level = SupportResistanceLevel(  # 创建压力位级别对象
            price=float(price),  # 压力位价格，转换为浮点数
            level=i + 1,  # 压力位级别（从 1 开始）
            distance_from_current=float(
                price) - current_price_float  # 距离当前价格的距离（压力位价格减去当前价格）
        )
        resistance_level_objects.append(level)  # 将压力位对象添加到列表
    result = SupportResistanceResult(  # 创建支撑位和压力位结果对象
        supports=support_level_objects,  # 支撑位对象列表
        resistances=resistance_level_objects,  # 压力位对象列表
        current_price=float(current_price),  # 当前价格
        volatility=volatility,  # 年化波动率
        price_trend=price_trend,  # 价格趋势（上涨/下跌/中性）
        data_points_analyzed=len(prices)  # 分析的数据点数量
    )
    return result.model_dump()
