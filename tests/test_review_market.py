import json
from datetime import date

import pandas as pd
import pytest

from utils import review_market as rm


def _hist_df(day="2026-09-02"):
    return pd.DataFrame(
        {
            "日期": [day],
            "开盘": [3000.0],
            "收盘": [3050.0],
            "最高": [3060.0],
            "最低": [2990.0],
            "成交量": [1.5e9],
            "成交额": [3.5e11],
            "振幅": [2.3],
            "涨跌幅": [1.67],
            "涨跌额": [50.0],
            "换手率": [1.0],
        }
    )


def test_indices_section(monkeypatch):
    monkeypatch.setattr(rm, "index_zh_a_hist", lambda symbol, **kw: _hist_df())
    out = rm.indices_section(date(2026, 9, 2))
    assert len(out["items"]) == 5
    first = out["items"][0]
    assert first["code"] == "000001" and first["name"] == "上证指数"
    assert first["close"] == 3050.0 and first["chg_pct"] == 1.67
    assert first["amount_yi"] == 3500.0


def test_indices_section_mixed(monkeypatch):
    """2 个指数有数据、3 个缺失：返回成功的+notes"""

    def fake(symbol, **kw):
        return (
            _hist_df("2026-09-02")
            if symbol in ("000001", "399001")
            else _hist_df("2026-09-01")
        )

    monkeypatch.setattr(rm, "index_zh_a_hist", fake)
    out = rm.indices_section(date(2026, 9, 2))
    assert len(out["items"]) == 2
    assert len(out["notes"]) == 3


def test_indices_section_all_missing(monkeypatch):
    monkeypatch.setattr(
        rm, "index_zh_a_hist", lambda symbol, **kw: _hist_df("2026-09-01")
    )
    with pytest.raises(ValueError):
        rm.indices_section(date(2026, 9, 2))


def test_indices_section_bad_cell_degrades(monkeypatch):
    """单指数坏值(成交额 None)降级为 note，不杀整个 section"""
    good = _hist_df()
    bad = _hist_df().copy()
    bad["成交额"] = [None]

    def fake(symbol, **kw):
        return bad if symbol == "899050" else good

    monkeypatch.setattr(rm, "index_zh_a_hist", fake)
    out = rm.indices_section(date(2026, 9, 2))
    assert len(out["items"]) == 4
    assert any("899050" in n for n in out["notes"])


def _zt_df():
    return pd.DataFrame(
        {
            "代码": ["003005", "601086"],
            "名称": ["竞业达", "国芳集团"],
            "涨跌幅": [10.0, 10.0],
            "最新价": [20.0, 11.1],
            "成交额": [8.78e7, 6.44e7],
            "换手率": [3.27, 0.87],
            "封板资金": [1.8e8, 3.06e8],
            "炸板次数": [0, 0],
            "涨停统计": ["4/4", "4/4"],
            "连板数": [4, 4],
            "所属行业": ["IT服务Ⅱ", "一般零售"],
        }
    )


def test_breadth_section_history(monkeypatch):
    # 历史日期：三个池有数据、涨跌家数降级
    monkeypatch.setattr(rm, "stock_zt_pool_em", lambda date: _zt_df())
    monkeypatch.setattr(
        rm, "stock_zt_pool_zbgc_em", lambda date: pd.DataFrame(columns=["代码"])
    )
    monkeypatch.setattr(
        rm,
        "stock_zt_pool_dtgc_em",
        lambda date: pd.DataFrame({"代码": ["605179"], "连续跌停": [1]}),
    )
    out = rm.breadth_section(date(2026, 9, 2))
    assert out["limit_up"] == 2 and out["max_lianban"] == 4
    assert out["lianban_dist"] == {"4板": 2}
    assert out["zhaban"] == 0 and out["limit_down"] == 1
    assert out["up"] is None and out["down"] is None
    assert any("仅支持当日" in n for n in out["notes"])


