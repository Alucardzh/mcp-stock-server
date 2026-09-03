from datetime import date

import pandas as pd

from utils import review_common as rc


def test_safe_num():
    assert rc.safe_num(3.14159, 2) == 3.14
    assert rc.safe_num(None) is None
    assert rc.safe_num(float("nan")) is None
    assert rc.safe_num("abc") is None
    assert rc.safe_num(-0.0) == 0.0


def test_json_envelope():
    ok = rc.json_ok({"a": 1})
    assert '"success": true' in ok and '"a": 1' in ok
    err = rc.json_err("boom")
    assert '"success": false' in err and "boom" in err


def test_parse_day():
    assert rc.parse_day("") is not None  # 默认今天
    assert rc.parse_day("2026-09-02") == date(2026, 9, 2)
    assert rc.parse_day("2026/09/02") is None
    assert rc.parse_day("bad") is None


def test_norm_date():
    assert rc.norm_date("2026-09-02") == "2026-09-02"
    assert rc.norm_date("20260902") == "2026-09-02"
    assert rc.norm_date(20260902) == "2026-09-02"


def test_prev_trading_days():
    # 2026-09-02 是周三，往前 3 个交易日 = 周二/周一/上周五
    assert rc.prev_trading_days(date(2026, 9, 2), 3) == [
        date(2026, 9, 1),
        date(2026, 8, 31),
        date(2026, 8, 28),
    ]


def test_col_like():
    df = pd.DataFrame(columns=["日期", "主力净流入-净额"])
    assert rc.col_like(df, "主力净流入-净额") == "主力净流入-净额"
    assert rc.col_like(df, "不存在") is None
