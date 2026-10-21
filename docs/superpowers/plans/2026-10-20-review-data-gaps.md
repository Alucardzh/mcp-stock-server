# 复盘数据缺口补齐 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为复盘工作流补齐 6 项数据能力：涨停池封板时间字段、分钟分时、竞价分时（含 CSV 归档）、行业/概念归属、龙虎榜榜单、龙虎榜席位明细。

**Architecture:** 沿用现有平铺模块模式：每个新领域一个 `utils/*.py` 文件（无状态实时拉取 + review_common JSON 信封），`server.py` 注册 `_tool` 后缀的薄包装。唯一的本地状态是竞价 CSV 归档（`{AUCTION_ARCHIVE_ROOT}/{code}/{date}.csv`，读写穿透）。通道遵循 efinance-first 规则：efinance 有封装的走 `utils/ef.py` 包装，akshare 独有的直接模块级导入（便于测试 monkeypatch），竞价分笔走 push2 直连（保留方向字段）。

**Tech Stack:** Python 3.13 / FastMCP / akshare 1.18.30 / efinance 0.5.9 / pandas / requests / pytest。

**Spec:** `docs/superpowers/specs/2026-10-20-review-data-gaps-design.md`

## Global Constraints

- 依赖锁定：akshare==1.18.30、efinance==0.5.9（uv.lock 冻结，`uv sync --frozen` 安装）；**不新增任何依赖**。
- 测试命令（Windows Git Bash，仓库根目录）：`.venv/Scripts/python.exe -m pytest tests -v`。
- 所有工具函数返回 `review_common.json_ok / json_err` 的 JSON 字符串信封；docstring 用中文。
- server.py 注册名一律 `*_tool` 后缀，注册函数只做参数透传。
- 东财流量全部经过 `akshare_proxy_patch` hook_domains（tools.py 安装）；测试中不要真连网（全部 monkeypatch 模块级函数）。
- 不提交 `.env`；`data/auction/` 归档目录加入 `.gitignore`。
- efinance 封装一律放 `utils/ef.py`（懒加载 `_load_ef()`），业务模块 `from .ef import ...` 导入。

## File Structure

```txt
utils/
├── ef.py               # 改：_KLT_MAP 补分钟档；增 ef_belong_board / ef_daily_billboard
├── review_market.py    # 改：_POOL_FIELDS 补封板时间两列（涨停/炸板）
├── intraday.py         # 新：分钟分时 + 竞价分时 + CSV 归档
├── boards.py           # 新：行业+概念归属（板块名单当日缓存）
├── lhb.py              # 新：龙虎榜榜单 + 席位明细
├── tools.py            # 改：hook_domains += datacenter-web.eastmoney.com
├── __init__.py         # 改：导出新函数
tests/
├── test_review_market.py  # 改：+封板时间字段测试
├── test_intraday.py       # 新
├── test_boards.py         # 新
├── test_lhb.py            # 新
scripts/
└── smoke_review_gaps.py   # 新：部署后实网冒烟
server.py              # 改：注册 5 个新工具
docker-compose.yml     # 改：挂 data/auction 卷 + AUCTION_ARCHIVE_ROOT env
env.template           # 改：+ AUCTION_ARCHIVE_ROOT
README.md              # 改：工具列表 + 5 行
```

---

### Task 1: 涨停/炸板池补封板时间字段

**Files:**
- Modify: `utils/review_market.py:103-114`（`_POOL_FIELDS`）
- Test: `tests/test_review_market.py`

**Interfaces:**
- Consumes: 现有 `get_zt_pool(pool, date)`、akshare 池原始列 `首次封板时间`/`最后封板时间`（HHMMSS 字符串，akshare 已 zfill(6)）。
- Produces: `get_zt_pool` 的 items 每行新增 `首次封板时间`（涨停+炸板池）与 `最后封板时间`（仅涨停池，炸板池无此列）。白名单按 `if c in df.columns` 过滤，列缺失自动降级，不破坏现有消费方。

- [ ] **Step 1: Write the failing test**

在 `tests/test_review_market.py` 顶部补 `import json`，文件末尾追加：

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_review_market.py -v -k seal_time`
Expected: 2 FAILED（KeyError: '首次封板时间' 或断言不等）

- [ ] **Step 3: Write minimal implementation**

`utils/review_market.py` 的 `_POOL_FIELDS` 改为：

```python
_POOL_FIELDS = {
    "涨停": ["代码", "名称", "涨跌幅", "最新价", "成交额", "换手率", "封板资金",
             "首次封板时间", "最后封板时间", "炸板次数", "涨停统计", "连板数", "所属行业"],
    "炸板": ["代码", "名称", "涨跌幅", "最新价", "成交额", "换手率", "首次封板时间",
             "炸板次数", "涨停统计", "所属行业"],
    "跌停": ["代码", "名称", "涨跌幅", "最新价", "成交额", "换手率", "封单资金",
             "板上成交额", "连续跌停", "开板次数", "所属行业"],
    "强势": ["代码", "名称", "涨跌幅", "最新价", "成交额", "换手率", "涨停统计",
             "连板数", "所属行业"],
    "昨涨停": ["代码", "名称", "涨跌幅", "最新价", "成交额", "换手率", "涨停统计",
               "连板数", "所属行业"],
}
```

（跌停/强势/昨涨停三池不变。）

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_review_market.py -v`
Expected: 全部 PASS（新旧测试）

- [ ] **Step 5: Commit**

```bash
git add utils/review_market.py tests/test_review_market.py
git commit -m "feat: expose seal time fields in zt/zhaban pool tool"
```

---

### Task 2: 分钟分时工具 get_stock_intraday

**Files:**
- Modify: `utils/ef.py:19`（`_KLT_MAP`）
- Create: `utils/intraday.py`
- Modify: `server.py`（import + 注册）、`utils/__init__.py`（导出）、`README.md`（工具列表）
- Test: `tests/test_intraday.py`

