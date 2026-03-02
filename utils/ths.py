"""
# @ Author: Alucard
# @ Create Time: 2025-12-22 11:51:09
# @ Modified by: Alucard
# @ Modified time: 2025-12-22 11:55:34
# @ Description:
"""

__all__ = ["get_ths_hot_list"]


import json

import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36",
}


def get_ths_hot_list(span: str, limit: int) -> str:
    """
    span: hour  近1小时榜
        day   今日榜
    """
    url = "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock"
    params = {
        "stock_type": "a",
        "type": span,  # hour / day
        "list_type": "normal",
    }
    headers = {
        **HEADERS,
        "Referer": "https://www.10jqka.com.cn/",
    }

    r = requests.get(url, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()

    if data.get("status_code") == 0:
        stock_list = data.get("data", {}).get("stock_list", [])
        records = [
            {
                "热榜排名": item["order"],
                "代码": item["code"],
                "名称": item["name"],
                "热度": item["rate"],
                "涨幅": item.get("rise_and_fall", 0),
                "标签": ",".join(item["tag"].get("concept_tag", [])),
                "上榜标签": item["tag"].get("popularity_tag", ""),
                "上榜原因": f"{item.get('analyse_title', '')} {item.get('analyse', '')}",
            }
            for item in stock_list
        ]
        return json.dumps(records[:limit], ensure_ascii=False)
    return json.dumps([], ensure_ascii=False)
