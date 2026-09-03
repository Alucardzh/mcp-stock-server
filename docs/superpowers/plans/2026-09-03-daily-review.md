# A股每日复盘工具 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 StockMCP 中新增"一键复盘"聚合工具 `get_daily_review_tool` 与 6 个细分工具，覆盖指数/情绪/主力资金/国家队ETF/期指基差与席位/期权PCR/两融七模块。

**Architecture:** 方案 A——`utils/` 下平铺新增 3 个域模块（review_market / review_funds / review_derivatives）+ 1 个聚合器（review.py）+ 共享小工具（review_common.py），无状态实时拉取；聚合用 ThreadPoolExecutor 并行、模块级降级、10 分钟缓存。国家队复用现有 `etf.py`（经 JSON 解析，不改动该文件）。

**Tech Stack:** Python 3.11+ / akshare 1.18.30（东财接口经 akshare-proxy-patch 0.5.0 走代理，其余直连）/ requests / pandas / pytest（已安装 9.0.2，尚无 tests 目录）/ FastMCP（仅 server.py 注册）。

## Global Constraints

- **命名约定（重要，避免递归陷阱）**：utils 层函数**不带** `_tool` 后缀（如 `get_zt_pool`），server.py 注册的 MCP 工具**带** `_tool` 后缀（如 `get_zt_pool_tool`）并在函数体内调用 utils 版本——与现有 `get_etf_daily` / `get_etf_daily_tool` 模式一致。
- 所有 MCP 工具返回 JSON 字符串，成功信封 `{"success": true, "data": {...}}`，失败 `{"success": false, "error": "..."}`（与 `etf.py` 一致）。
- 金额输出单位统一为**亿元**（`_yi` 后缀），保留 2 位小数（指数成交额保留 0 位）。
- akshare 函数一律 `from akshare import xxx` 顶部导入（便于测试 monkeypatch 模块命名空间），不用 `import akshare as ak`。
- 网络入口函数复用 `utils/tools.py` 的 `RateLimiter(max_calls=10, time_window=60)` 与 `with_retry(max_retries=3, delay=1.0, backoff=2.0)` 装饰器（与 `etf.py` 相同用法）；`get_daily_review` 只用 `RateLimiter(max_calls=6, time_window=60)`（内部已多次限流，避免嵌套 with_retry 重试风暴）。
- 席位数据口径：经纪席位（`(代客)` 后缀），仅前 20 会员可见，缺失方向按 0 计，输出中注明。
- 模块级降级约定：section 函数返回 dict 内可含 `"notes": [str]`，聚合器弹出并加 `[模块名]` 前缀；section 抛异常 → 聚合器捕获进 `errors[模块名]`，整体不失败。
- `etf.py` 保持不动（用户未提交的工作，Task 0 单独提交）；`review_common.safe_num` 与其私有 `_num` 语义一致，允许这处小重复。
- 每个任务 TDD：先写失败测试 → 实现 → 通过 → commit（conventional commits）。
- 测试命令统一用 `./.venv/Scripts/python.exe -m pytest ...`（Windows Git Bash 环境，`uv`/`pytest` 不在 PATH）。
- `pytest.ini_options`: `testpaths=["tests"]`、`addopts="-m 'not live'"`、注册 `live` marker。
- 聚合器内 section 函数引用必须在**函数体内**解析模块全局名（jobs 列表在函数体内构建），保证测试 monkeypatch 生效；**禁止**用模块级常量持有 section 函数引用。

## 已验证的数据接口事实（实现时直接依赖，勿再猜测）

- `index_zh_a_hist(symbol, period="daily", start_date, end_date)`：列 `日期/开盘/收盘/最高/最低/成交量/成交额/振幅/涨跌幅/涨跌额/换手率`（东财，走 proxy）。
- `stock_zt_pool_em(date="20260902")`：列 `序号/代码/名称/涨跌幅/最新价/成交额/流通市值/总市值/换手率/封板资金/首次封板时间/最后封板时间/炸板次数/涨停统计/连板数/所属行业`；`stock_zt_pool_zbgc_em` 有 `涨停价/涨速/振幅` 无 `连板数`；`stock_zt_pool_dtgc_em` 有 `封单资金/板上成交额/连续跌停/开板次数` 无 `连板数`（东财，走 proxy）。
- `stock_zh_a_spot_em()`：列含 `代码/名称/涨跌幅/...`（东财，走 proxy；乐咕 `stock_market_activity_legu` 已失效勿用）。
- `stock_market_fund_flow()`：历史序列，列 `日期/上证-涨跌幅/深证-涨跌幅/主力净流入-净额/主力净流入-净占比/超大单净流入-净额/...`（元，东财，走 proxy）。
- `stock_sector_fund_flow_rank(indicator="今日"|"5日"|"10日", sector_type="行业资金流")`：列 `名称/今日涨跌幅/今日主力净流入-净额/...`（指标前缀随 indicator 变化，用子串匹配列名）。
- `stock_margin_sse(start_date, end_date)`：区间表，日期列为 `信用交易日期`，含 `融资余额/融券余额`（元，上交所直连）；`stock_margin_szse(date="20260902")`：单日表，含 `日期` 与 `融资余额`（深交所直连，非交易日抛异常）。
- `get_cffex_daily(date="20260902")`：当日**全部品种全部合约**，列 `symbol/date/open/high/low/close/volume/open_interest/turnover/settle/pre_settle/variety`（中金所直连）。
- `stock_zh_index_daily(symbol="sh000300")`：新浪直连，列 `date/open/high/low/close/volume`，全历史。
- `option_daily_stats_sse(date="20260902")`：列 `合约标的代码/合约标的名称/合约数量/总成交额/总成交量/认购成交量/认沽成交量/认沽/认购/未平仓合约总数/未平仓认购合约数/未平仓认沽合约数/交易日`；`option_daily_stats_szse(date)`：列 `合约标的代码/合约标的名称/成交量/认购成交量/认沽成交量/认沽/认购持仓比/未平仓合约总数/未平仓认购合约数/未平仓认沽合约数/交易日`（交易所直连，非交易日返回空）。
- `get_cffex_rank_table(date="20260902", vars_list=["IF"])`：返回 `dict{合约: DataFrame}`，列 `rank/vol_party_name/vol/vol_chg/long_party_name/long_open_interest/long_open_interest_chg/short_party_name/short_open_interest/short_open_interest_chg/symbol/var/date`（中金所直连；非交易日返回 `{}`）。
- 股指期权席位 CSV：`http://www.cffex.com.cn/sj/ccpm/YYYYMM/DD/{IO|MO|HO}_1.csv`，GBK，前 2 行为双行表头，数据列依次为 `交易日,合约系列,排名,会员简称,成交量,比上一交易日增减,会员简称,持买单量,比上一交易日增减,会员简称,持卖单量,比上一交易日增减`；404/无数据 → 回退前一交易日。
- 2026-09-02 是周三，9 月第三个周五 = 2026-09-18，12 月第三个周五 = 2026-12-18（年化基差测试用）。

## File Structure

```txt
conftest.py                      # 空，确保 pytest 把项目根加入 sys.path
tests/
├── test_review_common.py
├── test_review_market.py
├── test_review_funds.py
├── test_review_derivatives.py
├── test_review.py
└── test_live_smoke.py           # @pytest.mark.live，默认跳过
utils/
├── review_common.py             # safe_num/json 信封/parse_day/norm_date/prev_trading_days/col_like
├── review_market.py             # indices_section/breadth_section/get_market_breadth/get_zt_pool
├── review_funds.py              # market/sector/fund_flow/margin sections + get_fund_flow/get_margin
├── review_derivatives.py        # basis/pcr/cffex rank sections + get_index_derivatives/get_cffex_rank
└── review.py                    # get_daily_review（并行聚合+缓存）
server.py                        # 注册 7 个新 @mcp.tool（*_tool 后缀包装）
README.md                        # 工具文档
```

**Interfaces（跨任务契约）:**

- `review_common`: `safe_num(value, ndigits=2) -> float | None`；`json_ok(data: dict) -> str`；`json_err(msg: str) -> str`；`parse_day(date_str: str) -> date | None`（None=格式非法，不判断未来）；`norm_date(s) -> str`（"20260902"→"2026-09-02"）；`prev_trading_days(day: date, n: int = 6) -> list[date]`；`col_like(df, keyword: str) -> str | None`。
- `review_market`: `indices_section(day: date) -> dict`；`breadth_section(day: date) -> dict`；`get_market_breadth(date: str = "") -> str`；`get_zt_pool(pool: str = "涨停", date: str = "") -> str`。
- `review_funds`: `market_fund_flow_section(day: date) -> dict`；`sector_fund_flow_section(day: date, indicator: str = "今日") -> dict`；`fund_flow_section(day: date) -> dict`；`margin_section(day: date, days: int = 10, market: str = "沪深") -> dict`；`get_fund_flow(scope: str = "全部", indicator: str = "今日", date: str = "") -> str`；`get_margin(market: str = "沪深", days: int = 10, date: str = "") -> str`。
- `review_derivatives`: `basis_section(day: date) -> dict`；`pcr_section(day: date) -> dict`；`derivatives_section(day: date) -> dict`；`get_index_derivatives(date: str = "") -> str`；`get_cffex_rank(var: str = "IF", date: str = "", member: str = "") -> str`；`fetch_option_rank_csv(var: str, day: date) -> pd.DataFrame | None`；`_summarize_members(df) -> list[dict]`。
- `review`: `get_daily_review(date: str = "") -> str`。
- `server.py` 注册名（MCP 对外）：`get_daily_review_tool`、`get_market_breadth_tool`、`get_zt_pool_tool`、`get_fund_flow_tool`、`get_margin_tool`、`get_cffex_rank_tool`、`get_index_derivatives_tool`。

---

### Task 0: 工作区整理（前置条件）

**Files:** 无新增，只提交用户已有的未提交改动。

**Interfaces:** 无代码接口；产出 = 干净的工作区，后续任务的 commit 不会混入无关改动。

- [ ] **Step 1: 确认当前未提交改动内容**

Run: `cd D:/DevProject/StockMCP && git status --short`
Expected: `M README.md`、`M server.py`、`M utils/__init__.py`、`?? utils/etf.py`（ETF 功能的完整未提交工作）。

- [ ] **Step 2: 快速核对改动确属 ETF 功能**

Run: `git diff --stat && git diff server.py | head -40`
Expected: 改动均为 `get_etf_daily_tool` 注册、`utils/__init__.py` 导出、README 的 ETF 文档。若发现无关改动，停下来问用户，不要盲目提交。

- [ ] **Step 3: 作为一个独立 commit 提交**