**Interfaces:**
- Consumes: `ef_stock_hist(symbol, period, start_date, end_date)`（ef.py 现有，period 补 "1"/"5"）；akshare `stock_zh_a_hist_min_em(symbol, period, start_date, end_date)`；`validate_stock_symbol`（utils/validate.py）。
- Produces: `get_stock_intraday(symbol: str, period: str = "1", mode: str = "summary", date: str = "") -> str`（JSON 信封）；`limit_price(prev_close, code, name) -> float|None`（导出的涨停价推算助手）；`_prev_close(symbol, day)`（Task 3 竞价摘要的昨收来源）；`AUCTION_ARCHIVE_ROOT`（Task 3 使用）。

- [ ] **Step 1: Write the failing test**

Create `tests/test_intraday.py`：

```python
import json
from datetime import date

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_intraday.py -v`
Expected: FAIL（ModuleNotFoundError: utils.intraday）

- [ ] **Step 3: Write minimal implementation**

`utils/ef.py:19` 的 `_KLT_MAP` 改为：

```python
_KLT_MAP = {
    "daily": 101, "weekly": 102, "monthly": 103,
    "1": 1, "5": 5, "15": 15, "30": 30, "60": 60,
}
```

Create `utils/intraday.py`：

```python
#!/usr/bin/env python3
"""
个股分钟分时 + 竞价分时（含 CSV 归档）。

数据源：
- ef_stock_hist(period="1"/"5")  efinance 分钟K线（1分钟仅当日，5分钟可回溯）
- akshare stock_zh_a_hist_min_em  分钟线备用（同源东财，列名略异）
- push2 details/get 直连          当日分笔，含 9:15-9:25 竞价虚拟撮合过程
                                  （efinance 封装丢弃第5段方向字段，故直连）

竞价归档：{AUCTION_ARCHIVE_ROOT}/{code}/{date}.csv。当日首次拉取落盘；
历史日期只读归档（免费源无历史档，无法回补）。
"""

from datetime import date as date_type, datetime
import logging
import os
from pathlib import Path

import pandas as pd
import requests

from akshare import stock_zh_a_hist_min_em

from .ef import ef_stock_hist
from .review_common import json_err, json_ok, parse_day, safe_num
from .tools import RateLimiter, with_retry
from .validate import validate_stock_symbol

logger = logging.getLogger(__name__)

AUCTION_ARCHIVE_ROOT = Path(
    os.getenv("AUCTION_ARCHIVE_ROOT")
    or (Path(__file__).resolve().parent.parent / "data" / "auction")
)


def limit_price(prev_close, code: str, name: str):
    """按板块规则推算涨停价：北交±30%、创业/科创±20%、主板ST±5%、其余±10%。"""
    if not prev_close:
        return None
    if code.startswith(("83", "87", "88", "43", "92")):
        pct = 0.30
    elif code.startswith(("300", "301", "302", "688", "689")):
        pct = 0.20
    elif "ST" in (name or "").upper():
        pct = 0.05
    else:
        pct = 0.10
    return round(prev_close * (1 + pct), 2)


def _prev_close(symbol: str, day: date_type):
    """day 之前最近一根日K的收盘（竞价摘要的昨收来源）"""
    try:
        df = ef_stock_hist(symbol, period="daily", end_date=day.strftime("%Y%m%d"))
    except Exception as e:  # noqa: BLE001
        logger.warning("daily hist for prev_close failed %s: %s", symbol, e)
        return None
    if df is None or df.empty:
        return None
    hist = df[pd.to_datetime(df["日期"]).dt.date < day]
    if hist.empty:
        hist = df
    return safe_num(hist.iloc[-1]["收盘"])


def _minute_bars(symbol: str, day: date_type, period: str):
    """分钟bar（efinance 主 / akshare 备）；时间列统一为 日期（'YYYY-MM-DD HH:MM'）"""
    ymd = day.strftime("%Y%m%d")
    df = None
    try:
        df = ef_stock_hist(symbol, period=period, start_date=ymd, end_date=ymd)
    except Exception as e:  # noqa: BLE001
        logger.warning("efinance minute hist failed %s: %s", symbol, e)
    if df is None or df.empty:
        try:
            df = stock_zh_a_hist_min_em(
                symbol=symbol,
                period=period,
                start_date=f"{ymd} 09:00:00",
                end_date=f"{ymd} 15:30:00",
            )
            if df is not None and not df.empty and "时间" in df.columns:
                df = df.rename(columns={"时间": "日期"})
        except Exception as e:  # noqa: BLE001
            logger.warning("akshare minute hist failed %s: %s", symbol, e)
            return None
    if df is None or df.empty:
        return None
    return df


def _bar_rows(df: pd.DataFrame) -> list[dict]:
    rows = []
    for _, r in df.iterrows():
        t = str(r["日期"])
        rows.append(
            {
                "time": t[11:16] if len(t) >= 16 else t,
                "open": safe_num(r.get("开盘")),
                "close": safe_num(r.get("收盘")),
                "high": safe_num(r.get("最高")),
                "low": safe_num(r.get("最低")),
                "volume": safe_num(r.get("成交量"), 0),
                "amount": safe_num(r.get("成交额"), 0),
            }
        )
    return rows


def _seg(bars: list[dict]) -> dict:
    return {
        "high": max((b["high"] for b in bars if b["high"] is not None), default=None),
        "low": min((b["low"] for b in bars if b["low"] is not None), default=None),
        "close": bars[-1]["close"],
        "volume": safe_num(sum(b["volume"] or 0 for b in bars), 0),
        "amount_yi": safe_num(sum(b["amount"] or 0 for b in bars) / 1e8, 2),
    }


def intraday_summary(symbol: str, name: str, df: pd.DataFrame, zt_price) -> dict:
    bars = _bar_rows(df)
    first, last = bars[0], bars[-1]
    n30 = min(30, len(bars))
    his = [b for b in bars if b["high"] is not None]
    los = [b for b in bars if b["low"] is not None]
    hi = max(his, key=lambda b: b["high"]) if his else None
    lo = min(los, key=lambda b: b["low"]) if los else None
    if zt_price:
        touch = [b["time"] for b in bars if (b["high"] or 0) >= zt_price]
    else:
        touch = []
    return {
        "symbol": symbol,
        "name": name,
        "open": first["open"],  # 集合竞价撮合价
        "auction_vol_hand": first["volume"],
        "open_30min": _seg(bars[:n30]),
        "tail_30min": _seg(bars[-n30:]),
        "day_high": {"price": hi["high"], "time": hi["time"]} if hi else None,
        "day_low": {"price": lo["low"], "time": lo["time"]} if lo else None,
        "close": last["close"],
        "limit_price": zt_price,
        "limit_touch": {
            "first": touch[0] if touch else None,
            "last": touch[-1] if touch else None,
            "count": len(touch),
        },
    }


@RateLimiter(max_calls=10, time_window=60)
@with_retry(max_retries=2, delay=1.0)
def get_stock_intraday(symbol: str, period: str = "1", mode: str = "summary", date: str = "") -> str:
    """查询个股分钟分时（1分钟仅当日；5分钟可回溯历史）

    Args:
        symbol: 6位股票代码
        period: "1"或"5"（分钟粒度）
        mode: summary=关键统计摘要 / full=全量分钟bar（1分钟约240行，token大慎用）
        date: YYYY-MM-DD，默认今天
    """
    try:
        symbol = validate_stock_symbol(symbol)
        if period not in ("1", "5"):
            return json_err('period 仅支持 "1"/"5"')
        if mode not in ("summary", "full"):
            return json_err('mode 仅支持 "summary"/"full"')
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        df = _minute_bars(symbol, day, period)
        if df is None or df.empty:
            if period == "1" and day < datetime.now().date():
                return json_err("1分钟分时仅支持当日；历史回溯请用 period=5")
            return json_err(f"{symbol} 在 {day} 无分钟数据(可能非交易日)")
        name = str(df.iloc[0].get("股票名称") or "") if "股票名称" in df.columns else ""
        zt = limit_price(_prev_close(symbol, day), symbol, name)
        if mode == "full":
            return json_ok(
                {"date": str(day), "period": f"{period}min", "limit_price": zt, "bars": _bar_rows(df)}
            )
        return json_ok({"date": str(day), **intraday_summary(symbol, name, df, zt)})
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_stock_intraday: %s", e)
        return json_err(f"查询分钟分时失败: {e}")
```

