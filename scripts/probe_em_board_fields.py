#!/usr/bin/env python3
"""一次性探测东财板块接口字段支持性（本地 uv run，需 AKPROXY_TOKEN 环境变量）。

验证点（设计文档 §5）：
① clist m:90 是否返回 f17/f18（板块今开/昨收）与 f62/f104/f105/f128
② ulist.np/get 是否支持 90.BK secid 的 f46/f60
③ 空响应/字段缺失形态确认（fltt=2 数值是否已格式化）

用法：uv run python scripts/probe_em_board_fields.py
"""

import json

# 导入 utils.tools 触发 akshare_proxy_patch 安装（requests 被接管 → akproxy）
import utils.tools  # noqa: F401

import requests

FIELDS = "f12,f14,f2,f3,f17,f18,f62,f104,f105,f128,f140,f141"


def probe_clist():
    r = requests.get(
        "https://push2.eastmoney.com/api/qt/clist/get",
        params={
            "pn": 1, "pz": 5, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fid": "f3", "fs": "m:90+t:2", "fields": FIELDS,
        },
        timeout=10,
    )
    data = r.json().get("data") or {}
    diff = data.get("diff") or []
    print("== clist 行业 m:90 ==")
    print(f"total={data.get('total')} rows={len(diff)}")
    if diff:
        print("首行字段:", json.dumps(diff[0], ensure_ascii=False))
        keys = set(diff[0])
        for f in ("f17", "f18", "f62", "f104", "f105", "f128"):
            print(f"  {f}: {'✓ 返回' if f in keys else '✗ 缺失'}")


def probe_ulist():
    r = requests.get(
        "https://push2.eastmoney.com/api/qt/ulist.np/get",
        params={
            "fltt": 2, "invt": 2,
            "secids": "90.BK0459,90.BK1036",
            "fields": "f12,f14,f46,f60",
        },
        timeout=10,
    )
    data = r.json().get("data")
    print("== ulist 90.BK secid ==")
    print(json.dumps(data, ensure_ascii=False)[:500])


def probe_stock_get():
    r = requests.get(
        "https://push2.eastmoney.com/api/qt/stock/get",
        params={
            "fltt": 2, "invt": 2,
            "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f59,f60,f62,f104,f105,f107,f168,f169",
            "secid": "90.BK0459",
        },
        timeout=10,
    )
    data = r.json().get("data")
    print("== stock/get 90.BK0459 ==")
    print(json.dumps(data, ensure_ascii=False)[:500])


if __name__ == "__main__":
    probe_clist()
    probe_ulist()
    probe_stock_get()
    print("结论回填：clist f17/f18 是否可用 → 决定开盘价主通道；ulist 是否认 90.BK")
