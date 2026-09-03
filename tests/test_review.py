import json
from datetime import date

from utils import review as rv


def test_get_daily_review(monkeypatch):
    rv._review_cache.clear()
    monkeypatch.setattr(
        rv,
        "indices_section",
        lambda day: {"date": str(day), "items": [{"code": "000001"}], "notes": []},
    )
    monkeypatch.setattr(
        rv,
        "breadth_section",
        lambda day: {"date": str(day), "limit_up": 50, "notes": ["x"]},
    )
    monkeypatch.setattr(
        rv,
        "fund_flow_section",
        lambda day: {
            "date": str(day),
            "market": {"main_net_yi": -100.0},
            "notes": [],
        },
    )
    monkeypatch.setattr(
        rv,
        "derivatives_section",
        lambda day: {
            "date": str(day),
            "basis": None,
            "pcr": None,
            "seats": {"IF": None, "IO": None},
            "notes": [],
        },
    )
    monkeypatch.setattr(
        rv,
        "margin_section",
        lambda day, days=10, market="沪深": {
            "date": str(day),
            "rzye_yi": 16100.0,
            "notes": [],
        },
    )
    monkeypatch.setattr(
        rv,
        "get_etf_daily",
        lambda symbols, date: json.dumps(
            {
                "success": True,
                "data": {
                    "date": date,
                    "merged": {
                        "est_net_subscription_yi": 12.5,
                        "avg_change_pct": 0.8,
                    },
                },
            },
            ensure_ascii=False,
        ),
    )
    out = rv.get_daily_review("2026-09-02")
    data = json.loads(out)["data"]
    assert data["date"] == "2026-09-02"
    assert data["indices"]["items"][0]["code"] == "000001"
    assert data["breadth"]["limit_up"] == 50
    assert data["national_team"]["total_est_net_subscription_yi"] == 12.5
    assert data["margin"]["rzye_yi"] == 16100.0
    assert data.get("errors") is None  # 无失败模块时不输出 errors
    assert any("[breadth] x" == n for n in data["notes"])


def test_get_daily_review_partial_failure(monkeypatch):
    rv._review_cache.clear()

    def boom(day):
        raise ValueError("非交易日")

    monkeypatch.setattr(rv, "indices_section", boom)
    monkeypatch.setattr(rv, "breadth_section", boom)
    monkeypatch.setattr(rv, "fund_flow_section", boom)
    monkeypatch.setattr(rv, "derivatives_section", boom)
    monkeypatch.setattr(rv, "margin_section", boom)
    monkeypatch.setattr(
        rv,
        "get_etf_daily",
        lambda symbols, date: json.dumps(
            {"success": True, "data": {"date": date, "merged": {}}},
            ensure_ascii=False,
        ),
    )
    out = rv.get_daily_review("2026-09-02")
    data = json.loads(out)["data"]
    assert data["indices"] is None
    assert data["errors"]["indices"] == "非交易日"
    assert data["national_team"] is not None  # 单模块失败不影响整体


def test_get_daily_review_all_fail(monkeypatch):
    rv._review_cache.clear()

    def boom(day):
        raise ValueError("非交易日")

    monkeypatch.setattr(rv, "indices_section", boom)
    monkeypatch.setattr(rv, "breadth_section", boom)
    monkeypatch.setattr(rv, "fund_flow_section", boom)
    monkeypatch.setattr(rv, "derivatives_section", boom)
    monkeypatch.setattr(rv, "margin_section", boom)
    monkeypatch.setattr(
        rv,
        "get_etf_daily",
        lambda symbols, date: json.dumps({"success": False, "error": "no data"}),
    )
    out = rv.get_daily_review("2026-09-02")
    payload = json.loads(out)
    assert payload["success"] is False
    assert "全部模块" in payload["error"]


def test_get_daily_review_bad_date():
    out = rv.get_daily_review("2026/09/02")
    assert json.loads(out)["success"] is False