`server.py`：import 块（`from utils import (...)`）按字母序插入 `get_stock_intraday,`；在 `get_zt_pool_tool` 注册之后追加：

```python
@mcp.tool()
def get_stock_intraday_tool(
    symbol: str, period: str = "1", mode: str = "summary", date: str = ""
) -> str:
    """查询个股分钟分时（1分钟仅当日、5分钟可回溯历史；默认摘要模式）

    Args:
        symbol: 6位股票代码
        period: "1"/"5" 分钟粒度，默认"1"
        mode: summary=竞价价/开盘30分钟/高低点时间/涨停触碰统计；full=全量bar
        date: YYYY-MM-DD，默认今天
    """
    return get_stock_intraday(symbol, period, mode, date)
```

`utils/__init__.py`：import 区加 `from .intraday import get_stock_auction, get_stock_intraday`（`get_stock_auction` Task 3 才创建，本 task 先只导入 `get_stock_intraday`：`from .intraday import get_stock_intraday`），`__all__` 加 `"get_stock_intraday"`。

`README.md` 工具列表追加一行：

```markdown
### `get_stock_intraday_tool`

查询个股分钟分时（1分钟仅当日，5分钟可回溯历史）。`mode=summary` 返回竞价撮合价、开盘/尾盘30分钟统计、全天高低点时间、涨停触碰时间线；`mode=full` 返回全量分钟bar。
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_intraday.py tests/test_review_market.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add utils/ef.py utils/intraday.py utils/__init__.py server.py README.md tests/test_intraday.py
git commit -m "feat: add minute-bar intraday tool with summary/full modes"
```

---

### Task 3: 竞价分时工具 get_stock_auction + CSV 归档

**Files:**
- Modify: `utils/intraday.py`（追加竞价部分）
- Modify: `server.py`、`utils/__init__.py`、`README.md`
- Modify: `docker-compose.yml`（volume + env）、`env.template`、`.gitignore`
- Test: `tests/test_intraday.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `AUCTION_ARCHIVE_ROOT`、`_prev_close`、`validate_stock_symbol`；push2 `details/get`（requests，被 patch 接管）。
- Produces: `get_stock_auction(symbol: str, date: str = "", mode: str = "summary") -> str`；归档文件 `{AUCTION_ARCHIVE_ROOT}/{code}/{date}.csv`（列：`时间,价格,成交量,单数,方向`）。

- [ ] **Step 1: Write the failing test**

`tests/test_intraday.py` 追加：

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_intraday.py -v -k auction`
Expected: 2 FAILED（AttributeError: _fetch_deal_details）

- [ ] **Step 3: Write minimal implementation**

`utils/intraday.py` 追加：