```bash
git add README.md server.py utils/__init__.py utils/etf.py
git commit -m "feat: add ETF daily data tool with national-team preset"
```

---

### Task 1: 测试基建 + review_common.py

**Files:**
- Create: `conftest.py`
- Create: `utils/review_common.py`
- Create: `tests/test_review_common.py`
- Modify: `pyproject.toml`（文件末尾追加 pytest 配置）

**Interfaces:**
- Consumes: 无
- Produces: Task 2-7 全部依赖的 `safe_num/json_ok/json_err/parse_day/norm_date/prev_trading_days/col_like`。

- [ ] **Step 1: 写失败测试**

`tests/test_review_common.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_review_common.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'utils.review_common'` 或收集错误）。

- [ ] **Step 3: 建 conftest.py 与 pytest 配置**

`conftest.py`（项目根，空文件仅注释）：

```python
# 确保 pytest 以 prepend 模式把项目根加入 sys.path，使 `import utils` 可用。
```

`pyproject.toml` 末尾追加：

```toml

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-m 'not live'"
markers = [
    "live: 真实接口冒烟测试（默认跳过，显式 -m live 运行）",
]
```

- [ ] **Step 4: 实现 review_common.py**

`utils/review_common.py`：

```python
#!/usr/bin/env python3
"""复盘模块共享小工具：数值安全转换、JSON 信封、日期解析与交易日回退。"""

from datetime import date as date_type, datetime, timedelta
import json


def safe_num(value, ndigits: int = 2):
    """安全数值转换：NaN/None/异常 -> None，否则四舍五入（-0.0 归一为 0.0）"""
    try:
        if value is None:
            return None
        v = float(value)
        if v != v:  # NaN
            return None
        if v == 0:
            v = 0.0
        return round(v, ndigits)
    except (TypeError, ValueError):
        return None


def json_ok(data) -> str:
    return json.dumps({"success": True, "data": data}, ensure_ascii=False, indent=2)


def json_err(msg: str) -> str:
    return json.dumps({"success": False, "error": msg}, ensure_ascii=False, indent=2)


def parse_day(date_str: str) -> date_type | None:
    """解析 YYYY-MM-DD；空串=今天；格式非法返回 None（不判断未来，由调用方检查）"""
    s = (date_str or "").strip()
    if not s:
        return datetime.now().date()
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def norm_date(s) -> str:
    """日期归一化为 YYYY-MM-DD：兼容 '2026-09-02' / '20260902' / int"""
    t = str(s).strip()
    if "-" in t:
        return t[:10]
    if len(t) >= 8 and t[:8].isdigit():
        return f"{t[:4]}-{t[4:6]}-{t[6:8]}"
    return t


def prev_trading_days(day: date_type, n: int = 6) -> list[date_type]:
    """返回 day 之前最近的 n 个工作日（跳过周末，不含节假日判断）"""
    out, d = [], day - timedelta(days=1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out


def col_like(df, keyword: str) -> str | None:
    """按子串找列名（各数据源指标前缀随 indicator 变化，不能精确匹配）"""
    for c in df.columns:
        if keyword in str(c):
            return c
    return None
```

- [ ] **Step 5: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_review_common.py -v`
Expected: 6 passed。

- [ ] **Step 6: Commit**

```bash
git add conftest.py pyproject.toml utils/review_common.py tests/test_review_common.py
git commit -m "feat: add review_common helpers and pytest scaffolding"
```

---

### Task 2: review_market.py — 指数概览

**Files:**
- Create: `utils/review_market.py`
- Test: `tests/test_review_market.py`

**Interfaces:**
- Consumes: `review_common.safe_num/json_err/json_ok/parse_day`，`tools.RateLimiter/with_retry/CachedData`
- Produces: `indices_section(day: date) -> dict`（Task 7 聚合器消费）；模块内缓存 `_get_spot()` 供 Task 3 复用。

- [ ] **Step 1: 写失败测试**

`tests/test_review_market.py`：

```python
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
        return _hist_df("2026-09-02") if symbol in ("000001", "399001") else _hist_df("2026-09-01")

    monkeypatch.setattr(rm, "index_zh_a_hist", fake)
    out = rm.indices_section(date(2026, 9, 2))
    assert len(out["items"]) == 2
    assert len(out["notes"]) == 3


def test_indices_section_all_missing(monkeypatch):
    monkeypatch.setattr(rm, "index_zh_a_hist", lambda symbol, **kw: _hist_df("2026-09-01"))
    with pytest.raises(ValueError):
        rm.indices_section(date(2026, 9, 2))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_review_market.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'utils.review_market'`）。

- [ ] **Step 3: 实现 review_market.py（本任务先写指数部分 + spot 缓存 + 两个工具占位导入）**

`utils/review_market.py`：

```python
#!/usr/bin/env python3
"""
复盘·市场概况模块：指数概览 + 涨跌结构/涨停生态。

数据源（akshare）：
- index_zh_a_hist          东财指数日行情（收盘后含当日）
- stock_zt_pool_*          东财涨停/炸板/跌停/强势/昨涨停股池
- stock_zh_a_spot_em       东财全市场快照（当日涨跌家数；乐咕接口已失效后的替代）

口径说明：
- 涨跌家数仅当日可算（全市场快照无历史），历史日期在 notes 降级说明。
"""

from datetime import date as date_type, datetime, timedelta
import logging

import pandas as pd

from akshare import (
    index_zh_a_hist,
    stock_zh_a_spot_em,
    stock_zt_pool_dtgc_em,
    stock_zt_pool_em,
    stock_zt_pool_previous_em,
    stock_zt_pool_strong_em,
    stock_zt_pool_zbgc_em,
)

from .review_common import json_err, json_ok, parse_day, prev_trading_days, safe_num
from .tools import CachedData, RateLimiter, with_retry

logger = logging.getLogger(__name__)

REVIEW_INDEXES = [
    ("000001", "上证指数"),
    ("399001", "深证成指"),
    ("399006", "创业板指"),
    ("000688", "科创50"),
    ("899050", "北证50"),
]

MAX_POOL_ROWS = 60

_spot_cache: CachedData | None = None


def _get_spot() -> pd.DataFrame:
    """全市场快照（缓存 300 秒）"""
    global _spot_cache
    if _spot_cache is not None and not _spot_cache.is_expired():
        return _spot_cache.data
    df = stock_zh_a_spot_em()
    _spot_cache = CachedData(df, ttl=300)
    return df


def indices_section(day: date_type) -> dict:
    """① 指数概览：五大指数收盘/涨跌幅/成交额（亿元）"""
    notes, items = [], []
    start = (day - timedelta(days=14)).strftime("%Y%m%d")
    end = day.strftime("%Y%m%d")
    for code, name in REVIEW_INDEXES:
        try:
            df = index_zh_a_hist(
                symbol=code, period="daily", start_date=start, end_date=end
            )
        except Exception as e:  # noqa: BLE001
            notes.append(f"{name}({code}) 行情获取失败: {e}")
            continue
        if df is None or df.empty or str(df["日期"].iloc[-1])[:10] != str(day):
            notes.append(f"{name}({code}) 在 {day} 无数据(可能非交易日)")
            continue
        r = df.iloc[-1]
        items.append(
            {
                "code": code,
                "name": name,
                "close": safe_num(r["收盘"], 2),
                "chg_pct": safe_num(r["涨跌幅"], 2),
                "amount_yi": safe_num(float(r["成交额"]) / 1e8, 0),
            }
        )
    if not items:
        raise ValueError(f"{day} 未获取到任何指数行情(可能非交易日)")
    return {"date": str(day), "items": items, "notes": notes}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_review_market.py -v`
Expected: 3 passed。

- [ ] **Step 5: Commit**

```bash
git add utils/review_market.py tests/test_review_market.py
git commit -m "feat: add indices overview section for daily review"
```

---

### Task 3: review_market.py — 涨跌结构与涨停生态（含 2 个工具函数）

**Files:**
- Modify: `utils/review_market.py`（追加）
- Test: `tests/test_review_market.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `_get_spot`；`review_common.prev_trading_days`（Task 2 已导入）。
- Produces: `breadth_section(day) -> dict`（Task 7 消费）；`get_market_breadth / get_zt_pool`（Task 8 注册）。

- [ ] **Step 1: 追加失败测试**

`tests/test_review_market.py` 追加：

```python
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
        "stock_zh_a_spot_em",
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_review_market.py -v`
Expected: 新增用例 FAIL（`AttributeError: ... no attribute 'breadth_section'`）。

- [ ] **Step 3: 追加实现**

`utils/review_market.py` 追加：

