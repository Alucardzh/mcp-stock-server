#!/usr/bin/env python3
"""新工具实网冒烟（本地或容器内均可运行；需 AKPROXY_TOKEN）。"""
import json
import sys

from utils import (
    get_lhb,
    get_lhb_seats,
    get_stock_auction,
    get_stock_boards,
    get_stock_intraday,
    get_zt_pool,
)

CHECKS = [
    ("zt_pool 封板时间", lambda: get_zt_pool("涨停", "2026-10-16")),
    ("intraday 1min", lambda: get_stock_intraday("600825", "1", "summary")),
    ("intraday 5min 历史", lambda: get_stock_intraday("600825", "5", "summary", "2026-10-16")),
    ("auction 当日", lambda: get_stock_auction("600825")),
    ("auction 归档回读", lambda: get_stock_auction("600825", mode="detail")),
    ("boards 行业概念", lambda: get_stock_boards("600825")),
    ("lhb 榜单", lambda: get_lhb("2026-10-16")),
    ("lhb 席位", lambda: get_lhb_seats("600825", "2026-10-16")),
]


def main() -> int:
    failed = 0
    for name, fn in CHECKS:
        try:
            out = json.loads(fn())
            ok = out.get("success")
            print(f"[{'OK' if ok else 'FAIL'}] {name}: {str(out)[:160]}")
            failed += 0 if ok else 1
        except Exception as e:
            print(f"[FAIL] {name}: {type(e).__name__}: {e}")
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