```python
def _secid(symbol: str) -> str:
    return f"{1 if symbol.startswith('6') else 0}.{symbol}"


def _fetch_deal_details(symbol: str):
    """当日全量分笔（push2 details/get 直连；requests 已被 patch 接管走代理）

    返回 (pre_price, details)；detail 为原始五段字符串 "时间,价格,成交量,单数,方向"。
    pos=-1000000 即从头返回（竞价在响应头部）；流动性极高的个股响应可达数 MB，
    仅按需调用可接受。
    """
    params = {
        "secid": _secid(symbol),
        "fields1": "f1,f2,f3,f4,f5",
        "fields2": "f51,f52,f53,f54,f55",
        "pos": "-1000000",
    }
    r = requests.get(
        "https://push2.eastmoney.com/api/qt/stock/details/get",
        params=params,
        timeout=20,
    )
    data = r.json().get("data") or {}
    return data.get("prePrice"), data.get("details") or []


_AUCTION_HEADER = ["时间", "价格", "成交量", "单数", "方向"]


def _auction_rows(details) -> list[list[str]]:
    """切出 9:15-9:25 竞价段（含 09:25:00 撮合行）"""
    return [d.split(",") for d in details if d[:5] <= "09:25"]


def _archive_path(symbol: str, day: date_type) -> Path:
    return AUCTION_ARCHIVE_ROOT / symbol / f"{day}.csv"


def _read_archive(symbol: str, day: date_type):
    p = _archive_path(symbol, day)
    if not p.exists():
        return None
    return pd.read_csv(p, dtype=str).fillna("").values.tolist()


def _write_archive(symbol: str, day: date_type, rows: list[list[str]]):
    p = _archive_path(symbol, day)
    p.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=_AUCTION_HEADER).to_csv(p, index=False, encoding="utf-8")


def _auction_summary(symbol: str, pre_close, rows: list[list[str]]) -> dict:
    seg1 = [r for r in rows if r[0] < "09:20:00"]
    seg2 = [r for r in rows if "09:20:00" <= r[0] < "09:25:00"]
    final = rows[-1] if rows else None
    match_price = safe_num(float(final[1])) if final else None
    match_vol = safe_num(float(final[2]), 0) if final else None

    def _end(seg):
        return (
            {"price": safe_num(float(seg[-1][1])), "cum_vol": safe_num(float(seg[-1][2]), 0)}
            if seg
            else {"price": None, "cum_vol": None}
        )

    return {
        "symbol": symbol,
        "pre_close": safe_num(float(pre_close)) if pre_close else None,
        "match_price": match_price,  # 09:25 撮合价 = 当日开盘价
        "match_vol_hand": match_vol,  # 竞价匹配量（手，累计口径）
        "est_match_amount_wan": safe_num(match_price * match_vol * 100 / 1e4)
        if match_price and match_vol
        else None,
        "open_gap_pct": safe_num((match_price / float(pre_close) - 1) * 100, 2)
        if match_price and pre_close
        else None,
        "track": {
            "0915_0920": {**_end(seg1), "note": "可撤单段，假单噪音大"},
            "0920_0925": {**_end(seg2), "note": "不可撤单，真实意愿段"},
        },
        "rows": len(rows),
    }


@RateLimiter(max_calls=10, time_window=60)
@with_retry(max_retries=2, delay=1.0)
def get_stock_auction(symbol: str, date: str = "", mode: str = "summary") -> str:
    """查询个股集合竞价分时（9:15-9:25 虚拟撮合过程）

    Args:
        symbol: 6位股票代码
        date: YYYY-MM-DD 默认今天。当日实时拉取并落盘归档；历史日期读 CSV 归档
              （免费源仅当日可拉，无归档的历史无法回补）
        mode: summary=三段轨迹+撮合统计 / detail=竞价段逐笔快照
    """
    try:
        symbol = validate_stock_symbol(symbol)
        if mode not in ("summary", "detail"):
            return json_err('mode 仅支持 "summary"/"detail"')
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        pre_close, rows = None, _read_archive(symbol, day)
        source = "archive"
        if rows is None:
            if day != datetime.now().date():
                return json_err(f"{day} 无竞价归档（免费源仅当日可拉，历史无法回补）")
            pre_close, details = _fetch_deal_details(symbol)
            rows = _auction_rows(details)
            if not rows:
                return json_err(f"{symbol} 当日无竞价分笔数据(可能非交易日)")
            _write_archive(symbol, day, rows)
            source = "live"
        out: dict = {"date": str(day), "source": source}
        if mode == "detail":
            out["items"] = [dict(zip(_AUCTION_HEADER, r)) for r in rows]
            return json_ok(out)
        if pre_close is None:
            pre_close = _prev_close(symbol, day)  # 归档不含昨收，从日K补
        out.update(_auction_summary(symbol, pre_close, rows))
        return json_ok(out)
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_stock_auction: %s", e)
        return json_err(f"查询竞价分时失败: {e}")
```

`server.py` 注册（`get_stock_intraday_tool` 之后）：

```python
@mcp.tool()
def get_stock_auction_tool(symbol: str, date: str = "", mode: str = "summary") -> str:
    """查询个股集合竞价分时（9:15-9:25 虚拟撮合；当日拉取即归档，历史读CSV归档）

    Args:
        symbol: 6位股票代码
        date: YYYY-MM-DD，默认今天
        mode: summary=9:15/9:20/9:25 三段轨迹+撮合价量/高开幅度；detail=逐笔快照
    """
    return get_stock_auction(symbol, date, mode)
```

`utils/__init__.py`：改为 `from .intraday import get_stock_auction, get_stock_intraday`，`__all__` 加 `"get_stock_auction"`。

`docker-compose.yml`：`services.mcp-stock` 增加 volumes 与 env（`restart: unless-stopped` 之前插入）：

```yaml
    volumes:
      - ./data/auction:/app/data/auction
    environment:
      - TZ=Asia/Shanghai
      - FASTMCP_HTTP_HOST_ORIGIN_PROTECTION=false
      - AKPROXY_TOKEN=${AKPROXY_TOKEN:-}
      - YAHOO_PROXY=${YAHOO_PROXY:-}
      - AUCTION_ARCHIVE_ROOT=/app/data/auction
```

（environment 整块替换为上面内容，即原块 + 最后一行。）

`env.template` 追加：

```
# 竞价分时 CSV 归档根目录（容器内默认 /app/data/auction，本地开发默认 ./data/auction）
AUCTION_ARCHIVE_ROOT=
```

`.gitignore` 追加一行：`data/auction/`

`README.md` 工具列表追加：

```markdown
### `get_stock_auction_tool`

查询个股集合竞价分时（9:15-9:25 虚拟撮合过程，仅当日可拉取）。当日首次查询即落盘 CSV 归档（`data/auction/{code}/{date}.csv`），历史日期读归档。`mode=summary` 返回 9:15/9:20/9:25 三段轨迹、撮合价量、高开幅度；`mode=detail` 返回逐笔快照。
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_intraday.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add utils/intraday.py utils/__init__.py server.py README.md docker-compose.yml env.template .gitignore tests/test_intraday.py
git commit -m "feat: add auction tool with read-through csv archive"
```

---

### Task 4: 行业/概念归属工具 get_stock_boards

**Files:**
- Modify: `utils/ef.py`（增 `ef_belong_board`）
- Create: `utils/boards.py`
- Modify: `server.py`、`utils/__init__.py`、`README.md`
- Test: `tests/test_boards.py`

