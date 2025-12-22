'''
 # @ Author: Alucard
 # @ Create Time: 2025-12-22 12:30:42
 # @ Modified by: Alucard
 # @ Modified time: 2025-12-22 12:31:14
 # @ Description:
'''
__all__ = ['StockCal']

import json
from datetime import datetime, timedelta
import pandas as pd
from akshare import stock_zt_pool_em, tool_trade_date_hist_sina, stock_zh_a_hist
from .ths import get_ths_hot_list
from .schema import StockCalLimit


def get_last_trade_date() -> str:
    """获取最新的交易日"""
    trade_calendar = tool_trade_date_hist_sina()
    today_date = datetime.today().date()
    trade_dates = trade_calendar.trade_date.values.tolist()
    is_trade = today_date in trade_dates
    # 找到小于等于今天的最大交易日
    trade_calendar = trade_calendar[
        trade_calendar.trade_date <= today_date
    ].trade_date.values.tolist()
    now_time = datetime.now().strftime("%H%M")
    trade_date = trade_calendar[-1 if is_trade else -
                                2 if int(now_time) < 930 else -1]
    return trade_date.strftime("%Y%m%d")


class StockCal:
    """个人选股计算方法"""
    def __init__(self, data: StockCalLimit) -> None:
        self.limit = data.limit
        self.span = data.span
        self.total_market_value = data.total_market_value
        self.has_front = data.has_front
        self.basic_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0 Safari/537.36",
        }

    @staticmethod
    def _cal(val: str) -> float:
        a, b = val.split("/")
        return int(b) / int(a)

    @staticmethod
    def _get_lbhy(df: pd.DataFrame):
        """获取连板票行业

        Args:
            df (pd.DataFrame): _description_

        Returns:
            _type_: _description_
        """
        df = df.sort_values(by=["连板数"], ascending=[False])
        df = df.drop_duplicates(subset=["所属行业"], keep="first")
        return df

    def get_limit_list(self) -> pd.DataFrame:
        """统计筛选涨停池

        Returns:
            pd.DataFrame: _description_
        """
        trade_date = get_last_trade_date()
        df = stock_zt_pool_em(date=trade_date)  # 东方财富-涨停股池
        if df.shape[0]:
            df.涨停统计 = df.涨停统计.apply(lambda x: self._cal(x))
            if self.has_front is False:
                df = self._get_lbhy(df)
            df = df[
                (df["连板数"] >= 2)
                & (df["涨停统计"] >= 0.666)
                & (df["流通市值"] <= self.total_market_value * 100000000)
            ]
            df = df[~(df.最后封板时间 <= "093010")]
            if df.shape[0]:
                return df
        return pd.DataFrame([])

    def get_basic_list(self) -> pd.DataFrame:
        """整理数据

        Returns:
            pd.DataFrame: _description_
        """
        df1 = get_ths_hot_list(span=self.span, limit=self.limit)
        df2 = self.get_limit_list()
        if df1 and df2.shape[0]:
            df1 = pd.DataFrame(json.loads(df1))
            merged_df = pd.merge(df1, df2, on=[
                                 "代码", "名称"], how="inner")
            if merged_df.shape[0]:
                merged_df = merged_df[
                    [
                        "代码",
                        "名称",
                        "热度",
                        "涨幅",
                        "标签",
                        "成交额",
                        "流通市值",
                        "总市值",
                        "换手率",
                        "封板资金",
                        "首次封板时间",
                        "最后封板时间",
                        "炸板次数",
                        "涨停统计",
                        "连板数",
                        "所属行业",
                    ]
                ]
                merged_df.总市值 = merged_df.总市值.astype(float) / 100000000
                merged_df.流通市值 = merged_df.流通市值.astype(float) / 100000000
                merged_df.成交额 = merged_df.成交额.astype(float) / 100000000
                merged_df.封板资金 = merged_df.封板资金.astype(float) / 100000000
                merged_df = merged_df.rename(
                    columns={
                        "成交额": "成交额(亿元)",
                        "流通市值": "流通市值(亿元)",
                        "总市值": "总市值(亿元)",
                        "换手率": "换手率(%)",
                        "封板资金": "封板资金(亿元)",
                    }
                )
                codes = merged_df["代码"].values.tolist()
                high_prices = [
                    {"代码": code, **self.get_high_price(symbol=code)}
                    for code in codes
                ]
                df = pd.DataFrame(high_prices)
                merged_df = pd.merge(merged_df, df, on=["代码"], how="inner")
                merged_df = merged_df.fillna(0)
                return merged_df
        return pd.DataFrame([])

    def get_high_price(self, symbol: str) -> dict:
        """查询最高价

        Args:
            symbol (str): _description_

        Returns:
            dict: _description_
        """
        end_day = datetime.today()
        year_start = datetime(end_day.year, 1, 1)
        day_180 = end_day - timedelta(days=180)
        day_365 = end_day - timedelta(days=365)

        # 2. 拉取日线（前复权）
        df = stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=day_365.strftime("%Y%m%d"),
            end_date=end_day.strftime("%Y%m%d"),
            adjust="qfq",
        )

        # 3. 把日期列转成 datetime64[ns]
        df["日期"] = pd.to_datetime(df["日期"])

        # 4. 计算三段区间的最高价
        high_365 = df["最高"].max()
        high_ytd = df.loc[df["日期"] >= pd.Timestamp(
            year_start.date()), "最高"].max()
        high_180 = df.loc[df["日期"] >= pd.Timestamp(day_180.date()), "最高"].max()
        return {
            "当前价格": df.iloc[-1]["收盘"],
            "近一年最高": high_365,
            "今年最高": high_ytd,
            "半年最高": high_180,
        }

    def get_daily_code_data(self) -> str:
        """
        更新每日股票数据
        """
        df = self.get_basic_list()
        if df.shape[0]:
            message = "数据更新成功"
            data = df.to_dict('records')
        else:
            now = datetime.now().strftime("%H%M")
            message = "没有获取到数据"
            data = []
            if int(now) in range(1530, 1536):
                message = "数据获取中,请15:35之后再试"
        return json.dumps({"message": message, "data": data}, ensure_ascii=False)
