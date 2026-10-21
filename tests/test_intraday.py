import json
from datetime import date, datetime

import pandas as pd
import pytest

from utils import intraday as itd


def _min_df():
    times = [f"2026-09-02 09:{m:02d}" for m in range(31, 59)]
    times += [f"2026-09-02 10:{m:02d}" for m in range(10)]
    n = len(times)
    return pd.DataFrame(
        {
            "日期": times,
            "股票名称": ["新华传媒"] * n,
            "开盘": [10.0] * n,
            "收盘": [10.5] * n,
            "最高": [11.0] * n,
            "最低": [9.9] * n,
            "成交量": [1000.0] * n,
            "成交额": [1.05e7] * n,
        }
    )


def _fake_hist(symbol, period="daily", start_date="", end_date=""):
    if period == "1":
        return _min_df()
    return pd.DataFrame({"日期": ["2026-09-01"], "收盘": [10.0]})


def _no_min(symbol, period="daily", start_date="", end_date=""):
    if period == "1":
        return pd.DataFrame()
    return pd.DataFrame({"日期": ["2026-09-01"], "收盘": [10.0]})


@pytest.fixture(autouse=True)
def _no_akshare(monkeypatch):
    monkeypatch.setattr(itd, "stock_zh_a_hist_min_em", lambda **kw: pd.DataFrame())


def test_limit_price_rules():
    assert itd.limit_price(10.0, "600825", "新华传媒") == 11.0
    assert itd.limit_price(10.0, "300045", "华力创通") == 12.0
    assert itd.limit_price(10.0, "920006", "晟楠科技") == 13.0
    assert itd.limit_price(10.0, "000573", "ST宏远") == 10.5
    assert itd.limit_price(None, "600825", "x") is None


def test_intraday_summary(monkeypatch):
    monkeypatch.setattr(itd, "ef_stock_hist", _fake_hist)
    out = json.loads(itd.get_stock_intraday("600825", "1", "summary", "2026-09-02"))
    assert out["success"]
    d = out["data"]
    assert d["open"] == 10.0
    assert d["limit_price"] == 11.0
    assert d["limit_touch"]["count"] == 38
    assert d["limit_touch"]["first"] == "09:31"
    assert d["open_30min"]["amount_yi"] == pytest.approx(3.15, abs=0.01)


def test_intraday_full_mode(monkeypatch):
    monkeypatch.setattr(itd, "ef_stock_hist", _fake_hist)
    out = json.loads(itd.get_stock_intraday("600825", "1", "full", "2026-09-02"))
    assert out["success"]
    assert len(out["data"]["bars"]) == 38
    assert out["data"]["bars"][0]["time"] == "09:31"


def test_intraday_1min_history_degrades(monkeypatch):
    monkeypatch.setattr(itd, "ef_stock_hist", _no_min)
    out = json.loads(itd.get_stock_intraday("600825", "1", "summary", "2026-09-01"))
    assert not out["success"]
    assert "period=5" in out["error"]


def test_intraday_bad_args(monkeypatch):
    monkeypatch.setattr(itd, "ef_stock_hist", _fake_hist)
    assert not json.loads(itd.get_stock_intraday("600825", "3"))["success"]
    assert not json.loads(itd.get_stock_intraday("600825", "1", "x"))["success"]
    assert not json.loads(itd.get_stock_intraday("600825", "1", "summary", "bad"))["success"]


def test_auction_live_then_archive(monkeypatch, tmp_path):
    monkeypatch.setattr(itd, "AUCTION_ARCHIVE_ROOT", tmp_path)
    monkeypatch.setattr(
        itd,
        "_fetch_deal_details",
        lambda s: (
            "10.0",
            [
                "09:15:00,10.1,9,0,4",
                "09:19:30,10.2,50,0,4",
                "09:21:00,10.3,80,0,4",
                "09:25:00,10.4,120,0,4",
                "09:30:00,10.4,130,0,1",
            ],
        ),
    )
    out = json.loads(itd.get_stock_auction("600825", "", "summary"))
    d = out["data"]
    assert d["source"] == "live"
    assert d["match_price"] == 10.4
    assert d["match_vol_hand"] == 120
    assert d["track"]["0920_0925"]["cum_vol"] == 80
    assert d["open_gap_pct"] == 4.0

    # 二次调用：detail 走归档，不再触发网络
    calls = []

    def _no_fetch(s):
        calls.append(1)
        return ("10.0", [])

    monkeypatch.setattr(itd, "_fetch_deal_details", _no_fetch)
    out2 = json.loads(itd.get_stock_auction("600825", "", "detail"))
    assert out2["data"]["source"] == "archive"
    assert not calls
    assert out2["data"]["items"][0]["方向"] == "4"
    assert len(out2["data"]["items"]) == 4