**Interfaces:**
- Consumes: efinance `ef.stock.get_belong_board(symbol)`（经 `utils/ef.py` 包装）；akshare `stock_board_concept_name_em()` / `stock_board_industry_name_em()`（返回含 `板块代码`/`板块名称` 列）。
- Produces: `ef_belong_board(symbol) -> pd.DataFrame`（ef.py）；`get_stock_boards(symbol: str, with_raw: bool = False) -> str`。

- [ ] **Step 1: Write the failing test**

Create `tests/test_boards.py`：

```python
import json

import pandas as pd

from utils import boards as bd


def _belong_df():
    return pd.DataFrame(
        {
            "股票名称": ["新华传媒"] * 5,
            "股票代码": ["600825"] * 5,
            "板块代码": ["BK0477", "BK0733", "BK0500", "BK0596", "BK0509"],
            "板块名称": ["出版", "AI语料", "上证50_", "融资融券", "上海板块"],
            "板块涨幅": [0.5, 1.2, 0.1, -0.3, 0.2],
        }
    )


def _concept_df():
    return pd.DataFrame({"板块代码": ["BK0733"], "板块名称": ["AI语料"]})


def _industry_df():
    return pd.DataFrame({"板块代码": ["BK0477"], "板块名称": ["出版"]})


def test_get_stock_boards(monkeypatch):
    monkeypatch.setattr(bd, "ef_belong_board", lambda s: _belong_df())
    monkeypatch.setattr(bd, "stock_board_concept_name_em", _concept_df)
    monkeypatch.setattr(bd, "stock_board_industry_name_em", _industry_df)
    bd._board_cache.update(date="", concept=set(), industry={})
    out = json.loads(bd.get_stock_boards("600825", with_raw=True))
    assert out["success"]
    d = out["data"]
    assert d["industry"] == "出版"
    assert d["concepts"] == ["AI语料"]
    assert len(d["raw"]) == 5


def test_get_stock_boards_cache_reuse(monkeypatch):
    monkeypatch.setattr(bd, "ef_belong_board", lambda s: _belong_df())
    calls = []
    monkeypatch.setattr(bd, "stock_board_concept_name_em", lambda: calls.append(1) or _concept_df())
    monkeypatch.setattr(bd, "stock_board_industry_name_em", _industry_df)
    bd._board_cache.update(date="", concept=set(), industry={})
    json.loads(bd.get_stock_boards("600825"))
    json.loads(bd.get_stock_boards("600825"))
    assert len(calls) == 1  # 名单当日缓存，第二次不再拉


def test_get_stock_boards_empty(monkeypatch):
    monkeypatch.setattr(bd, "ef_belong_board", lambda s: pd.DataFrame())
    out = json.loads(bd.get_stock_boards("600825"))
    assert not out["success"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_boards.py -v`
Expected: FAIL（ModuleNotFoundError: utils.boards）

- [ ] **Step 3: Write minimal implementation**

`utils/ef.py` 在 `ef_spot_all` 之后追加：

```python
def ef_belong_board(symbol: str) -> pd.DataFrame:
    """个股所属板块（行业+概念+地域+属性混合，调用方负责过滤）。"""
    ef = _load_ef()
    return ef.stock.get_belong_board(symbol)
```

Create `utils/boards.py`：

```python
#!/usr/bin/env python3
"""
个股板块归属：完整行业名 + 概念标签。

数据源：efinance get_belong_board（push2 slist，混合板块）+
akshare 行业/概念板块名单（BK 代码，用于过滤混合结果，当日缓存）。
涨停池"所属行业"4字截断为上游固有，本工具提供完整口径。
"""

from datetime import datetime
import logging

from akshare import stock_board_concept_name_em, stock_board_industry_name_em

from .ef import ef_belong_board
from .review_common import json_err, json_ok
from .validate import validate_stock_symbol

logger = logging.getLogger(__name__)

_board_cache: dict = {"date": "", "concept": set(), "industry": {}}


def _refresh_board_lists():
    """行业/概念板块名单（概念=代码集合，行业=代码→名称映射），当日缓存。"""
    today = str(datetime.now().date())
    if _board_cache["date"] == today and (_board_cache["concept"] or _board_cache["industry"]):
        return
    try:
        dfc = stock_board_concept_name_em()
        _board_cache["concept"] = set(dfc["板块代码"].astype(str))
    except Exception as e:  # noqa: BLE001
        logger.warning("concept board list failed: %s", e)
    try:
        dfi = stock_board_industry_name_em()
        _board_cache["industry"] = dict(
            zip(dfi["板块代码"].astype(str), dfi["板块名称"].astype(str))
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("industry board list failed: %s", e)
    _board_cache["date"] = today


def get_stock_boards(symbol: str, with_raw: bool = False) -> str:
    """查询个股完整行业名与概念板块列表

    Args:
        symbol: 6位股票代码
        with_raw: 是否附带原始全部所属板块（含地域/属性板块）
    """
    try:
        symbol = validate_stock_symbol(symbol)
        df = ef_belong_board(symbol)
        if df is None or df.empty:
            return json_err(f"{symbol} 无所属板块数据")
        _refresh_board_lists()
        industry, concepts, raw = None, [], []
        for _, r in df.iterrows():
            code = str(r.get("板块代码") or "")
            name = str(r.get("板块名称") or "")
            if code in _board_cache["industry"]:
                industry = name
            elif code in _board_cache["concept"]:
                concepts.append(name)
            if with_raw:
                raw.append({"code": code, "name": name})
        notes = []
        if industry is None and not _board_cache["industry"]:
            notes.append("行业名单拉取失败，industry 缺失")
        if not concepts and not _board_cache["concept"]:
            notes.append("概念名单拉取失败，concepts 可能为空")
        out = {"symbol": symbol, "industry": industry, "concepts": concepts, "notes": notes}
        if with_raw:
            out["raw"] = raw
        return json_ok(out)
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_stock_boards: %s", e)
        return json_err(f"查询板块归属失败: {e}")
```

`server.py` import 块插入 `get_stock_boards,`，注册：

```python
@mcp.tool()
def get_stock_boards_tool(symbol: str, with_raw: bool = False) -> str:
    """查询个股完整行业名与概念板块列表（修复涨停池行业4字截断的完整口径）

    Args:
        symbol: 6位股票代码
        with_raw: 是否附带原始全部所属板块（含地域/属性板块）
    """
    return get_stock_boards(symbol, with_raw)
```