```python
# 延迟绑定：lambda 在调用时从模块全局取函数，保证测试 monkeypatch 可生效
POOL_FETCHERS = {
    "涨停": lambda date: stock_zt_pool_em(date=date),
    "炸板": lambda date: stock_zt_pool_zbgc_em(date=date),
    "跌停": lambda date: stock_zt_pool_dtgc_em(date=date),
    "强势": lambda date: stock_zt_pool_strong_em(date=date),
    "昨涨停": lambda date: stock_zt_pool_previous_em(date=date),
}

MONEY_FIELDS = {"成交额", "封板资金", "封单资金", "板上成交额", "流通市值", "总市值"}

_POOL_FIELDS = {
    "涨停": ["代码", "名称", "涨跌幅", "最新价", "成交额", "换手率", "封板资金",
             "炸板次数", "涨停统计", "连板数", "所属行业"],
    "炸板": ["代码", "名称", "涨跌幅", "最新价", "成交额", "换手率", "炸板次数",
             "涨停统计", "所属行业"],
    "跌停": ["代码", "名称", "涨跌幅", "最新价", "成交额", "换手率", "封单资金",
             "板上成交额", "连续跌停", "开板次数", "所属行业"],
    "强势": ["代码", "名称", "涨跌幅", "最新价", "成交额", "换手率", "涨停统计",
             "连板数", "所属行业"],
    "昨涨停": ["代码", "名称", "涨跌幅", "最新价", "成交额", "换手率", "涨停统计",
               "连板数", "所属行业"],
}


def _pool_rows(pool: str, day: date_type) -> pd.DataFrame | None:
    fetcher = POOL_FETCHERS[pool]
    try:
        return fetcher(date=day.strftime("%Y%m%d"))
    except Exception as e:  # noqa: BLE001
        logger.warning("%s 股池(%s) failed: %s", pool, day, e)
        return None


def _pool_with_fallback(
    pool: str, day: date_type
) -> tuple[pd.DataFrame | None, date_type, list[str]]:
    notes = []
    for d in [day, *prev_trading_days(day, 5)]:
        df = _pool_rows(pool, d)
        if df is not None and not df.empty:
            if d != day:
                notes.append(f"{day} 无数据，返回最近交易日 {d} 的{pool}股池")
            return df, d, notes
    return None, day, notes


def breadth_section(day: date_type) -> dict:
    """② 涨跌结构：涨跌家数(仅当日) + 涨停/跌停/炸板家数 + 连板梯队"""
    notes = []
    out = {
        "date": str(day),
        "up": None,
        "down": None,
        "flat": None,
        "limit_up": None,
        "limit_down": None,
        "zhaban": None,
        "max_lianban": None,
        "lianban_dist": None,
    }
    zt = _pool_rows("涨停", day)
    if zt is None or zt.empty:
        notes.append(f"{day} 无涨停池数据(可能非交易日)")
    else:
        out["limit_up"] = len(zt)
        lianban = pd.to_numeric(zt.get("连板数"), errors="coerce")
        out["max_lianban"] = int(lianban.max()) if lianban.notna().any() else 0
        out["lianban_dist"] = {
            f"{int(k)}板": int(v)
            for k, v in lianban.value_counts().items()
            if k >= 2
        }
    for key, pool in (("zhaban", "炸板"), ("limit_down", "跌停")):
        df = _pool_rows(pool, day)
        if df is not None:
            out[key] = 0 if df.empty else len(df)
    if day == datetime.now().date():
        try:
            spot = _get_spot()
            chg = pd.to_numeric(spot["涨跌幅"], errors="coerce")
            out["up"] = int((chg > 0).sum())
            out["down"] = int((chg < 0).sum())
            out["flat"] = int((chg == 0).sum())
        except Exception as e:  # noqa: BLE001
            notes.append(f"全市场快照获取失败，涨跌家数缺失: {e}")
    else:
        notes.append("涨跌家数仅支持当日(全市场快照无历史)")
    # 空数据(None=拉取失败或 0 行)视为无数据：非交易日三大池应全空
    if out["limit_up"] is None and not out["zhaban"] and not out["limit_down"]:
        raise ValueError(f"{day} 涨停/炸板/跌停池均无数据(可能非交易日)")
    return {**out, "notes": notes}


@RateLimiter(max_calls=10, time_window=60)
@with_retry(max_retries=3, delay=1.0, backoff=2.0)
def get_market_breadth(date: str = "") -> str:
    """查询市场涨跌结构：涨跌家数(仅当日)、涨停/跌停/炸板家数、连板梯队

    Args:
        date: 查询日期 YYYY-MM-DD，默认今天。历史日期时涨跌家数不可用(降级说明)。
    """
    try:
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        if day > datetime.now().date():
            return json_err(f"查询日期 {day} 晚于今天，无法查询未来数据")
        return json_ok(breadth_section(day))
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_market_breadth: %s", e)
        return json_err(f"查询涨跌结构失败: {e}")


@RateLimiter(max_calls=10, time_window=60)
@with_retry(max_retries=3, delay=1.0, backoff=2.0)
def get_zt_pool(pool: str = "涨停", date: str = "") -> str:
    """查询涨停生态股池明细（金额字段单位: 亿元）

    Args:
        pool: 涨停/炸板/跌停/强势/昨涨停，默认"涨停"
        date: 查询日期 YYYY-MM-DD，默认今天；无数据时自动回退最近交易日
    """
    try:
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        key = (pool or "").strip()
        if key not in POOL_FETCHERS:
            return json_err(f"pool 仅支持 {'/'.join(POOL_FETCHERS)}，当前: {pool}")
        df, actual_day, notes = _pool_with_fallback(key, day)
        if df is None or df.empty:
            return json_err(f"{day} 前后数个交易日内无{key}股池数据")
        fields = [c for c in _POOL_FIELDS[key] if c in df.columns]
        rows = []
        for _, r in df.head(MAX_POOL_ROWS).iterrows():
            row = {}
            for c in fields:
                if c in MONEY_FIELDS:
                    row[c] = safe_num(float(r[c]) / 1e8, 2)
                elif c in ("涨跌幅", "换手率"):
                    row[c] = safe_num(r[c], 2)
                else:
                    row[c] = r[c]
            rows.append(row)
        return json_ok(
            {
                "date": str(actual_day),
                "pool": key,
                "count": len(df),
                "items": rows,
                "notes": notes,
            }
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_zt_pool: %s", e)
        return json_err(f"查询{pool}股池失败: {e}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_review_market.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add utils/review_market.py tests/test_review_market.py
git commit -m "feat: add market breadth and limit-up pool sections with tool functions"
```

---

### Task 4: review_funds.py — 主力资金与两融（含 2 个工具函数）

**Files:**
- Create: `utils/review_funds.py`
- Test: `tests/test_review_funds.py`

**Interfaces:**
- Consumes: `review_common`（全部）、`tools.RateLimiter/with_retry`
- Produces: `market_fund_flow_section / sector_fund_flow_section / fund_flow_section / margin_section`（Task 7 消费）；`get_fund_flow / get_margin`（Task 8 注册）。

- [ ] **Step 1: 写失败测试**

`tests/test_review_funds.py`：

```python
from datetime import date

import pandas as pd
import pytest

from utils import review_funds as rf


def _mff_df(day="2026-09-02"):
    return pd.DataFrame(
        {
            "日期": [day],
            "上证-涨跌幅": [1.2],
            "深证-涨跌幅": [1.5],
            "主力净流入-净额": [-3.5e10],
            "主力净流入-净占比": [-2.1],
            "超大单净流入-净额": [-1.0e10],
            "超大单净流入-净占比": [-0.6],
            "大单净流入-净额": [-2.5e10],
            "大单净流入-净占比": [-1.5],
            "中单净流入-净额": [1.0e10],
            "中单净流入-净占比": [0.6],
            "小单净流入-净额": [2.5e10],
            "小单净流入-净占比": [1.5],
        }
    )


def _sector_df():
    return pd.DataFrame(
        {
            "名称": ["计算机", "银行", "白酒"],
            "今日涨跌幅": [2.0, -0.5, -1.0],
            "今日主力净流入-净额": [5.0e10, -3.0e10, -4.0e10],
            "今日主力净流入-净占比": [3.0, -2.0, -3.0],
        }
    )


def _sse_margin_df():
    return pd.DataFrame(
        {
            "信用交易日期": ["2026-09-01", "2026-09-02"],
            "融资余额": [9.0e12, 9.1e12],
            "融券余额": [5.0e10, 5.2e10],
        }
    )


def _szse_margin_df(compact):
    data = {
        "20260901": 7.0e12,   # 周二
        "20260902": 7.05e12,  # 周三
    }
    return pd.DataFrame({"日期": [compact], "融资余额": [data[compact]]})


def test_market_fund_flow_section(monkeypatch):
    monkeypatch.setattr(rf, "stock_market_fund_flow", lambda: _mff_df())
    out = rf.market_fund_flow_section(date(2026, 9, 2))
    assert out["main_net_yi"] == -350.0
    assert out["super_large_net_yi"] == -100.0
    assert out["large_net_yi"] == -250.0


def test_market_fund_flow_section_missing_day(monkeypatch):
    monkeypatch.setattr(rf, "stock_market_fund_flow", lambda: _mff_df("2026-09-01"))
    with pytest.raises(ValueError):
        rf.market_fund_flow_section(date(2026, 9, 2))


def test_sector_fund_flow_history_degrades():
    out = rf.sector_fund_flow_section(date(2026, 9, 2), "今日")
    assert out["items"] is None
    assert any("仅支持当日" in n for n in out["notes"])


def test_sector_fund_flow_today(monkeypatch):
    class FakeDT:
        @staticmethod
        def now():
            class _T:
                @staticmethod
                def date():
                    return date(2026, 9, 3)

            return _T()

    monkeypatch.setattr(rf, "datetime", FakeDT)
    monkeypatch.setattr(
        rf, "stock_sector_fund_flow_rank", lambda indicator, sector_type: _sector_df()
    )
    out = rf.sector_fund_flow_section(date(2026, 9, 3), "今日")
    assert out["top5"][0]["name"] == "计算机"
    assert out["top5"][0]["main_net_yi"] == 500.0
    assert out["bottom5"][0]["name"] == "白酒"


def test_margin_section(monkeypatch):
    monkeypatch.setattr(
        rf, "stock_margin_sse", lambda start_date, end_date: _sse_margin_df()
    )
    monkeypatch.setattr(
        rf,
        "stock_margin_szse",
        lambda date: _szse_margin_df(date) if date in ("20260901", "20260902")
        else (_ for _ in ()).throw(ValueError("no data")),
    )
    out = rf.margin_section(date(2026, 9, 2), days=5, market="沪深")
    assert out["date"] == "2026-09-02"
    # 最新日: 沪 9.1e12 + 深 7.05e12；前一日: 沪 9.0e12 + 深 7.0e12
    assert out["rzye_yi"] == round((9.1e12 + 7.05e12) / 1e8, 2)
    assert out["rzye_chg_yi"] == round((0.1e12 + 0.05e12) / 1e8, 2)
    assert len(out["series"]) == 2


def test_fund_flow_section_combined(monkeypatch):
    monkeypatch.setattr(rf, "stock_market_fund_flow", lambda: _mff_df())
    out = rf.fund_flow_section(date(2026, 9, 2))
    assert out["market"]["main_net_yi"] == -350.0
    assert out["sector"]["items"] is None  # 历史日期板块降级
    assert any("[sector]" in n for n in out["notes"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_review_funds.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现 review_funds.py**

`utils/review_funds.py`：

```python
#!/usr/bin/env python3
"""
复盘·资金模块：大盘/板块主力资金 + 两融余额。

数据源（akshare）：
- stock_market_fund_flow       东财大盘资金流历史序列（含当日）
- stock_sector_fund_flow_rank  东财板块资金流排名（仅 今日/5日/10日 口径）
- stock_margin_sse/szse        沪深交易所两融（T-1 披露）

口径说明：
- 主力净流入为东方财富口径（超大单+大单净额），推断口径非真实机构。
- 两融余额为交易所披露口径，隔日发布（T-1）。
"""

from datetime import date as date_type, datetime, timedelta
import logging

import pandas as pd

from akshare import (
    stock_margin_sse,
    stock_margin_szse,
    stock_market_fund_flow,
    stock_sector_fund_flow_rank,
)

from .review_common import (
    col_like,
    json_err,
    json_ok,
    norm_date,
    parse_day,
    prev_trading_days,
    safe_num,
)
from .tools import RateLimiter, with_retry

logger = logging.getLogger(__name__)