def test_auction_history_needs_archive(monkeypatch, tmp_path):
    monkeypatch.setattr(itd, "AUCTION_ARCHIVE_ROOT", tmp_path)
    out = json.loads(itd.get_stock_auction("600825", "2026-09-02"))
    assert not out["success"]
    assert "归档" in out["error"]


def test_auction_partial_live_never_archived(monkeypatch, tmp_path):
    """盘中半截快照（未到 09:25）永不落盘：每次调用都走 live。"""
    monkeypatch.setattr(itd, "AUCTION_ARCHIVE_ROOT", tmp_path)
    monkeypatch.setattr(
        itd,
        "_fetch_deal_details",
        lambda s: ("10.0", ["09:15:00,10.1,9,0,4", "09:19:30,10.2,50,0,4", "09:21:00,10.3,80,0,4"]),
    )
    today = datetime.now().date()
    out1 = json.loads(itd.get_stock_auction("600825", "", "summary"))
    assert out1["success"]
    assert out1["data"]["source"] == "live"
    out2 = json.loads(itd.get_stock_auction("600825", "", "summary"))
    assert out2["success"]
    assert out2["data"]["source"] == "live"
    assert not itd._archive_path("600825", today).exists()


def test_auction_complete_fetch_archived(monkeypatch, tmp_path):
    """完整竞价（末行 09:25:00）落盘，二次调用读归档。"""
    monkeypatch.setattr(itd, "AUCTION_ARCHIVE_ROOT", tmp_path)
    monkeypatch.setattr(
        itd,
        "_fetch_deal_details",
        lambda s: ("10.0", ["09:15:00,10.1,9,0,4", "09:25:00,10.4,120,0,4"]),
    )
    today = datetime.now().date()
    out1 = json.loads(itd.get_stock_auction("600825", "", "summary"))
    assert out1["data"]["source"] == "live"
    assert itd._archive_path("600825", today).exists()
    out2 = json.loads(itd.get_stock_auction("600825", "", "detail"))
    assert out2["data"]["source"] == "archive"
    assert out2["data"]["items"][-1]["时间"] == "09:25:00"


def test_auction_stale_partial_archive_ignored_for_today(monkeypatch, tmp_path):
    """当日已存在的半截归档（旧版遗留）不得被当作最终结果冻结。"""
    monkeypatch.setattr(itd, "AUCTION_ARCHIVE_ROOT", tmp_path)
    today = datetime.now().date()
    itd._write_archive("600825", today, [["09:15:00", "10.1", "9", "0", "4"], ["09:21:00", "10.3", "80", "0", "4"]])
    monkeypatch.setattr(
        itd,
        "_fetch_deal_details",
        lambda s: ("10.0", ["09:15:00,10.1,9,0,4", "09:25:00,10.4,120,0,4"]),
    )
    out = json.loads(itd.get_stock_auction("600825", "", "summary"))
    assert out["data"]["source"] == "live"
    assert out["data"]["match_price"] == 10.4  # 未被半截归档的 10.3 冻结
    # 完整数据回写覆盖半截归档
    rows = itd._read_archive("600825", today)
    assert rows[-1][0] == "09:25:00"


def test_auction_history_partial_archive_served(monkeypatch, tmp_path):
    """历史日期的半截归档仍作为最优可用数据返回。"""
    monkeypatch.setattr(itd, "AUCTION_ARCHIVE_ROOT", tmp_path)
    itd._write_archive("600825", date(2026, 9, 2), [["09:15:00", "10.1", "9", "0", "4"]])
    out = json.loads(itd.get_stock_auction("600825", "2026-09-02", "detail"))
    assert out["success"]
    assert out["data"]["source"] == "archive"
    assert len(out["data"]["items"]) == 1


def test_write_archive_atomic_no_tmp_leftover(monkeypatch, tmp_path):
    """原子写：完成后目录中无 .tmp 残留。"""
    monkeypatch.setattr(itd, "AUCTION_ARCHIVE_ROOT", tmp_path)
    itd._write_archive("600825", date(2026, 9, 2), [["09:25:00", "10.4", "120", "0", "4"]])
    p = itd._archive_path("600825", date(2026, 9, 2))
    assert p.exists()
    assert list(p.parent.glob("*.tmp")) == []
    assert itd._read_archive("600825", date(2026, 9, 2))[0][1] == "10.4"
