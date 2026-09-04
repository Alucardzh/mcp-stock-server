import json
from datetime import date

import pandas as pd

from utils import global_rates as gr


def _rate_df():
    return pd.DataFrame(
        {
            "日期": ["2026-09-01", "2026-09-02", "2026-09-03"],
            "中国国债收益率2年": [1.24, 1.23, 1.24],
            "美国国债收益率2年": [4.30, 4.32, 4.34],
            "美国国债收益率5年": [4.50, 4.51, 4.52],
            "美国国债收益率10年": [4.75, 4.78, 4.77],
            "美国国债收益率30年": [5.22, 5.26, 5.25],
            "美国国债收益率10年-2年": [0.45, 0.46, 0.43],
        }
    )


def test_us_treasury_section_latest(monkeypatch):
    gr._us_treasury_cache = None
    monkeypatch.setattr(gr, "bond_zh_us_rate", lambda: _rate_df())
    out = gr.us_treasury_section()
    assert out["date"] == "2026-09-03"
    assert out["us10y"] == 4.77
    assert out["us10y_chg_bp"] == round((4.77 - 4.78) * 100, 1)
    assert out["us30y"] == 5.25
    assert out["spread_10y_2y"] == 0.43


def test_us_treasury_section_day(monkeypatch):
    gr._us_treasury_cache = None
    monkeypatch.setattr(gr, "bond_zh_us_rate", lambda: _rate_df())
    out = gr.us_treasury_section(date(2026, 9, 2))
    assert out["us10y"] == 4.78
    assert out["us10y_chg_bp"] == round((4.78 - 4.75) * 100, 1)


def test_us_treasury_section_missing_day(monkeypatch):
    import pytest

    gr._us_treasury_cache = None
    monkeypatch.setattr(gr, "bond_zh_us_rate", lambda: _rate_df())
    with pytest.raises(ValueError):
        gr.us_treasury_section(date(2026, 9, 4))


def test_get_fed_watch(monkeypatch):
    gr._us_treasury_cache = None
    monkeypatch.setattr(gr, "bond_zh_us_rate", lambda: _rate_df())
    out = json.loads(gr.get_fed_watch())
    assert out["success"] is True
    d = out["data"]
    assert d["us2y"] == 4.34
    assert d["spread_10y_2y"] == 0.43
    # 仅3行数据: 周变动回退到首行 (4.34-4.30)*100
    assert d["us2y_chg_1w_bp"] == round((4.34 - 4.30) * 100, 1)


def test_us_treasury_exact_column_priority(monkeypatch):
    """利差列排在10年列之前时, 精确匹配优先防止读错列"""
    gr._us_treasury_cache = None
    base = _rate_df()
    cols = ["日期", "美国国债收益率10年-2年", "美国国债收益率2年", "美国国债收益率10年", "美国国债收益率30年"]
    monkeypatch.setattr(gr, "bond_zh_us_rate", lambda: base[cols])
    out = gr.us_treasury_section()
    assert out["us10y"] == 4.77
    assert out["spread_10y_2y"] == 0.43