`utils/__init__.py`：`from .boards import get_stock_boards`，`__all__` 加 `"get_stock_boards"`。

`README.md` 工具列表追加：

```markdown
### `get_stock_boards_tool`

查询个股完整行业名与概念板块列表（efinance 所属板块 + 概念名单过滤）。用于复盘模块⑦题材聚类的完整口径，替代涨停池 4 字截断行业。
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_boards.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add utils/ef.py utils/boards.py utils/__init__.py server.py README.md tests/test_boards.py
git commit -m "feat: add industry/concept boards tool via efinance belong_board"
```

---

### Task 5: 龙虎榜榜单工具 get_lhb

**Files:**
- Modify: `utils/ef.py`（增 `ef_daily_billboard`）
- Create: `utils/lhb.py`
- Modify: `server.py`、`utils/__init__.py`、`README.md`
- Test: `tests/test_lhb.py`

**Interfaces:**
- Consumes: efinance `ef.stock.get_daily_billboard(start_date, end_date)`（经 `utils/ef.py` 包装，列中文名）；akshare `stock_lhb_detail_em(start_date, end_date)`（YYYYMMDD，列英文名）。
- Produces: `ef_daily_billboard(start_date, end_date) -> pd.DataFrame`（ef.py）；`get_lhb(date: str = "") -> str`。

- [ ] **Step 1: Write the failing test**

Create `tests/test_lhb.py`：

```python
import json

import pandas as pd

from utils import lhb


def _bill_df():
    return pd.DataFrame(
        {
            "股票代码": ["000788", "000788", "600825"],
            "股票名称": ["北新建材", "北新建材", "新华传媒"],
            "解读": ["机构买入", "机构买入", "游资卖出"],
            "龙虎榜净买额": [1.0e8, 0.5e8, -2.0e8],
            "龙虎榜买入额": [1.5e8, 0.6e8, 0.5e8],
            "龙虎榜卖出额": [0.5e8, 0.1e8, 2.5e8],
            "上榜原因": ["日涨幅偏离值达到7%", "日换手率达到20%", "日跌幅偏离值达到7%"],
        }
    )


def test_get_lhb_aggregates(monkeypatch):
    monkeypatch.setattr(lhb, "ef_daily_billboard", lambda s, e: _bill_df())
    out = json.loads(lhb.get_lhb("2026-09-02"))
    assert out["success"]
    d = out["data"]
    assert d["count"] == 2
    item = next(i for i in d["items"] if i["code"] == "000788")
    assert item["net"] == 1.5
    assert item["buy"] == 2.1
    assert len(item["reasons"]) == 2
    assert d["net_sell_top5"][0]["code"] == "600825"


def test_get_lhb_fallback_to_akshare(monkeypatch):
    def _raise(s, e):
        raise RuntimeError("ef down")

    monkeypatch.setattr(lhb, "ef_daily_billboard", _raise)
    ak_df = _bill_df().rename(
        columns={
            "股票代码": "SECURITY_CODE",
            "股票名称": "SECURITY_NAME_ABBR",
            "解读": "EXPLAIN",
            "龙虎榜净买额": "BILLBOARD_NET_AMT",
            "龙虎榜买入额": "BILLBOARD_BUY_AMT",
            "龙虎榜卖出额": "BILLBOARD_SELL_AMT",
            "上榜原因": "EXPLANATION",
        }
    )
    monkeypatch.setattr(lhb, "stock_lhb_detail_em", lambda start_date, end_date: ak_df)
    out = json.loads(lhb.get_lhb("2026-09-02"))
    assert out["success"]
    assert out["data"]["count"] == 2


def test_get_lhb_no_data(monkeypatch):
    monkeypatch.setattr(lhb, "ef_daily_billboard", lambda s, e: pd.DataFrame())
    monkeypatch.setattr(lhb, "stock_lhb_detail_em", lambda start_date, end_date: pd.DataFrame())
    out = json.loads(lhb.get_lhb("2026-09-02"))
    assert not out["success"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_lhb.py -v -k aggregates`
Expected: FAIL（ModuleNotFoundError: utils.lhb）

- [ ] **Step 3: Write minimal implementation**

`utils/ef.py` 追加：

```python
def ef_daily_billboard(start_date: str, end_date: str) -> pd.DataFrame:
    """东财龙虎榜榜单（date 区间，'YYYY-MM-DD'）。"""
    ef = _load_ef()
    return ef.stock.get_daily_billboard(start_date=start_date, end_date=end_date)
```

Create `utils/lhb.py`：