def test_breadth_section_today(monkeypatch):
    class FakeDT:
        @staticmethod
        def now():
            class _T:
                @staticmethod
                def date():
                    return date(2026, 9, 3)

            return _T()

    monkeypatch.setattr(rm, "datetime", FakeDT)
    monkeypatch.setattr(rm, "stock_zt_pool_em", lambda date: _zt_df())
    monkeypatch.setattr(
        rm, "stock_zt_pool_zbgc_em", lambda date: pd.DataFrame(columns=["代码"])
    )
    monkeypatch.setattr(
        rm, "stock_zt_pool_dtgc_em", lambda date: pd.DataFrame(columns=["代码"])
    )
    monkeypatch.setattr(
        rm,
        "ef_spot_all",
        lambda: pd.DataFrame({"代码": ["1", "2", "3"], "涨跌幅": [1.0, -2.0, 0.0]}),
    )
    out = rm.breadth_section(date(2026, 9, 3))
    assert out["up"] == 1 and out["down"] == 1 and out["flat"] == 1


def test_breadth_section_all_empty(monkeypatch):
    empty = pd.DataFrame(columns=["代码"])
    monkeypatch.setattr(rm, "stock_zt_pool_em", lambda date: empty)
    monkeypatch.setattr(rm, "stock_zt_pool_zbgc_em", lambda date: empty)
    monkeypatch.setattr(rm, "stock_zt_pool_dtgc_em", lambda date: empty)
    with pytest.raises(ValueError):
        rm.breadth_section(date(2026, 9, 2))


def test_get_zt_pool(monkeypatch):
    monkeypatch.setattr(rm, "stock_zt_pool_em", lambda date: _zt_df())
    out = rm.get_zt_pool(pool="涨停", date="2026-09-02")
    assert '"success": true' in out and "竞业达" in out


def test_get_zt_pool_bad_pool():
    out = rm.get_zt_pool(pool="飞天", date="2026-09-02")
    assert '"success": false' in out


def test_get_zt_pool_seal_time_fields(monkeypatch):
    df = _zt_df()
    df["首次封板时间"] = ["092500", "093100"]
    df["最后封板时间"] = ["145700", "100000"]
    monkeypatch.setattr(rm, "stock_zt_pool_em", lambda date: df)
    out = json.loads(rm.get_zt_pool("涨停", "2026-09-02"))
    assert out["success"]
    item = out["data"]["items"][0]
    assert item["首次封板时间"] == "092500"
    assert item["最后封板时间"] == "145700"


def test_get_zt_pool_zhaban_first_seal_time(monkeypatch):
    df = _zt_df().rename(columns={"封板资金": "x"})
    df["首次封板时间"] = ["101500", "143000"]
    monkeypatch.setattr(rm, "stock_zt_pool_zbgc_em", lambda date: df)
    out = json.loads(rm.get_zt_pool("炸板", "2026-09-02"))
    assert out["success"]
    assert out["data"]["items"][0]["首次封板时间"] == "101500"
    assert "最后封板时间" not in out["data"]["items"][0]


# ---- 追加到 tests/test_review_market.py（pytest/json/pd/rm 顶部已 import）----


@pytest.fixture(autouse=True)
def _clear_push2_cache():
    """三池/push2 缓存跨用例隔离（对既有用例无副作用）。"""
    from utils.push2_client import CACHE

    CACHE.clear()
    yield
    CACHE.clear()


def _pool_df(n=70):
    return pd.DataFrame(
        {
            "代码": [f"60000{i}" for i in range(n)],
            "名称": [f"股{i}" for i in range(n)],
            "涨跌幅": [10.0] * n,
            "最新价": [10.0 + i for i in range(n)],
            "成交额": [1e8] * n,
            "换手率": [1.0] * n,
            "封板资金": [6.5e8] * n,
            "首次封板时间": ["092500"] * n,
            "最后封板时间": ["093000"] * n,
            "炸板次数": [0] * n,
            "涨停统计": ["1/1"] * n,
            "连板数": [1] * n,
            "所属行业": ["元件" if i % 2 else "半导体" for i in range(n)],
        }
    )