INDICATORS = ("今日", "5日", "10日")


def _pick(df: pd.DataFrame, r: pd.Series, keyword: str, ndigits: int = 2, div: float = 1.0):
    """取列并安全转数值：先精确匹配列名，再子串回退（"大单净流入-净额"是
    "超大单净流入-净额"的子串，纯子串匹配会误绑定），列缺失返回 None"""
    c = keyword if keyword in df.columns else col_like(df, keyword)
    if c is None:
        return None
    try:
        return safe_num(float(r[c]) / div, ndigits)
    except (TypeError, ValueError):
        return None


def market_fund_flow_section(day: date_type) -> dict:
    """③ 大盘主力资金（取历史序列中目标日行，单位亿元）"""
    df = stock_market_fund_flow()
    if df is None or df.empty:
        raise ValueError("大盘资金流接口无数据")
    col_date = col_like(df, "日期")
    hit = df[df[col_date].astype(str).str[:10] == str(day)]
    if hit.empty:
        raise ValueError(f"{day} 无大盘资金流数据(可能非交易日)")
    r = hit.iloc[0]
    return {
        "date": str(day),
        "main_net_yi": _pick(df, r, "主力净流入-净额", div=1e8),
        "super_large_net_yi": _pick(df, r, "超大单净流入-净额", div=1e8),
        "large_net_yi": _pick(df, r, "大单净流入-净额", div=1e8),
        "sh_chg_pct": _pick(df, r, "上证-涨跌幅"),
        "sz_chg_pct": _pick(df, r, "深证-涨跌幅"),
        "notes": ["主力口径: 东财超大单+大单净额(推断口径, 非真实机构)"],
    }


def sector_fund_flow_section(day: date_type, indicator: str = "今日") -> dict:
    """③ 板块主力资金排名（行业口径；"今日"仅当日，5日/10日为滚动口径）"""
    if indicator not in INDICATORS:
        raise ValueError(f"indicator 仅支持 {'/'.join(INDICATORS)}，当前: {indicator}")
    if indicator == "今日" and day != datetime.now().date():
        return {
            "date": str(day),
            "items": None,
            "notes": ["板块资金流仅支持当日(今日口径)，历史日期已省略"],
        }
    df = stock_sector_fund_flow_rank(indicator=indicator, sector_type="行业资金流")
    col_main = col_like(df, "主力净流入-净额")
    col_chg = col_like(df, "涨跌幅")
    d = df.copy()
    d["_v"] = pd.to_numeric(d[col_main], errors="coerce")
    d = d.dropna(subset=["_v"]).sort_values("_v", ascending=False)

    def slim(r):
        return {
            "name": str(r["名称"]),
            "main_net_yi": safe_num(float(r[col_main]) / 1e8, 2),
            "chg_pct": safe_num(r[col_chg], 2) if col_chg else None,
        }

    return {
        "date": str(day),
        "indicator": indicator,
        "count": len(d),
        "top5": [slim(r) for _, r in d.head(5).iterrows()],
        "bottom5": [slim(r) for _, r in d.tail(5).iloc[::-1].iterrows()],
        "notes": [],
    }


def fund_flow_section(day: date_type) -> dict:
    """聚合用：大盘 + 板块（各自独立降级）"""
    out, notes = {}, []
    try:
        out["market"] = market_fund_flow_section(day)
        notes += [f"[market] {n}" for n in out["market"].pop("notes", [])]
    except Exception as e:  # noqa: BLE001
        out["market"] = None
        notes.append(f"[market] 失败: {e}")
    try:
        out["sector"] = sector_fund_flow_section(day, "今日")
        notes += [f"[sector] {n}" for n in out["sector"].pop("notes", [])]
    except Exception as e:  # noqa: BLE001
        out["sector"] = None
        notes.append(f"[sector] 失败: {e}")
    if out["market"] is None and out["sector"] is None:
        raise ValueError("大盘与板块资金流均失败")
    return {"date": str(day), **out, "notes": notes}


def margin_section(day: date_type, days: int = 10, market: str = "沪深") -> dict:
    """⑦ 两融余额（融资余额，T-1 披露；沪深合计，单位亿元）"""
    if market not in ("沪", "深", "沪深"):
        raise ValueError(f"market 仅支持 沪/深/沪深，当前: {market}")
    notes = []
    per_day: dict[str, dict[str, float]] = {}
    if market in ("沪深", "沪"):
        try:
            start = (day - timedelta(days=days * 2 + 6)).strftime("%Y%m%d")
            sse = stock_margin_sse(start_date=start, end_date=day.strftime("%Y%m%d"))
            c_d, c_r = col_like(sse, "日期"), col_like(sse, "融资余额")
            for _, r in sse.iterrows():
                try:
                    per_day.setdefault(norm_date(r[c_d]), {})["sse"] = float(r[c_r])
                except (TypeError, ValueError, KeyError):
                    continue
        except Exception as e:  # noqa: BLE001
            notes.append(f"沪市两融获取失败: {e}")
    if market in ("沪深", "深"):
        got = 0
        for d in [day, *prev_trading_days(day, days + 4)]:
            if got >= days:
                break
            try:
                sz = stock_margin_szse(date=d.strftime("%Y%m%d"))
            except Exception:  # noqa: BLE001 非交易日/未发布
                continue
            if sz is None or sz.empty:
                continue
            c_r = col_like(sz, "融资余额")
            c_d = col_like(sz, "日期")
            key = norm_date(sz.iloc[0][c_d]) if c_d else str(d)
            try:
                per_day.setdefault(key, {})["szse"] = float(sz.iloc[0][c_r])
                got += 1
            except (TypeError, ValueError, KeyError):
                continue
    if not per_day:
        raise ValueError(f"{day} 前后无两融数据(交易所T+1披露)")
    series = []
    for k in sorted(per_day):
        entry = per_day[k]
        parts = [v for v in (entry.get("sse"), entry.get("szse")) if v is not None]
        if not parts:
            continue
        series.append(
            {
                "date": k,
                "rzye_yi": round(sum(parts) / 1e8, 2),
                "sse_yi": round(entry["sse"] / 1e8, 2) if "sse" in entry else None,
                "szse_yi": round(entry["szse"] / 1e8, 2) if "szse" in entry else None,
            }
        )
    series = series[-days:]
    latest = series[-1]
    prev = series[-2] if len(series) > 1 else None
    return {
        "date": latest["date"],
        "rzye_yi": latest["rzye_yi"],
        "rzye_chg_yi": round(latest["rzye_yi"] - prev["rzye_yi"], 2) if prev else None,
        "series": series,
        "notes": notes + ["两融为T-1披露口径(交易所隔日发布)"],
    }


@RateLimiter(max_calls=10, time_window=60)
@with_retry(max_retries=3, delay=1.0, backoff=2.0)
def get_fund_flow(scope: str = "全部", indicator: str = "今日", date: str = "") -> str:
    """查询主力资金：大盘（沪深两市净流入）与/或行业板块排名

    Args:
        scope: "全部"(大盘+板块) / "大盘" / "板块"，默认"全部"
        indicator: "今日" / "5日" / "10日"（仅板块口径使用），默认"今日"
        date: 查询日期 YYYY-MM-DD，默认今天（板块"今日"口径仅支持当日）
    """
    try:
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        if day > datetime.now().date():
            return json_err(f"查询日期 {day} 晚于今天，无法查询未来数据")
        s = (scope or "").strip()
        if s not in ("全部", "大盘", "板块"):
            return json_err(f"scope 仅支持 全部/大盘/板块，当前: {scope}")
        if s == "大盘":
            return json_ok(market_fund_flow_section(day))
        if s == "板块":
            return json_ok(sector_fund_flow_section(day, indicator))
        return json_ok(fund_flow_section(day))
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_fund_flow: %s", e)
        return json_err(f"查询主力资金失败: {e}")