```python
#!/usr/bin/env python3
"""
龙虎榜：榜单汇总（efinance 主 / akshare 备）+ 席位明细（akshare）。
金额：榜单输出亿元，席位输出万元。当日榜单约 17:00 后陆续齐全。
"""

import logging

import pandas as pd

from akshare import stock_lhb_detail_em, stock_lhb_stock_detail_em

from .ef import ef_daily_billboard
from .review_common import json_err, json_ok, parse_day, safe_num
from .tools import RateLimiter, with_retry

logger = logging.getLogger(__name__)


def _norm_billboard(df: pd.DataFrame) -> list[dict]:
    """efinance(中文列) / akshare(英文列) 两种榜单归一。"""
    rows = []
    for _, r in df.iterrows():
        code = r.get("股票代码", r.get("SECURITY_CODE"))
        if code is None or str(code) == "nan":
            continue
        rows.append(
            {
                "code": str(code),
                "name": str(r.get("股票名称", r.get("SECURITY_NAME_ABBR")) or ""),
                "net": r.get("龙虎榜净买额", r.get("BILLBOARD_NET_AMT")),
                "buy": r.get("龙虎榜买入额", r.get("BILLBOARD_BUY_AMT")),
                "sell": r.get("龙虎榜卖出额", r.get("BILLBOARD_SELL_AMT")),
                "explain": str(r.get("解读", r.get("EXPLAIN")) or ""),
                "reason": str(r.get("上榜原因", r.get("EXPLANATION")) or ""),
            }
        )
    return rows


def _fetch_billboard(day):
    d_dash, d_compact = str(day), day.strftime("%Y%m%d")
    try:
        df = ef_daily_billboard(d_dash, d_dash)
        if df is not None and not df.empty:
            return df
    except Exception as e:  # noqa: BLE001
        logger.warning("efinance billboard failed: %s", e)
    try:
        return stock_lhb_detail_em(start_date=d_compact, end_date=d_compact)
    except Exception as e:  # noqa: BLE001
        logger.warning("akshare billboard failed: %s", e)
        return None


@RateLimiter(max_calls=10, time_window=60)
@with_retry(max_retries=2, delay=1.0)
def get_lhb(date: str = "") -> str:
    """查询龙虎榜榜单（按股聚合净买卖、上榜原因与解读；金额亿元）

    Args:
        date: YYYY-MM-DD 默认今天。当日榜单约17:00后陆续齐全
    """
    try:
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        df = _fetch_billboard(day)
        if df is None or df.empty:
            return json_err(f"{day} 无龙虎榜数据(可能非交易日或榜单未出)")
        agg: dict[str, dict] = {}
        for r in _norm_billboard(df):
            a = agg.setdefault(
                r["code"],
                {
                    "code": r["code"],
                    "name": r["name"],
                    "net": 0.0,
                    "buy": 0.0,
                    "sell": 0.0,
                    "explain": r["explain"],
                    "reasons": [],
                },
            )
            a["net"] += safe_num(r["net"], 4) or 0.0
            a["buy"] += safe_num(r["buy"], 4) or 0.0
            a["sell"] += safe_num(r["sell"], 4) or 0.0
            if r["reason"] and r["reason"] not in a["reasons"]:
                a["reasons"].append(r["reason"])
        items = []
        for a in agg.values():
            a["net"] = safe_num(a["net"] / 1e8)
            a["buy"] = safe_num(a["buy"] / 1e8)
            a["sell"] = safe_num(a["sell"] / 1e8)
            items.append(a)
        ranked = sorted(items, key=lambda x: -(x["net"] if x["net"] is not None else 0))
        return json_ok(
            {
                "date": str(day),
                "count": len(items),
                "net_buy_top10": ranked[:10],
                "net_sell_top5": ranked[-5:][::-1],
                "items": items,
            }
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_lhb: %s", e)
        return json_err(f"查询龙虎榜失败: {e}")
```

`server.py` import 块插入 `get_lhb,`，注册：

```python
@mcp.tool()
def get_lhb_tool(date: str = "") -> str:
    """查询龙虎榜榜单（按股聚合净买卖亿元、上榜原因、东财解读；净买TOP10/净卖TOP5）

    Args:
        date: YYYY-MM-DD 默认今天；当日榜单约17:00后陆续齐全
    """
    return get_lhb(date)
```

`utils/__init__.py`：`from .lhb import get_lhb, get_lhb_seats`（`get_lhb_seats` Task 6 才有，本 task 先 `from .lhb import get_lhb`），`__all__` 加 `"get_lhb"`。

`README.md` 工具列表追加：

```markdown
### `get_lhb_tool`

查询龙虎榜榜单（efinance 主 / akshare 备）。按股聚合净买/卖（亿元）、上榜原因、东财解读，附净买 TOP10 / 净卖 TOP5 摘要。
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_lhb.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add utils/ef.py utils/lhb.py utils/__init__.py server.py README.md tests/test_lhb.py
git commit -m "feat: add lhb billboard tool with efinance primary and akshare fallback"
```

---

### Task 6: 龙虎榜席位明细工具 get_lhb_seats（含 hook_domains）

**Files:**
- Modify: `utils/tools.py:33-42`（`install_patch` 的 `hook_domains`）
- Modify: `utils/lhb.py`（追加席位部分）
- Modify: `server.py`、`utils/__init__.py`、`README.md`
- Test: `tests/test_lhb.py`（追加）

**Interfaces:**
- Consumes: akshare `stock_lhb_stock_detail_em(symbol, date, flag)`（date=YYYYMMDD，flag="买入"/"卖出"；打 datacenter-web，需要 hook）。
- Produces: `get_lhb_seats(symbol: str, date: str = "") -> str`；hook_domains 含 `datacenter-web.eastmoney.com`。

- [ ] **Step 1: Write the failing test**

`tests/test_lhb.py` 追加：

```python
def _seats_df(flag):
    amt_col = "买入金额" if flag == "买入" else "卖出金额"
    return pd.DataFrame(
        {
            "营业部名称": ["机构专用", "沪股通专用", "中信证券上海分公司", "华鑫上海分公司", "东财拉萨团结路"],
            amt_col: [5.0e7, 3.0e7, 2.0e7, 1.0e7, 0.5e7],
        }
    )


def test_get_lhb_seats(monkeypatch):
    monkeypatch.setattr(
        lhb, "stock_lhb_stock_detail_em", lambda symbol, date, flag: _seats_df(flag)
    )
    out = json.loads(lhb.get_lhb_seats("600825", "2026-09-02"))
    assert out["success"]
    d = out["data"]
    assert d["buy"][0]["seat"].startswith("[机构]")
    assert d["buy"][0]["amount_wan"] == 5000.0
    assert d["buy"][1]["seat"].startswith("[外资]")
    assert d["sell"][0]["amount_wan"] == 5000.0


def test_get_lhb_seats_not_on_board(monkeypatch):
    monkeypatch.setattr(
        lhb, "stock_lhb_stock_detail_em", lambda symbol, date, flag: pd.DataFrame()
    )
    out = json.loads(lhb.get_lhb_seats("600825", "2026-09-02"))
    assert not out["success"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_lhb.py -v -k seats`
Expected: 2 FAILED（AttributeError: get_lhb_seats）

- [ ] **Step 3: Write minimal implementation**

`utils/tools.py` 的 `install_patch(...)` 中 `hook_domains` 列表末尾追加一项（`"searchapi.eastmoney.com/api/suggest/get"` 之后）：

```python
        hook_domains=[
          "fund.eastmoney.com",
          "push2.eastmoney.com",
          "push2his.eastmoney.com",
          "emweb.securities.eastmoney.com",
          "searchapi.eastmoney.com/api/suggest/get",
          "datacenter-web.eastmoney.com",
        ],
```