def test_zt_pool_truncated_by_default(monkeypatch):
    monkeypatch.setitem(rm.POOL_FETCHERS, "涨停", lambda date: _pool_df(70))
    out = json.loads(rm.get_zt_pool("涨停", "2026-11-12"))
    assert out["success"] and out["data"]["count"] == 70
    assert len(out["data"]["items"]) == rm.MAX_POOL_ROWS  # 默认截断 60
    assert out["data"]["as_of"] and out["data"]["source"] == "eastmoney_push2ex"


def test_zt_pool_full_returns_all(monkeypatch):
    monkeypatch.setitem(rm.POOL_FETCHERS, "涨停", lambda date: _pool_df(85))
    out = json.loads(rm.get_zt_pool("涨停", "2026-11-12", full=True))
    assert out["data"]["count"] == 85 and len(out["data"]["items"]) == 85


def test_zt_pool_cached_second_call(monkeypatch):
    counter = {"n": 0}

    def fetch(date):
        counter["n"] += 1
        return _pool_df(3)

    monkeypatch.setitem(rm.POOL_FETCHERS, "涨停", fetch)
    rm.get_zt_pool("涨停", "2026-11-12")
    out = json.loads(rm.get_zt_pool("涨停", "2026-11-12"))
    assert counter["n"] == 1 and out["data"]["cached"] is True


def test_zt_industry_counts(monkeypatch):
    monkeypatch.setitem(rm.POOL_FETCHERS, "涨停", lambda date: _pool_df(6))
    counts = rm.zt_industry_counts("2026-11-12")
    assert counts == {"元件": 3, "半导体": 3}


def test_zt_industry_counts_failure_returns_none(monkeypatch):
    def boom(date):
        raise ValueError("upstream down")

    monkeypatch.setitem(rm.POOL_FETCHERS, "涨停", boom)
    assert rm.zt_industry_counts("2026-11-12") is None


# ---- Task5 fupan_v1: 严格日期池快照 / 通用行业分桶 / zhaban_rate ----


def test_pool_full_snapshot_strict_date(monkeypatch):
    """严格日期：当日空池不回退（与 get_zt_pool 的回退行为相反）。"""
    monkeypatch.setitem(rm.POOL_FETCHERS, "涨停", lambda date: _pool_df(3))
    snap = rm.pool_full_snapshot("涨停", date(2026, 11, 12))
    assert snap and snap["count"] == 3 and snap["truncated"] is False
    assert snap["items"][0]["code"].startswith("600")  # 键名已映射为英文
    assert snap["items"][0]["hybk"] in ("元件", "半导体")


def test_pool_full_snapshot_empty_returns_none(monkeypatch):
    monkeypatch.setitem(rm.POOL_FETCHERS, "涨停", lambda date: pd.DataFrame())
    assert rm.pool_full_snapshot("涨停", date(2026, 11, 16)) is None


def test_industry_counts_generic(monkeypatch):
    df = _pool_df(6)  # 元件3 半导体3
    monkeypatch.setitem(rm.POOL_FETCHERS, "涨停", lambda date: df)
    monkeypatch.setitem(rm.POOL_FETCHERS, "炸板", lambda date: df)
    assert rm.industry_counts("涨停", date(2026, 11, 12)) == {"元件": 3, "半导体": 3}
    assert rm.industry_counts("炸板", date(2026, 11, 12)) == {"元件": 3, "半导体": 3}


def test_breadth_zhaban_rate(monkeypatch):
    monkeypatch.setitem(rm.POOL_FETCHERS, "涨停", lambda date: _pool_df(10))
    monkeypatch.setitem(rm.POOL_FETCHERS, "炸板", lambda date: _pool_df(2))
    monkeypatch.setitem(rm.POOL_FETCHERS, "跌停", lambda date: pd.DataFrame())
    out = rm.breadth_section(date(2026, 11, 12))
    assert out["limit_up"] == 10 and out["zhaban"] == 2
    assert abs(out["zhaban_rate"] - round(2 / 12 * 100, 2)) < 1e-9
