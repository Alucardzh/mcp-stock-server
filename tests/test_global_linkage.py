import json
from datetime import date

import pandas as pd

from utils import global_linkage as gl


def _sina_hist():
    return pd.DataFrame(
        {
            "date": ["2026-09-01", "2026-09-02"],
            "close": [26217.83, 26584.06],
        }
    )


def _patch(monkeypatch):
    monkeypatch.setattr(gl, "index_us_stock_sina", lambda symbol: _sina_hist())
    monkeypatch.setattr(
        gl, "us_treasury_section",
        lambda day=None: {"date": str(day), "us10y": 4.77, "us10y_chg_bp": -1.0,
                          "us30y": 5.25, "us30y_chg_bp": -1.0, "us2y": 4.34,
                          "spread_10y_2y": 0.43, "notes": []},
    )
    monkeypatch.setattr(
        gl, "jgb_yield_on",
        lambda day: {"date": str(day), "y10": 2.987, "y20": 3.859, "y30": 4.131}
        if day == date(2026, 9, 2) else
        {"date": str(day), "y10": 3.006, "y20": 3.864, "y30": 4.122},
    )
    monkeypatch.setattr(
        gl, "indices_section",
        lambda day: {"items": [{"name": "上证指数", "chg_pct": -0.19}], "notes": []},
    )
    monkeypatch.setattr(
        gl, "breadth_section",
        lambda day: {"up": 4337, "down": 1126, "limit_up": 50, "notes": []},
    )
    monkeypatch.setattr(
        gl, "sector_fund_flow_section",
        lambda day, indicator="今日": {"top5": [{"name": "电子", "main_net_yi": -108.93}],
                                       "bottom5": [], "notes": []},
    )


def test_get_global_linkage_review(monkeypatch):
    _patch(monkeypatch)
    out = json.loads(gl.get_global_linkage_review("2026-09-03"))
    assert out["success"] is True
    d = out["data"]
    assert d["date"] == "2026-09-03"
    assert d["overnight"]["nasdaq_pct"] == round((26584.06 / 26217.83 - 1) * 100, 2)
    assert d["overnight"]["us10y_chg_bp"] == -1.0
    assert d["overnight"]["japan10y"] == 2.987
    assert d["a_share"]["up_down_ratio"] == "4337/1126"
    assert d["a_share"]["elec_fund_flow_yi"] == -108.93
    assert d["overnight"]["vix"] is None  # 无历史源


def test_get_global_linkage_review_bad_date():
    out = json.loads(gl.get_global_linkage_review("2026/09/03"))
    assert out["success"] is False