`utils/lhb.py` 追加：

```python
def _classify_seat(name: str) -> str:
    if "机构专用" in name:
        return "[机构]"
    if "股通专用" in name:
        return "[外资]"
    return "[游资]"


@RateLimiter(max_calls=10, time_window=60)
@with_retry(max_retries=2, delay=1.0)
def get_lhb_seats(symbol: str, date: str = "") -> str:
    """查询个股龙虎榜席位明细（买/卖前5；金额万元，带机构/外资/游资分类）

    Args:
        symbol: 6位股票代码
        date: YYYY-MM-DD 默认今天。当日席位约17:00后陆续齐全，建议>=18:00查询
    """
    try:
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        d_compact = day.strftime("%Y%m%d")
        sides, notes = {}, []
        for flag, label in (("买入", "buy"), ("卖出", "sell")):
            try:
                df = stock_lhb_stock_detail_em(symbol=symbol, date=d_compact, flag=flag)
            except Exception as e:  # noqa: BLE001
                notes.append(f"{flag}方席位拉取失败: {e}")
                continue
            if df is None or df.empty:
                continue
            exact = "买入金额" if flag == "买入" else "卖出金额"
            amt_col = exact if exact in df.columns else next(
                (c for c in df.columns if "金额" in str(c)), None
            )
            name_col = "营业部名称" if "营业部名称" in df.columns else next(
                (c for c in df.columns if "营业部" in str(c)
                 or "OPERATEDEPT" in str(c).upper()), None
            )
            if amt_col is None or name_col is None:
                notes.append(f"{flag}方席位列结构异常: {list(df.columns)}")
                continue
            seats = []
            for _, r in df.head(5).iterrows():
                nm = str(r[name_col] or "?")
                seats.append(
                    {
                        "seat": _classify_seat(nm) + nm,
                        "amount_wan": safe_num(
                            pd.to_numeric(r[amt_col], errors="coerce") / 1e4, 0
                        ),
                    }
                )
            sides[label] = seats
        if not sides:
            return json_err(f"{symbol} 在 {day} 无龙虎榜席位明细(可能未上榜)")
        return json_ok({"symbol": symbol, "date": str(day), **sides, "notes": notes})
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_lhb_seats: %s", e)
        return json_err(f"查询席位明细失败: {e}")
```

`server.py` import 块插入 `get_lhb_seats,`，注册：

```python
@mcp.tool()
def get_lhb_seats_tool(symbol: str, date: str = "") -> str:
    """查询个股龙虎榜席位明细（买/卖前5，万元，机构/外资/游资分类）

    Args:
        symbol: 6位股票代码
        date: YYYY-MM-DD 默认今天；当日席位>=18:00才齐全
    """
    return get_lhb_seats(symbol, date)
```

`utils/__init__.py`：改为 `from .lhb import get_lhb, get_lhb_seats`，`__all__` 加 `"get_lhb_seats"`。

`README.md` 工具列表追加：

```markdown
### `get_lhb_seats_tool`

查询个股龙虎榜席位明细（akshare 东财数据源，买/卖前 5 席位，金额万元，带机构/外资/游资分类）。当日数据建议 18:00 后查询。
```

- [ ] **Step 4: Run test + 本地实网冒烟（验证 akproxy 放行 datacenter-web）**

Run: `.venv/Scripts/python.exe -m pytest tests/test_lhb.py -v`
Expected: 全部 PASS

实网冒烟（**本任务的关键前置验证**，spec §10）：

```bash
.venv/Scripts/python.exe -c "
from utils import get_lhb_seats
import json
out = json.loads(get_lhb_seats('600825', '2026-10-16'))
print(out['success'], str(out)[:300])
"
```

Expected: `True`（若 False 且错误含连接失败，说明 akproxy 不放行 datacenter-web：在 `utils/lhb.py` 中将 `stock_lhb_stock_detail_em` 替换为 `temp/lhb_fetch.py` 的裸 URL 直连实现（requests，同域名同 reportName），并提交备注说明。）

- [ ] **Step 5: Commit**

```bash
git add utils/tools.py utils/lhb.py utils/__init__.py server.py README.md tests/test_lhb.py
git commit -m "feat: add lhb seats tool via akshare with datacenter-web hooked"
```

---

### Task 7: 部署与冒烟

**Files:**
- Create: `scripts/smoke_review_gaps.py`

**Interfaces:**
- Consumes: 全部新工具（utils 层直调，导入 utils 即安装代理 patch）。
- Produces: 部署后的验证记录；镜像 `docker.sihanfu.cn:15999/mcp-stock-server:latest` 更新。

- [ ] **Step 1: Write smoke script**

Create `scripts/smoke_review_gaps.py`：

```python
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
```

- [ ] **Step 2: 本地跑通全量测试 + 冒烟**

Run: `.venv/Scripts/python.exe -m pytest tests -v`
Expected: 全部 PASS

Run: `.venv/Scripts/python.exe scripts/smoke_review_gaps.py`
Expected: 8 项全 OK（auction 归档回读应显示 `source: archive`）。注：非交易日运行时 auction 两项预期 FAIL 属正常，换交易日重跑。

- [ ] **Step 3: 构建并推送镜像**

```bash
docker build -t docker.sihanfu.cn:15999/mcp-stock-server:latest .
docker push docker.sihanfu.cn:15999/mcp-stock-server:latest
```

Expected: push 成功（需本机已 `docker login docker.sihanfu.cn:15999`）

- [ ] **Step 4: 远程拉起（用户执行）**

远程主机：

```bash
docker compose pull && docker compose up -d
```

- [ ] **Step 5: 容器内冒烟（验证 datacenter-web 经 akproxy、归档卷挂载）**

远程主机：

```bash
docker exec mcp-stock python scripts/smoke_review_gaps.py
docker exec mcp-stock ls /app/data/auction
```

Expected: 8 项全 OK；归档目录存在。若席位项失败 → 按 Task 6 Step 4 的备选路径处理。

- [ ] **Step 6: Commit**

```bash
git add scripts/smoke_review_gaps.py
git commit -m "chore: add smoke script for review gap tools"
```
