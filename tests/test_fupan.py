"""fupan_v1 组装单测：段并行降级 / NOT_TRADE_DAY / NOT_READY / klines 默认涨停池 / chains。"""

import json
from datetime import datetime

import pandas as pd
import pytest

from utils import fupan as fp


@pytest.fixture(autouse=True)
def _clean():
    fp._fupan_cache.clear()
    yield
    fp._fupan_cache.clear()


def _mock_sections(monkeypatch, *, zt_n=3, zb_n=1):
    monkeypatch.setattr(fp, "indices_section", lambda day: {"items": [], "notes": []})
    monkeypatch.setattr(fp, "breadth_section", lambda day: {"up": 1, "notes": []})
    monkeypatch.setattr(fp, "fund_flow_section", lambda day: {"notes": []})
    monkeypatch.setattr(fp, "derivatives_section", lambda day: {"notes": []})
    monkeypatch.setattr(
        fp,
        "pool_full_snapshot",
        lambda pool, day: (
            {
                "pool": pool,
                "count": zt_n if pool == "涨停" else zb_n,
                "items": [{"code": "002384", "hybk": "元件", "lianban": 1}]
                * (zt_n if pool == "涨停" else zb_n),
                "truncated": False,
                "as_of": "t",
                "notes": [],
            }
            if (pool == "涨停" or zb_n)
            else None
        ),
    )
    monkeypatch.setattr(
        fp,
        "fupan_boards_section",
        lambda: {"industry": [], "concept": [], "as_of": "t"},
    )
    monkeypatch.setattr(
        fp,
        "industry_counts",
        lambda pool, day: {"元件": zt_n} if pool == "涨停" else {"电子": zb_n},
    )
    monkeypatch.setattr(
        fp,
        "lhb_aggregate",
        lambda d, symbols=[]: {
            "stocks": {},
            "cats": {},
            "total_net": None,
            "notes": [],
            "as_of": "t",
        },
    )
    monkeypatch.setattr(
        fp,
        "batch_klines",
        lambda symbols, end_date, bars=130: {
            "items": {s: {"close": 1.0} for s in symbols},
            "warnings": [],
        },
    )


def test_fupan_v1_full_flow(monkeypatch):
    _mock_sections(monkeypatch, zt_n=3, zb_n=1)
    out = json.loads(
        fp.fupan_v1(
            "2026-11-11",
            symbols=["002384"],
            options={"chain_buckets": {"电子链": ["元件", "电子"]}},
        )
    )
    assert out["success"] and out["data"]["schema_version"] == "v1"
    s = out["data"]["sections"]
    assert s["pools"]["zt"]["count"] == 3 and s["pools"]["zt"]["truncated"] is False
    assert (
        s["chains"]["电子链"]["limitup"] == 3 and s["chains"]["电子链"]["zhaban"] == 1
    )
    assert s["buckets"]["元件"]["limitup"] == 3
    assert s["klines"]["002384"]["close"] == 1.0  # klines 段直接是 {code: 指标}
    assert "deprecated" not in out["data"]


def test_fupan_v1_klines_default_uses_zt_pool(monkeypatch):
    """symbols 不传 → klines 取涨停池全量代码。"""
    _mock_sections(monkeypatch, zt_n=2, zb_n=0)
    captured = {}

    def fake_batch(symbols, end_date, bars=130):
        captured["symbols"] = symbols
        return {"items": {s: {"close": 1.0} for s in symbols}, "warnings": []}

    monkeypatch.setattr(fp, "batch_klines", fake_batch)
    out = json.loads(fp.fupan_v1("2026-11-11"))
    assert captured["symbols"] == ["002384"]
    assert out["success"]


def test_fupan_v1_not_trade_day(monkeypatch):
    class FakeNow(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 11, 17, 16, 0)  # 使 2026-11-15(周日) 恒为过去日期

    _mock_sections(monkeypatch)  # 其余段照 mock
    monkeypatch.setattr(fp, "pool_full_snapshot", lambda pool, day: None)

    def empty_section(day):
        raise ValueError("非交易日")

    monkeypatch.setattr(fp, "indices_section", empty_section)
    monkeypatch.setattr(fp, "datetime", FakeNow)
    out = json.loads(fp.fupan_v1("2026-11-15"))  # 周日
    assert out["success"] is False and out["error_code"] == "NOT_TRADE_DAY"


def test_fupan_v1_not_ready(monkeypatch):
    class FakeNow(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 11, 11, 10, 0)

    monkeypatch.setattr(fp, "datetime", FakeNow)
    out = json.loads(fp.fupan_v1("2026-11-11"))
    assert out["success"] is False and out["error_code"] == "NOT_READY"
    assert out["expected_at"] == "15:05"


def test_fupan_v1_section_degrade(monkeypatch):
    _mock_sections(monkeypatch)
    monkeypatch.setattr(fp, "fupan_boards_section", lambda: None)
    out = json.loads(fp.fupan_v1("2026-11-11"))
    s = out["data"]["sections"]
    assert s["boards"] == {"status": "unavailable", "reason": "upstream_error"}
    assert s["klines"]["002384"]["close"] == 1.0  # 其余段照常


def test_fupan_v1_invalid_arg(monkeypatch):
    out = json.loads(fp.fupan_v1("2026/11/11"))
    assert out["success"] is False and out["error_code"] == "INVALID_ARG"


def test_fupan_v1_cached(monkeypatch):
    _mock_sections(monkeypatch)
    fp.fupan_v1("2026-11-11")
    out = json.loads(fp.fupan_v1("2026-11-11"))
    assert out["data"]["cached"] is True