@RateLimiter(max_calls=10, time_window=60)
@with_retry(max_retries=3, delay=1.0, backoff=2.0)
def get_margin(market: str = "沪深", days: int = 10, date: str = "") -> str:
    """查询两融（融资余额，T-1 披露）

    Args:
        market: "沪深"(合计) / "沪" / "深"，默认"沪深"
        days: 返回最近 N 个交易日的余额序列（1-30，默认10）
        date: 截止日期 YYYY-MM-DD，默认今天（实际取该日前最近披露日）
    """
    try:
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        if day > datetime.now().date():
            return json_err(f"查询日期 {day} 晚于今天，无法查询未来数据")
        if not 1 <= int(days) <= 30:
            return json_err(f"days 需在 1-30 之间，当前: {days}")
        return json_ok(margin_section(day, days=int(days), market=market))
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_margin: %s", e)
        return json_err(f"查询两融失败: {e}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_review_funds.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add utils/review_funds.py tests/test_review_funds.py
git commit -m "feat: add fund flow and margin sections with tool functions"
```

---

### Task 5: review_derivatives.py — 期指基差与期权 PCR（含 1 个工具函数）

**Files:**
- Create: `utils/review_derivatives.py`
- Test: `tests/test_review_derivatives.py`

**Interfaces:**
- Consumes: `review_common`、`tools.CachedData/RateLimiter/with_retry`
- Produces: `basis_section(day) -> dict`、`pcr_section(day) -> dict`（Task 6 的 `derivatives_section` 与 Task 7 消费）；`_cffex_daily_with_fallback`（Task 6 复用）；`get_index_derivatives`（Task 8 注册）。

- [ ] **Step 1: 写失败测试**

`tests/test_review_derivatives.py`：

```python
from datetime import date

import pandas as pd
import pytest

from utils import review_derivatives as rd


def _cffex_df(day="20260902"):
    # IF2609 成交量大于 IF2612 -> 主力为 IF2609
    return pd.DataFrame(
        {
            "symbol": ["IF2609", "IF2612"],
            "date": [day, day],
            "open": [4566.2, 4550.0],
            "high": [4567.0, 4560.0],
            "low": [4497.6, 4510.0],
            "close": [4532.2, 4520.0],
            "volume": [71487, 20000],
            "open_interest": [139367, 50000],
            "turnover": [3.2e11, 9.0e10],
            "settle": [4530.0, 4515.0],
            "pre_settle": [4590.0, 4555.0],
            "variety": ["IF", "IF"],
        }
    )


def _index_df(day="2026-09-02"):
    return pd.DataFrame(
        {"date": [day], "open": [4618.7], "high": [4640.1],
         "low": [4604.8], "close": [4611.4], "volume": [2.1e10]}
    )


def test_contract_expiry():
    assert rd._contract_expiry("IF2609") == date(2026, 9, 18)  # 9月第三个周五
    assert rd._contract_expiry("IM2612") == date(2026, 12, 18)


def test_basis_section(monkeypatch):
    monkeypatch.setattr(rd, "get_cffex_daily", lambda date: _cffex_df())
    monkeypatch.setattr(rd, "stock_zh_index_daily", lambda symbol: _index_df())
    out = rd.basis_section(date(2026, 9, 2))
    assert len(out["items"]) == 1  # 只有 IF 一个品种
    item = out["items"][0]
    assert item["contract"] == "IF2609"
    assert item["settle"] == 4530.0 and item["spot_close"] == 4611.4
    assert item["basis"] == round(4611.4 - 4530.0, 2)  # 现货-期货
    # 年化: basis/spot*365/16天*100（IF2609 到期 2026-09-18，距 09-02 为 16 天）
    assert item["basis_ann_pct"] == round(
        (4611.4 - 4530.0) / 4611.4 * 365 / 16 * 100, 2
    )


def test_pcr_section(monkeypatch):
    sse = pd.DataFrame(
        {
            "合约标的代码": ["510050", "510300"],
            "合约标的名称": ["上证50ETF华夏", "沪深300ETF华泰柏瑞"],
            "合约数量": [92, 96],
            "总成交额": [25156, 43614],
            "总成交量": [627474, 651847],
            "认购成交量": [335528, 348787],
            "认沽成交量": [291946, 303060],
            "认沽/认购": [87.01, 86.89],
            "未平仓合约总数": [1182953, 1123354],
            "未平仓认购合约数": [691541, 628365],
            "未平仓认沽合约数": [491412, 494989],
            "交易日": ["2026-09-02", "2026-09-02"],
        }
    )
    monkeypatch.setattr(rd, "option_daily_stats_sse", lambda date: sse)
    monkeypatch.setattr(rd, "option_daily_stats_szse", lambda date: pd.DataFrame())
    out = rd.pcr_section(date(2026, 9, 2))
    assert len(out["exchanges"]) == 1
    ex = out["exchanges"][0]
    assert ex["exchange"] == "SSE"
    assert ex["vol_pcr"] == round((291946 + 303060) / (335528 + 348787) * 100, 2)
    assert ex["oi_pcr"] == round((491412 + 494989) / (691541 + 628365) * 100, 2)
    assert len(ex["underlyings"]) == 2


def test_pcr_section_no_data(monkeypatch):
    monkeypatch.setattr(rd, "option_daily_stats_sse", lambda date: pd.DataFrame())
    monkeypatch.setattr(rd, "option_daily_stats_szse", lambda date: pd.DataFrame())
    with pytest.raises(ValueError):
        rd.pcr_section(date(2026, 9, 2))


def test_get_index_derivatives(monkeypatch):
    monkeypatch.setattr(rd, "get_cffex_daily", lambda date: _cffex_df())
    monkeypatch.setattr(rd, "stock_zh_index_daily", lambda symbol: _index_df())
    sse = pd.DataFrame(
        {
            "合约标的代码": ["510050"],
            "合约标的名称": ["上证50ETF华夏"],
            "合约数量": [92],
            "总成交额": [25156],
            "总成交量": [627474],
            "认购成交量": [335528],
            "认沽成交量": [291946],
            "认沽/认购": [87.01],
            "未平仓合约总数": [1182953],
            "未平仓认购合约数": [691541],
            "未平仓认沽合约数": [491412],
            "交易日": ["2026-09-02"],
        }
    )
    monkeypatch.setattr(rd, "option_daily_stats_sse", lambda date: sse)
    monkeypatch.setattr(rd, "option_daily_stats_szse", lambda date: pd.DataFrame())
    out = rd.get_index_derivatives(date="2026-09-02")
    assert '"success": true' in out and '"basis"' in out and '"pcr"' in out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_review_derivatives.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现 review_derivatives.py（基差 + PCR 部分）**

`utils/review_derivatives.py`：

```python
#!/usr/bin/env python3
"""
复盘·衍生品模块：股指期货基差、中金所席位持仓、期权 PCR。

数据源（全部直连，不走 akshare-proxy）：
- get_cffex_daily             中金所官方日行情（全部品种合约，含结算价/持仓量）
- stock_zh_index_daily        新浪现货指数日行情（基差的现货腿）
- get_cffex_rank_table        中金所股指期货前20会员成交持仓排名
- 中金所股指期权席位 CSV       http://www.cffex.com.cn/sj/ccpm/YYYYMM/DD/{IO|MO|HO}_1.csv
- option_daily_stats_sse/szse 沪深交易所期权日统计（认购/认沽量与持仓）

口径说明：
- 基差 = 现货指数收盘价 - 期货主力合约(成交量最大)结算价；负值为贴水。
- 年化基差按合约到期日（当月第三个周五）折算。
- PCR = 认沽/认购 × 100（%），成交量口径与持仓量口径分别给出，分交易所不混算。
- 席位数据为经纪口径（"(代客)"后缀），仅前20会员可见，缺失方向按0计。
"""

import io
import logging
import re
from datetime import date as date_type, timedelta

import pandas as pd
import requests

from akshare import (
    get_cffex_daily,
    get_cffex_rank_table,
    option_daily_stats_sse,
    option_daily_stats_szse,
    stock_zh_index_daily,
)

from .review_common import (
    json_err,
    json_ok,
    parse_day,
    prev_trading_days,
    safe_num,
)
from .tools import CachedData, RateLimiter, with_retry

logger = logging.getLogger(__name__)

INDEX_FUTURES = {
    "IF": ("sh000300", "沪深300"),
    "IH": ("sh000016", "上证50"),
    "IC": ("sh000905", "中证500"),
    "IM": ("sh000852", "中证1000"),
}
OPTION_VARS = {"IO": "沪深300期权", "MO": "中证1000期权", "HO": "上证50期权"}
RANK_VARS = set(INDEX_FUTURES) | set(OPTION_VARS)

RANK_URL = "http://www.cffex.com.cn/sj/ccpm/{ym}/{d}/{var}_1.csv"

_index_daily_cache: dict[str, CachedData] = {}


def _spot_close(sina_symbol: str, day: date_type) -> float | None:
    """现货指数收盘价（新浪日K，缓存300秒）"""
    c = _index_daily_cache.get(sina_symbol)
    if c is None or c.is_expired():
        c = CachedData(stock_zh_index_daily(symbol=sina_symbol), ttl=300)
        _index_daily_cache[sina_symbol] = c
    df = c.data
    hit = df[df["date"].astype(str) == str(day)]
    return float(hit.iloc[0]["close"]) if not hit.empty else None


def _contract_expiry(symbol: str) -> date_type | None:
    """合约到期日 = 合约月份的第三个周五（IF2609 -> 2026-09-18）"""
    m = re.match(r"^[A-Z]{1,2}(\d{2})(\d{2})$", str(symbol))
    if not m:
        return None
    year, month = 2000 + int(m.group(1)), int(m.group(2))
    first = date_type(year, month, 1)
    fridays = [
        first + timedelta(days=i)
        for i in range(31)
        if (first + timedelta(days=i)).weekday() == 4
    ]
    return fridays[2] if len(fridays) >= 3 else None


def _cffex_daily_with_fallback(day: date_type) -> tuple[pd.DataFrame, date_type]:
    """中金所日行情（非交易日/未发布时回退最近交易日）"""
    for d in [day, *prev_trading_days(day, 6)]:
        try:
            df = get_cffex_daily(d.strftime("%Y%m%d"))
        except Exception as e:  # noqa: BLE001
            logger.warning("get_cffex_daily(%s) failed: %s", d, e)
            continue
        if df is not None and not df.empty:
            return df, d
    raise ValueError(f"{day} 前后数个交易日均无中金所日行情")


def basis_section(day: date_type) -> dict:
    """⑤ 期指基差：IF/IH/IC/IM 主力合约结算价 vs 现货收盘"""
    df, actual_day = _cffex_daily_with_fallback(day)
    notes = []
    if actual_day != day:
        notes.append(f"{day} 无中金所日行情，使用最近交易日 {actual_day}")
    df = df[df["variety"].isin(INDEX_FUTURES)]
    items = []
    for var, (sina_sym, cname) in INDEX_FUTURES.items():
        sub = df[df["variety"] == var]
        if sub.empty:
            notes.append(f"{var} 当日无合约数据")
            continue
        main = sub.loc[pd.to_numeric(sub["volume"]).idxmax()]
        sym = str(main["symbol"])
        settle = float(main["settle"])
        spot = _spot_close(sina_sym, actual_day)
        if spot is None:
            notes.append(f"{cname} 现货收盘缺失，{var} 基差无法计算")
            continue
        basis = spot - settle
        expiry = _contract_expiry(sym)
        ann = None
        if expiry and expiry > actual_day:
            ann = round(basis / spot * 365 / (expiry - actual_day).days * 100, 2)
        items.append(
            {
                "variety": var,
                "index": cname,
                "contract": sym,
                "settle": round(settle, 1),
                "spot_close": round(spot, 2),
                "basis": round(basis, 2),
                "basis_pct": round(basis / spot * 100, 3),
                "basis_ann_pct": ann,
                "volume": int(main["volume"]),
                "open_interest": int(main["open_interest"]),
            }
        )
    if not items:
        raise ValueError("四大期指均无基差数据")
    return {"date": str(actual_day), "items": items, "notes": notes}


def pcr_section(day: date_type) -> dict:
    """⑥ 期权PCR：分交易所给出认购/认沽的量比与持仓比（×100，%）"""
    compact = day.strftime("%Y%m%d")
    notes = []
    sse = szse = None
    try:
        sse = option_daily_stats_sse(date=compact)
    except Exception as e:  # noqa: BLE001
        notes.append(f"上交所期权统计获取失败: {e}")
    try:
        szse = option_daily_stats_szse(date=compact)
    except Exception as e:  # noqa: BLE001
        notes.append(f"深交所期权统计获取失败: {e}")
    if (sse is None or sse.empty) and (szse is None or szse.empty):
        raise ValueError(f"{day} 无交易所期权统计(可能非交易日或未发布)")
    exchanges = []
    if sse is not None and not sse.empty:
        under = []
        for _, r in sse.iterrows():
            c, p = float(r["认购成交量"]), float(r["认沽成交量"])
            oc = float(r["未平仓认购合约数"])
            op = float(r["未平仓认沽合约数"])
            under.append(
                {
                    "underlying": f"{r['合约标的代码']} {r['合约标的名称']}",
                    "vol": int(r["总成交量"]),
                    "vol_pcr": round(p / c * 100, 2) if c else None,
                    "oi_pcr": round(op / oc * 100, 2) if oc else None,
                }
            )
        tc = sse["认购成交量"].astype(float).sum()
        tp = sse["认沽成交量"].astype(float).sum()
        oc = sse["未平仓认购合约数"].astype(float).sum()
        op = sse["未平仓认沽合约数"].astype(float).sum()
        exchanges.append(
            {
                "exchange": "SSE",
                "vol_pcr": round(tp / tc * 100, 2) if tc else None,
                "oi_pcr": round(op / oc * 100, 2) if oc else None,
                "underlyings": under,
            }
        )
    if szse is not None and not szse.empty:
        under = []
        for _, r in szse.iterrows():
            c, p = float(r["认购成交量"]), float(r["认沽成交量"])
            oc = float(r["未平仓认购合约数"])
            op = float(r["未平仓认沽合约数"])
            under.append(
                {
                    "underlying": f"{r['合约标的代码']} {r['合约标的名称']}",
                    "vol": int(r["成交量"]),
                    "vol_pcr": round(p / c * 100, 2) if c else None,
                    "oi_pcr": round(op / oc * 100, 2) if oc else None,
                }
            )
        tc = szse["认购成交量"].astype(float).sum()
        tp = szse["认沽成交量"].astype(float).sum()
        oc = szse["未平仓认购合约数"].astype(float).sum()
        op = szse["未平仓认沽合约数"].astype(float).sum()
        exchanges.append(
            {
                "exchange": "SZSE",
                "vol_pcr": round(tp / tc * 100, 2) if tc else None,
                "oi_pcr": round(op / oc * 100, 2) if oc else None,
                "underlyings": under,
            }
        )
    return {"date": str(day), "exchanges": exchanges, "notes": notes}


@RateLimiter(max_calls=10, time_window=60)
@with_retry(max_retries=3, delay=1.0, backoff=2.0)
def get_index_derivatives(date: str = "") -> str:
    """查询股指衍生品指标：四大期指基差（含年化）+ 期权PCR（分交易所）

    Args:
        date: 查询日期 YYYY-MM-DD，默认今天（席位排名见 get_cffex_rank）
    """
    try:
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        if day > date_type.today():
            return json_err(f"查询日期 {day} 晚于今天，无法查询未来数据")
        basis = pcr = None
        notes = []
        try:
            basis = basis_section(day)
            notes += [f"[basis] {n}" for n in basis.pop("notes", [])]
        except Exception as e:  # noqa: BLE001
            notes.append(f"[basis] 失败: {e}")
        try:
            pcr = pcr_section(day)
            notes += [f"[pcr] {n}" for n in pcr.pop("notes", [])]
        except Exception as e:  # noqa: BLE001
            notes.append(f"[pcr] 失败: {e}")
        if basis is None and pcr is None:
            return json_err(f"{day} 基差与PCR均无数据(可能非交易日)")
        return json_ok({"date": str(day), "basis": basis, "pcr": pcr, "notes": notes})
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_index_derivatives: %s", e)
        return json_err(f"查询衍生品指标失败: {e}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_review_derivatives.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add utils/review_derivatives.py tests/test_review_derivatives.py
git commit -m "feat: add futures basis and option PCR sections with tool function"
```

---

### Task 6: review_derivatives.py — 中金所席位持仓（含 1 个工具函数）

**Files:**
- Modify: `utils/review_derivatives.py`（追加）
- Test: `tests/test_review_derivatives.py`（追加）

**Interfaces:**
- Consumes: Task 5 的 `INDEX_FUTURES/OPTION_VARS/RANK_VARS`；`review_common`。
- Produces: `fetch_option_rank_csv(var, day)`、`_summarize_members(df)`、`_slim_rank_rows(df, member)`、`derivatives_section(day) -> dict`（Task 7 消费）、`get_cffex_rank`（Task 8 注册）。

- [ ] **Step 1: 追加失败测试**

`tests/test_review_derivatives.py` 追加：

```python
def _rank_df():
    return pd.DataFrame(
        {
            "rank": [1, 2],
            "vol_party_name": ["中信期货(代客)", "国泰君安(代客)"],
            "vol": [26865, 23501],
            "vol_chg": [2688, 5715],
            "long_party_name": ["国泰君安(代客)", "中信期货(代客)"],
            "long_open_interest": [22066, 18937],
            "long_open_interest_chg": [-789, 1432],
            "short_party_name": ["中信期货(代客)", "华泰期货(代客)"],
            "short_open_interest": [24419, 16152],
            "short_open_interest_chg": [905, -574],
            "symbol": ["IF2609", "IF2609"],
            "variety": ["IF", "IF"],
        }
    )


def test_summarize_members():
    rows = rd._summarize_members(_rank_df())
    by_name = {r["member"]: r for r in rows}
    citic = by_name["中信期货(代客)"]
    # 中信: 多18937(+1432) 空24419(+905) -> net -5482, net_chg +527
    assert citic["net"] == 18937 - 24419
    assert citic["net_chg"] == 1432 - 905
    gtja = by_name["国泰君安(代客)"]
    assert gtja["net"] == 22066 and gtja["net_chg"] == -789
    # 排序不变量：按 net_chg 降序
    chgs = [r["net_chg"] for r in rows]
    assert chgs == sorted(chgs, reverse=True)


class _FakeResp:
    def __init__(self, content, status=200):
        self.content = content
        self.status_code = status


_CSV_LINES = [
    "交易日,合约系列,排名,成交量排名,,,持买单量排名,,,持卖单量排名,,",
    ",,,会员简称,成交量,比上一交易日增减,会员简称,持买单量,比上一交易日增减,会员简称,持卖单量,比上一交易日增减",
    "20260902,IO2609,1,中信期货(代客),26143,4357,中信期货(代客),14934,1298,中信期货(代客),15053,1733",
    "20260902,IO2609,2,华泰期货(代客),21702,5211,国泰君安(代客),12084,272,国泰君安(代客),12452,131",
]


def test_fetch_option_rank_csv(monkeypatch):
    monkeypatch.setattr(
        rd.requests,
        "get",
        lambda url, timeout: _FakeResp("\n".join(_CSV_LINES).encode("gbk")),
    )
    df = rd.fetch_option_rank_csv("IO", date(2026, 9, 2))
    assert df is not None and len(df) == 2
    assert df.iloc[0]["vol_party_name"] == "中信期货(代客)"
    assert int(df.iloc[0]["long_open_interest"]) == 14934
    assert int(df.iloc[0]["short_open_interest"]) == 15053


def test_fetch_option_rank_csv_404(monkeypatch):
    monkeypatch.setattr(
        rd.requests, "get", lambda url, timeout: _FakeResp(b"", status=404)
    )
    assert rd.fetch_option_rank_csv("IO", date(2026, 9, 2)) is None


def test_get_cffex_rank_futures(monkeypatch):
    monkeypatch.setattr(
        rd,
        "get_cffex_rank_table",
        lambda date, vars_list: {"IF2609": _rank_df()},
    )
    out = rd.get_cffex_rank(var="IF", date="2026-09-02", member="中信")
    assert '"success": true' in out and "IF2609" in out
    data = __import__("json").loads(out)["data"]
    assert any("中信" in s["member"] for s in data["member_summary"])


def test_get_cffex_rank_option(monkeypatch):
    monkeypatch.setattr(
        rd.requests,
        "get",
        lambda url, timeout: _FakeResp("\n".join(_CSV_LINES).encode("gbk")),
    )
    out = rd.get_cffex_rank(var="IO", date="2026-09-02", member="")
    assert '"success": true' in out
    data = __import__("json").loads(out)["data"]
    assert "IO2609" in data["contracts"]


def test_get_cffex_rank_bad_var():
    out = rd.get_cffex_rank(var="XX", date="2026-09-02")
    assert '"success": false' in out


def test_derivatives_section(monkeypatch):
    monkeypatch.setattr(rd, "get_cffex_daily", lambda date: _cffex_df())
    monkeypatch.setattr(rd, "stock_zh_index_daily", lambda symbol: _index_df())
    monkeypatch.setattr(rd, "option_daily_stats_sse", lambda date: pd.DataFrame())
    monkeypatch.setattr(rd, "option_daily_stats_szse", lambda date: pd.DataFrame())
    monkeypatch.setattr(
        rd, "get_cffex_rank_table", lambda date, vars_list: {"IF2609": _rank_df()}
    )
    monkeypatch.setattr(
        rd.requests, "get", lambda url, timeout: _FakeResp(b"", status=404)
    )
    out = rd.derivatives_section(date(2026, 9, 2))
    assert out["basis"] is not None and out["seats"]["IF"] is not None
    assert out["seats"]["IO"] is None  # CSV 404 -> 降级
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_review_derivatives.py -v`
Expected: 新增用例 FAIL（无 `fetch_option_rank_csv` 等属性）。

- [ ] **Step 3: 追加实现**

`utils/review_derivatives.py` 追加：

```python
_RANK_NAMES = [
    "交易日", "合约系列", "rank",
    "vol_party_name", "vol", "vol_chg",
    "long_party_name", "long_open_interest", "long_open_interest_chg",
    "short_party_name", "short_open_interest", "short_open_interest_chg",
]


def fetch_option_rank_csv(var: str, day: date_type) -> pd.DataFrame | None:
    """中金所股指期权成交持仓排名 CSV（官网直连, GBK; 无数据返回 None）"""
    url = RANK_URL.format(ym=day.strftime("%Y%m"), d=day.strftime("%d"), var=var)
    try:
        resp = requests.get(url, timeout=10)
    except Exception as e:  # noqa: BLE001
        logger.warning("cffex option rank %s %s failed: %s", var, day, e)
        return None
    if resp.status_code != 200 or len(resp.content) < 60:
        return None
    try:
        return pd.read_csv(
            io.BytesIO(resp.content),
            encoding="gbk",
            header=None,
            skiprows=2,
            names=_RANK_NAMES,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("parse cffex option rank csv failed: %s", e)
        return None


def option_rank_with_fallback(
    var: str, day: date_type
) -> tuple[pd.DataFrame | None, date_type]:
    for d in [day, *prev_trading_days(day, 6)]:
        df = fetch_option_rank_csv(var, d)
        if df is not None and not df.empty:
            return df, d
    return None, day


def _summarize_members(rank_df: pd.DataFrame) -> list[dict]:
    """按会员聚合净持仓与净变化（跨合约求和；仅前20可见, 缺失方向按0计）"""
    agg: dict[str, dict[str, float]] = {}
    for _, r in rank_df.iterrows():
        for side, party_col, oi_col, chg_col in (
            ("long", "long_party_name", "long_open_interest", "long_open_interest_chg"),
            ("short", "short_party_name", "short_open_interest", "short_open_interest_chg"),
        ):
            name = str(r.get(party_col, "")).strip()
            if not name or name == "nan":
                continue
            oi = pd.to_numeric(r.get(oi_col), errors="coerce")
            chg = pd.to_numeric(r.get(chg_col), errors="coerce")
            slot = agg.setdefault(
                name, {"long": 0.0, "short": 0.0, "long_chg": 0.0, "short_chg": 0.0}
            )
            if pd.notna(oi):
                slot[side] += float(oi)
            if pd.notna(chg):
                slot[f"{side}_chg"] += float(chg)
    rows = [
        {
            "member": name,
            "net": int(s["long"] - s["short"]),
            "net_chg": int(s["long_chg"] - s["short_chg"]),
            "long_oi": int(s["long"]),
            "short_oi": int(s["short"]),
        }
        for name, s in agg.items()
    ]
    rows.sort(key=lambda x: x["net_chg"], reverse=True)
    return rows


def _slim_rank_rows(df: pd.DataFrame, member: str = "") -> list[dict]:
    """前20排名行瘦身（可按会员子串过滤）"""
    rows = []
    for _, r in df.head(20).iterrows():
        if member and not any(
            member in str(r.get(c, ""))
            for c in ("vol_party_name", "long_party_name", "short_party_name")
        ):
            continue
        rank = pd.to_numeric(r.get("rank"), errors="coerce")
        rows.append(
            {
                "rank": int(rank) if pd.notna(rank) else None,
                "vol_party": str(r.get("vol_party_name", "")),
                "vol": safe_num(r.get("vol"), 0),
                "vol_chg": safe_num(r.get("vol_chg"), 0),
                "long_party": str(r.get("long_party_name", "")),
                "long_oi": safe_num(r.get("long_open_interest"), 0),
                "long_chg": safe_num(r.get("long_open_interest_chg"), 0),
                "short_party": str(r.get("short_party_name", "")),
                "short_oi": safe_num(r.get("short_open_interest"), 0),
                "short_chg": safe_num(r.get("short_open_interest_chg"), 0),
            }
        )
    return rows


@RateLimiter(max_calls=10, time_window=60)
def get_cffex_rank(var: str = "IF", date: str = "", member: str = "") -> str:
    """查询中金所前20会员成交持仓排名（经纪席位口径, "(代客)"后缀）

    Args:
        var: IF/IH/IC/IM(股指期货) 或 IO/MO/HO(股指期权)，默认 IF
        date: 查询日期 YYYY-MM-DD，默认今天；非交易日自动回退最近交易日
        member: 会员名子串过滤（如 "中信"），默认不过滤
    """
    try:
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        v = (var or "").strip().upper()
        if v not in RANK_VARS:
            return json_err(f"var 仅支持 {'/'.join(sorted(RANK_VARS))}，当前: {var}")
        notes = []
        if v in INDEX_FUTURES:
            df_map: dict = {}
            actual_day = day
            for d in [day, *prev_trading_days(day, 6)]:
                try:
                    df_map = get_cffex_rank_table(d.strftime("%Y%m%d"), vars_list=[v])
                except Exception as e:  # noqa: BLE001
                    logger.warning("get_cffex_rank_table(%s) failed: %s", d, e)
                    continue
                if isinstance(df_map, dict) and df_map:
                    actual_day = d
                    break
            if not df_map:
                return json_err(f"{day} 前后数个交易日无 {v} 席位数据")
            if actual_day != day:
                notes.append(f"{day} 无数据，返回最近交易日 {actual_day}")
            contracts = {
                sym: _slim_rank_rows(df, member) for sym, df in df_map.items()
            }
            summary = _summarize_members(
                pd.concat(list(df_map.values()), ignore_index=True)
            )
        else:
            df, actual_day = option_rank_with_fallback(v, day)
            if df is None or df.empty:
                return json_err(f"{day} 前后数个交易日无 {v} 期权席位数据")
            if actual_day != day:
                notes.append(f"{day} 无数据，返回最近交易日 {actual_day}")
            contracts = {
                str(sym): _slim_rank_rows(sub, member)
                for sym, sub in df.groupby("合约系列")
            }
            summary = _summarize_members(df)
        if member:
            summary = [s for s in summary if member in s["member"]]
        return json_ok(
            {
                "date": str(actual_day),
                "variety": v,
                "contracts": contracts,
                "member_summary": summary[:20],
                "notes": notes
                + ["席位为经纪口径(代客)，仅前20会员可见，缺失方向按0计"],
            }
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_cffex_rank: %s", e)
        return json_err(f"查询席位排名失败: {e}")


def derivatives_section(day: date_type) -> dict:
    """⑤⑥聚合：基差 + PCR + 席位摘要(IF/IO 净持仓变化Top3)"""
    out, notes = {}, []
    try:
        out["basis"] = basis_section(day)
        notes += [f"[basis] {n}" for n in out["basis"].pop("notes", [])]
    except Exception as e:  # noqa: BLE001
        out["basis"] = None
        notes.append(f"[basis] 失败: {e}")
    try:
        out["pcr"] = pcr_section(day)
        notes += [f"[pcr] {n}" for n in out["pcr"].pop("notes", [])]
    except Exception as e:  # noqa: BLE001
        out["pcr"] = None
        notes.append(f"[pcr] 失败: {e}")
    seats: dict[str, dict | None] = {}
    for v in ("IF", "IO"):
        try:
            if v == "IF":
                df_map = get_cffex_rank_table(day.strftime("%Y%m%d"), vars_list=["IF"])
                df = (
                    pd.concat(list(df_map.values()), ignore_index=True)
                    if df_map
                    else None
                )
                actual = day
            else:
                df, actual = option_rank_with_fallback("IO", day)
            if df is None or df.empty:
                raise ValueError("无席位数据")
            s = _summarize_members(df)
            seats[v] = {
                "date": str(actual),
                "top_net_long_chg": s[:3],
                "top_net_short_chg": list(reversed(s[-3:])),
            }
        except Exception as e:  # noqa: BLE001
            seats[v] = None
            notes.append(f"[seats:{v}] 失败: {e}")
    out["seats"] = seats
    if out["basis"] is None and out["pcr"] is None and not any(seats.values()):
        raise ValueError("基差/PCR/席位均失败")
    return {"date": str(day), **out, "notes": notes}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_review_derivatives.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add utils/review_derivatives.py tests/test_review_derivatives.py
git commit -m "feat: add CFFEX member rank tool with option CSV fetcher"
```

---

### Task 7: review.py 聚合器 + utils 导出

**Files:**
- Create: `utils/review.py`
- Modify: `utils/__init__.py`
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes: `indices_section/breadth_section`（Task 2/3）、`fund_flow_section/margin_section`（Task 4）、`derivatives_section`（Task 6）、`etf.get_etf_daily`（已有）。
- Produces: `get_daily_review(date: str = "") -> str`（Task 8 注册）；`utils.__init__` 导出全部 7 个工具函数。

- [ ] **Step 1: 写失败测试**

`tests/test_review.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_review.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现 review.py**

`utils/review.py`：

```python
#!/usr/bin/env python3
"""
一键复盘聚合器：并行拉取七大模块并合并为一份 JSON。

模块：indices(指数) / breadth(涨跌结构) / fund_flow(主力资金) /
national_team(国家队ETF, 复用 etf.get_etf_daily) / derivatives(基差+PCR+席位) /
margin(两融)。

- ThreadPoolExecutor 并行，总耗时 ≈ 最慢单模块；
- 模块级降级：单模块失败进 errors，notes 弹出加 [模块名] 前缀；
- 聚合结果按日期缓存 600 秒（收盘后数据不变，防重复消耗东财积分）。
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import logging

from .etf import get_etf_daily
from .review_common import json_err, json_ok, parse_day
from .review_derivatives import derivatives_section
from .review_funds import fund_flow_section, margin_section
from .review_market import breadth_section, indices_section
from .tools import CachedData, RateLimiter

logger = logging.getLogger(__name__)

_review_cache: dict[str, CachedData] = {}


def _national_team_section(day) -> dict:
    """④ 国家队ETF：复用 etf.get_etf_daily（JSON 解析，不改动该模块）"""
    raw = json.loads(get_etf_daily("", str(day)))
    if not raw.get("success"):
        raise ValueError(str(raw.get("error", "ETF数据获取失败"))[:120])
    data = raw["data"]
    merged = data.get("merged", {})
    return {
        "date": data.get("date"),
        "total_est_net_subscription_yi": merged.get("est_net_subscription_yi"),
        "total_share_change_yi": merged.get("total_share_change_yi"),
        "total_main_inflow_yi": merged.get("total_main_inflow_yi"),
        "avg_change_pct": merged.get("avg_change_pct"),
        "top_net_subscription": merged.get("top_net_subscription"),
        "top_net_redemption": merged.get("top_net_redemption"),
    }


@RateLimiter(max_calls=6, time_window=60)
def get_daily_review(date: str = "") -> str:
    """一键复盘：当日 A 股全貌（七模块并行聚合）

    Args:
        date: 查询日期 YYYY-MM-DD，默认今天。历史日期时部分模块自动降级
              （板块资金流、涨跌家数仅当日），详见返回中的 notes。
    """
    try:
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        if day > datetime.now().date():
            return json_err(f"查询日期 {day} 晚于今天，无法查询未来数据")
        key = str(day)
        cached = _review_cache.get(key)
        if cached is not None and not cached.is_expired():
            return cached.data

        def run(name, fn):
            try:
                data = fn(day)
                mod_notes = data.pop("notes", [])
                return name, data, [f"[{name}] {n}" for n in mod_notes], None
            except Exception as e:  # noqa: BLE001
                logger.warning("section %s failed: %s", name, e)
                return name, None, [], str(e)

        # 注意：jobs 必须在函数体内引用模块全局名（indices_section 等），
        # 不能用模块级常量持有函数引用，否则测试 monkeypatch 不生效。
        jobs = [
            ("indices", indices_section),
            ("breadth", breadth_section),
            ("fund_flow", fund_flow_section),
            ("derivatives", derivatives_section),
            ("margin", margin_section),
            ("national_team", _national_team_section),
        ]
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda nf: run(nf[0], nf[1]), jobs))
        data = {
            "date": key,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        notes, errors = [], {}
        for name, section, mod_notes, err in results:
            data[name] = section
            notes.extend(mod_notes)
            if err:
                errors[name] = err
        if all(data.get(name) is None for name, _ in jobs):
            return json_err(f"{day} 全部模块均无数据(可能非交易日)")
        if notes:
            data["notes"] = notes
        if errors:
            data["errors"] = errors
        out = json_ok(data)
        _review_cache[key] = CachedData(out, ttl=600)
        return out
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_daily_review: %s", e)
        return json_err(f"一键复盘失败: {e}")
```

- [ ] **Step 4: 更新 utils/__init__.py 导出**

`utils/__init__.py` 在现有内容基础上追加导入（保留原有 etf/ths/tools/my_module 导入与 `__all__`）：

```python
from .review import get_daily_review
from .review_derivatives import get_cffex_rank, get_index_derivatives
from .review_funds import get_fund_flow, get_margin
from .review_market import get_market_breadth, get_zt_pool
```

`__all__` 列表追加：

```python
    "get_daily_review",
    "get_market_breadth",
    "get_zt_pool",
    "get_fund_flow",
    "get_margin",
    "get_cffex_rank",
    "get_index_derivatives",
```

- [ ] **Step 5: 跑全量测试确认通过**

Run: `./.venv/Scripts/python.exe -m pytest -v`
Expected: 全部 PASS（含前序任务用例）。

- [ ] **Step 6: Commit**

```bash
git add utils/review.py utils/__init__.py tests/test_review.py
git commit -m "feat: add daily review aggregator with parallel sections and caching"
```

---

### Task 8: server.py 注册 7 个工具 + README + 冒烟

**Files:**
- Modify: `server.py`
- Modify: `README.md`
- Create: `tests/test_live_smoke.py`

**Interfaces:**
- Consumes: Task 7 的 `utils` 导出（7 个工具函数，均无 `_tool` 后缀）。
- Produces: MCP 工具注册（最终交付）：`get_daily_review_tool`、`get_market_breadth_tool`、`get_zt_pool_tool`、`get_fund_flow_tool`、`get_margin_tool`、`get_cffex_rank_tool`、`get_index_derivatives_tool`。

- [ ] **Step 1: server.py 扩展导入**

`server.py` 的 `from utils import (...)` 块改为（原有项保留，新项按字母序插入）：

```python
from utils import (
    StockCal,
    calculate_support_resistance_func,
    get_cffex_rank,
    get_daily_review,
    get_etf_daily,
    get_fund_flow,
    get_index_derivatives,
    get_margin,
    get_market_breadth,
    get_market_index,
    get_stock_basic,
    get_stock_history,
    get_stock_realtime,
    get_stock_symbol_by_name,
    get_ths_hot_list,
    get_zt_pool,
)
```

- [ ] **Step 2: 追加 7 个工具注册（放在 `get_etf_daily_tool` 定义之后）**

```python
@mcp.tool()
def get_daily_review_tool(date: str = "") -> str:
    """一键复盘：当日 A 股全貌（七模块并行聚合）

    返回 indices(五大指数) / breadth(涨跌家数+涨停跌停炸板+连板梯队) /
    fund_flow(大盘+板块主力资金) / national_team(国家队ETF份额与净申购) /
    derivatives(期指基差+期权PCR+IF/IO席位摘要) / margin(两融余额T-1)。
    单模块失败不影响整体（见 errors/notes）。

    Args:
        date: 查询日期 YYYY-MM-DD，默认今天。历史日期时板块资金流、涨跌家数自动降级。
    """
    return get_daily_review(date)


@mcp.tool()
def get_market_breadth_tool(date: str = "") -> str:
    """查询市场涨跌结构：涨跌家数(仅当日)、涨停/跌停/炸板家数、连板梯队

    Args:
        date: 查询日期 YYYY-MM-DD，默认今天。历史日期时涨跌家数不可用(降级说明)。
    """
    return get_market_breadth(date)


@mcp.tool()
def get_zt_pool_tool(pool: str = "涨停", date: str = "") -> str:
    """查询涨停生态股池明细（金额单位: 亿元）

    Args:
        pool: 涨停/炸板/跌停/强势/昨涨停，默认"涨停"
        date: 查询日期 YYYY-MM-DD，默认今天；无数据时自动回退最近交易日
    """
    return get_zt_pool(pool, date)


@mcp.tool()
def get_fund_flow_tool(
    scope: str = "全部", indicator: str = "今日", date: str = ""
) -> str:
    """查询主力资金：大盘（沪深两市净流入）与/或行业板块排名

    Args:
        scope: "全部"(大盘+板块) / "大盘" / "板块"，默认"全部"
        indicator: "今日" / "5日" / "10日"（仅板块口径），默认"今日"
        date: 查询日期 YYYY-MM-DD，默认今天
    """
    return get_fund_flow(scope, indicator, date)


@mcp.tool()
def get_margin_tool(market: str = "沪深", days: int = 10, date: str = "") -> str:
    """查询两融（融资余额，T-1 披露）

    Args:
        market: "沪深"(合计) / "沪" / "深"，默认"沪深"
        days: 返回最近 N 个交易日余额序列(1-30)，默认10
        date: 截止日期 YYYY-MM-DD，默认今天
    """
    return get_margin(market, days, date)


@mcp.tool()
def get_cffex_rank_tool(var: str = "IF", date: str = "", member: str = "") -> str:
    """查询中金所前20会员成交持仓排名（经纪席位口径）

    Args:
        var: IF/IH/IC/IM(股指期货) 或 IO/MO/HO(股指期权)，默认 IF
        date: 查询日期 YYYY-MM-DD，默认今天；非交易日自动回退
        member: 会员名子串过滤（如 "中信"），默认不过滤
    """
    return get_cffex_rank(var, date, member)


@mcp.tool()
def get_index_derivatives_tool(date: str = "") -> str:
    """查询股指衍生品指标：四大期指基差（含年化）+ 期权PCR（分交易所）

    Args:
        date: 查询日期 YYYY-MM-DD，默认今天
    """
    return get_index_derivatives(date)
```

- [ ] **Step 3: 写 live 冒烟测试**

`tests/test_live_smoke.py`：

```python
"""真实接口冒烟测试（默认跳过；显式运行: -m live）。需要 AKPROXY_TOKEN。"""

import json

import pytest

from utils import (
    get_cffex_rank,
    get_daily_review,
    get_index_derivatives,
    get_market_breadth,
)


@pytest.mark.live
def test_daily_review_live():
    out = get_daily_review()  # 今天；非交易日时部分模块降级属预期
    payload = json.loads(out)
    assert payload["success"] in (True, False)  # 只验证不崩、结构是 JSON 信封
    if payload["success"]:
        assert "indices" in payload["data"]


@pytest.mark.live
def test_breadth_and_derivatives_live():
    assert "success" in json.loads(get_market_breadth())
    assert "success" in json.loads(get_index_derivatives())
    assert "success" in json.loads(get_cffex_rank(var="IO", member="中信"))
```

- [ ] **Step 4: 全量测试 + server 导入冒烟**

Run: `./.venv/Scripts/python.exe -m pytest -v && ./.venv/Scripts/python.exe -c "import server; print('server import OK')"`
Expected: 全部单测 PASS（live 默认跳过），输出 `server import OK`。

- [ ] **Step 5: README 工具文档**

`README.md` 的"工具列表"章节末尾（`get_akproxy_token_info` 条目之后）追加：

```markdown
### `get_daily_review_tool`

一键复盘：当日 A 股全貌（七模块并行聚合，单模块失败不影响整体）

- **date**: 查询日期 YYYY-MM-DD，默认今天；历史日期时板块资金流、涨跌家数自动降级

返回七段：`indices`（五大指数）、`breadth`（涨跌家数/涨停/跌停/炸板/连板梯队）、
`fund_flow`（大盘+板块主力资金，东财口径）、`national_team`（国家队ETF份额变化与估算净申购）、
`derivatives`（IF/IH/IC/IM 基差含年化、期权 PCR、IF/IO 席位摘要）、`margin`（两融余额，T-1）、
以及 `notes`（降级说明）与 `errors`（模块级错误）。

### `get_market_breadth_tool`

市场涨跌结构（涨跌家数仅当日；涨停/跌停/炸板支持历史日期）

- **date**: 查询日期 YYYY-MM-DD，默认今天

### `get_zt_pool_tool`

涨停生态股池明细（金额单位亿元；无数据自动回退最近交易日）

- **pool**: 涨停/炸板/跌停/强势/昨涨停，默认"涨停"
- **date**: 查询日期 YYYY-MM-DD，默认今天

### `get_fund_flow_tool`

主力资金（东财口径：超大单+大单净额）

- **scope**: "全部"(大盘+板块) / "大盘" / "板块"，默认"全部"
- **indicator**: "今日" / "5日" / "10日"（仅板块口径），默认"今日"
- **date**: 查询日期 YYYY-MM-DD，默认今天

### `get_margin_tool`

两融余额（交易所 T-1 披露）

- **market**: "沪深"(合计) / "沪" / "深"，默认"沪深"
- **days**: 最近 N 个交易日序列(1-30)，默认 10
- **date**: 截止日期 YYYY-MM-DD，默认今天

### `get_cffex_rank_tool`

中金所前 20 会员成交持仓排名（经纪席位口径，直连中金所官网零积分）

- **var**: IF/IH/IC/IM（股指期货）或 IO/MO/HO（股指期权），默认 IF
- **date**: 查询日期 YYYY-MM-DD，默认今天；非交易日自动回退
- **member**: 会员名子串过滤（如 "中信"），默认不过滤

### `get_index_derivatives_tool`

股指衍生品指标：四大期指基差（含年化）+ 期权 PCR（分交易所）

- **date**: 查询日期 YYYY-MM-DD，默认今天
```

- [ ] **Step 6: Commit**

```bash
git add server.py README.md tests/test_live_smoke.py
git commit -m "feat: register 7 daily-review MCP tools with docs and live smoke tests"
```

---

## 验收清单（对照 spec）

1. `get_daily_review_tool` 一次返回七模块 + notes/errors — Task 7/8
2. 细分工具 6 个全部注册 — Task 8
3. 基差 = 现货收盘 − 主力结算价 + 年化（当月第三个周五折算）— Task 5
4. PCR 量/持仓双口径，分交易所不混算 — Task 5
5. 席位摘要 Top3，完整前20在细分工具，经纪口径注明 — Task 6
6. 两融 T-1 标注 — Task 4
7. 模块级降级 + 非交易日回退 — Task 3/5/6/7
8. 聚合并行 + 600s 缓存 — Task 7
9. 乐咕失效替代（东财快照算涨跌家数）— Task 3
10. IO/MO/HO CSV 自写拉取 — Task 6
11. 国家队复用 etf.py 不改动 — Task 7
12. live 冒烟默认跳过 — Task 1（pytest 配置）+ Task 8
