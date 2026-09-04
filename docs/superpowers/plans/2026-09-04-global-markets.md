# 全球市场监控工具 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 StockMCP 中新增 4 个全球市场 MCP 工具：`get_global_markets_tool`（六组快照聚合）、`get_stock_global_snapshot_tool`（全球个股 preset）、`get_fed_watch_tool`（美债 2Y/利差简版）、`get_global_linkage_review_tool`（隔夜 vs 当日 A 股对照）。

**Architecture:** 沿用 review_* 平铺模式：`utils/global_common.py`（腾讯/Yahoo/MOF 三个 fetcher + 代理配置）→ `global_rates.py`（美债）→ `global_markets.py`（六组+组装）→ `global_stocks.py` / `global_linkage.py`；server.py 注册 4 个 `*_tool`。核心原则：**服务不依赖代理存活**（国内直连为主源，Yahoo/代理仅为备源或唯一源字段的降级路径）；MOF 日债三级链（直连→代理→本地缓存）。

**Tech Stack:** Python 3.11+ / akshare 1.18.30 / requests（腾讯+MOF）/ curl_cffi（Yahoo 浏览器指纹，已有依赖）/ pandas / pytest / FastMCP。

## Global Constraints

- **命名约定**：utils 层函数不带 `_tool` 后缀，server.py 注册带 `_tool` 后缀的包装（与 `get_etf_daily`/`get_daily_review` 模式一致），杜绝同名递归。
- 所有工具返回 JSON 信封 `{"success": true, "data": {...}}` / `{"success": false, "error": "..."}`，使用 `review_common.json_ok/json_err`（已内建 NaN/Inf→None 净化）。
- 收益率单位 %（3 位小数），bp 变动 = (当日−前一日)×100 保留 1 位；价格 2 位；涨跌幅 2 位。
- 代理配置：环境变量 `YAHOO_PROXY`（默认 `http://192.168.3.10:7890`，空=禁用 Yahoo 路径）。**任何 Yahoo 调用前必须先检查代理已配置**；未配置/失败一律返回 None 由调用方降级，不抛异常。
- MOF 三级链顺序：直连 → 经 `YAHOO_PROXY` → 本地缓存（`data/global/`，整目录 gitignore）；全历史文件 `jgbcme_all.csv` 本地存在即不重复下载。
- akshare 函数 `from akshare import xxx` 顶部导入；模块内映射到 akshare 函数必须调用时解析模块全局名（lambda 或直接按名调用），保证 monkeypatch 生效（Task 3 复盘功能的既定教训）。
- 每任务 TDD：失败测试 → RED → 实现 → GREEN → 全量套件 → commit（conventional commits）。
- 测试命令：`./.venv/Scripts/python.exe -m pytest ...`（Windows Git Bash；`uv`/`pytest` 不在 PATH）。测试零真实网络。
- 模块级缓存（`CachedData`）在测试中必须手动重置为 None（`monkeypatch.setattr(mod, "_xxx_cache", None)` 或直接赋值）。

## 已验证事实（实现直接依赖，勿再猜测）

- **腾讯批量**：`https://qt.gtimg.cn/q=usIXIC,usINX,usDJI,usVIX,hkHSTECH`，HTTP 200，GBK；每个代码形如 `v_usIXIC="f0~f1~...~f40"` 波浪号分隔，**字段[1]=名称、[3]=现价、[4]=昨收、[32]=涨跌%**（字符串如 "1.40"）；无效代码整段缺失或空引号。`usSOX/jpNI225/krKOSPI/fxS*` 均无效代码。
- **Yahoo**：`https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d[&includePrePost=true]`；国内直连与裸 requests 均 403，必须 curl_cffi `impersonate="chrome"` + 代理；meta 取 `regularMarketPrice/chartPreviousClose/currency/shortName/postMarketPrice/preMarketPrice`。已验证代码：`NVDA MRVL COHR AVGO LITE APH ANET MU SNDK 2330.TW 000660.KS 005930.KS 285A.T SMH AIQ BOTZ ^SOX ^TNX ^TYX ^N225 ^KS11 ^VIX USDCNH=X`。延迟 0.4-0.6s/只。
- **MOF**：当月 `https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv`（T+0 日频，小文件）；全历史 `.../interest_rate/historical/jgbcme_all.csv`（13,291 行，~1.2MB）。编码 **cp932**；L0=标题行、L1=`Date,1Y,2Y,3Y,4Y,5Y,6Y,7Y,8Y,9Y,10Y,15Y,20Y,25Y,30Y,40Y`、数据行 `2026/9/3,1.563,...`（日期非补零）、尾部可能有 `※...` 注释行。直连与代理均 HTTP 200。解析策略：按正则 `^\d{4}/\d{1,2}/\d{1,2},` 匹配数据行，16 列。
- **东财全球指数** `index_global_spot_em()`：push2（走 proxy，唯一积分调用）；内置 `100.N225/100.KS11/100.UDI`；列含 `名称/最新价/涨跌幅`；名称用子串匹配：`日经`、`KOSPI`、`美元指数`。
- **东财全球期货** `futures_global_spot_em()`：futsseapi 直连零积分，640 行，列含 `代码/名称/最新价/涨跌幅/成交量/昨结`；名称：布伦特=`布伦特`、WTI=`NYMEX原油`、黄金=`COMEX黄金`；主力合约=名称匹配且有价中**成交量最大**行。
- **美债** `bond_zh_us_rate()`：datacenter.eastmoney.com 直连零积分；19 页分页约 4s，**内部 tqdm 进度条写 stderr**（用 `contextlib.redirect_stderr` 静默）；列：`日期`(YYYY-MM-DD)/`美国国债收益率2年/5年/10年/30年`/`美国国债收益率10年-2年`（另有中国列）。
- **汇率** `fx_spot_quote()`：新浪直连；列 `货币对/买报价/卖报价`；含 `USD/CNY` 行（在岸，近似 CNH 时必须加 note）。
- **新浪美股指数** `index_us_stock_sina(symbol=".SOX")`：全历史日 K，列 `date/open/high/low/close/volume/amount`；`.IXIC/.INX/.DJI/.SOX` 均有效。
- 2026-09-02 是周三；`prev_trading_days`（review_common 已有）可复用。

## File Structure

```txt
utils/
├── global_common.py     # yahoo_proxy/fetch_tencent_quotes/fetch_yahoo_quote/MOF三级链(_mof_get_text/_parse_jgb_csv/_monthly_df/_load_jgb_local/fetch_jgb_yields/jgb_yield_on)
├── global_rates.py      # _us_rows 缓存/us_treasury_section/get_fed_watch
├── global_markets.py    # _tencent_batch/_em_global_indexes 缓存 + us_equities/fx/asia/commodities/fear/bond_group 六个 section + get_global_markets
├── global_stocks.py     # PRESETS/get_stock_global_snapshot
└── global_linkage.py    # get_global_linkage_review
server.py                # 4 个 *_tool 注册
env.template             # 追加 YAHOO_PROXY
.gitignore               # 追加 data/
data/global/             # MOF 本地缓存（gitignore，运行时生成）
tests/
├── test_global_common.py
├── test_global_rates.py
├── test_global_markets.py
├── test_global_stocks.py
├── test_global_linkage.py
└── test_live_global_smoke.py  # live 标记，默认跳过
```

**Interfaces（跨任务契约）:**

- `global_common`: `yahoo_proxy() -> dict | None`；`fetch_tencent_quotes(codes: list[str]) -> dict[str, dict | None]`（每项 `{name, price, prev_close, chg_pct}`）；`fetch_yahoo_quote(symbol: str, include_pre_post: bool = False) -> dict | None`（`{symbol, name, currency, close, chg_pct[, after_hours_pct]}`）；`fetch_jgb_yields() -> dict`（`{date, japan10y, japan10y_chg_bp, japan20y, japan30y, notes}`）；`jgb_yield_on(day: date) -> dict | None`（`{date, y10, y20, y30}`）；`MOF_CACHE_DIR: Path`。
- `global_rates`: `us_treasury_section(day: date | None = None) -> dict`（`{date, us2y, us10y, us10y_chg_bp, us30y, us30y_chg_bp, spread_10y_2y, notes}`）；`get_fed_watch() -> str`。
- `global_markets`: `us_equities_section() -> dict`、`fx_section() -> dict`、`asia_section() -> dict`、`commodities_section() -> dict`、`fear_section() -> dict`、`bond_group_section() -> dict`、`get_global_markets(groups: str = "全部") -> str`。
- `global_stocks`: `get_stock_global_snapshot(symbols: str = "", preset: str = "ai_chain") -> str`。
- `global_linkage`: `get_global_linkage_review(date: str = "") -> str`。
- server.py 注册名：`get_global_markets_tool`、`get_stock_global_snapshot_tool`、`get_fed_watch_tool`、`get_global_linkage_review_tool`。

---

### Task 0: 工作区整理（前置，controller 手工执行）

- [ ] 确认工作区干净（`git status --short` 为空；上轮功能已合并推送）
- [ ] 从 main 切出 `feature/global-markets` 分支
- [ ] 初始化 `.superpowers/sdd/progress.md` 台账（`.superpowers/` 已 gitignore）

---

### Task 1: global_common.py — 代理配置 + 腾讯批量 fetcher

**Files:**
- Create: `utils/global_common.py`
- Test: `tests/test_global_common.py`

**Interfaces:**
- Consumes: `review_common.safe_num`
- Produces: `yahoo_proxy()`、`fetch_tencent_quotes()`（Task 5/6/8 消费）。

- [ ] **Step 1: 写失败测试**

`tests/test_global_common.py`：

```python
from types import SimpleNamespace

from utils import global_common as gc


def _tencent_body():
    f = [""] * 40
    f[1] = "纳斯达克"
    f[3] = "26584.06"
    f[4] = "26217.83"
    f[32] = "1.40"
    return f'v_usIXIC="{"~".join(f)}";v_usINX="";'


def test_yahoo_proxy(monkeypatch):
    monkeypatch.setenv("YAHOO_PROXY", "http://p:7890")
    assert gc.yahoo_proxy() == {"http": "http://p:7890", "https": "http://p:7890"}
    monkeypatch.delenv("YAHOO_PROXY")
    assert gc.yahoo_proxy() is None


def test_fetch_tencent_quotes(monkeypatch):
    fake = SimpleNamespace(
        get=lambda url, timeout: SimpleNamespace(
            status_code=200, content=_tencent_body().encode("gbk")
        )
    )
    monkeypatch.setattr(gc, "std_requests", fake)
    out = gc.fetch_tencent_quotes(["usIXIC", "usINX"])
    assert out["usIXIC"]["name"] == "纳斯达克"
    assert out["usIXIC"]["price"] == 26584.06
    assert out["usIXIC"]["prev_close"] == 26217.83
    assert out["usIXIC"]["chg_pct"] == 1.4
    assert out["usINX"] is None  # 空响应


def test_fetch_tencent_quotes_http_fail(monkeypatch):
    fake = SimpleNamespace(
        get=lambda url, timeout: SimpleNamespace(status_code=502, content=b"")
    )
    monkeypatch.setattr(gc, "std_requests", fake)
    assert gc.fetch_tencent_quotes(["usIXIC"]) == {"usIXIC": None}
```

- [ ] **Step 2: RED 确认**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_global_common.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现**

`utils/global_common.py`：

```python
#!/usr/bin/env python3
"""
全球市场模块共享 fetcher：腾讯批量行情、Yahoo(经代理)、MOF日债(三级链)。

代理原则：YAHOO_PROXY 未配置或不可达时，所有 Yahoo 路径返回 None，
由调用方降级到国内直连源——服务不依赖代理存活。
"""

import logging
import os
import re
from datetime import date as date_type
from pathlib import Path

import pandas as pd
import requests as std_requests
from curl_cffi import requests as cr_requests

from .review_common import safe_num
from .tools import CachedData

logger = logging.getLogger(__name__)

TENCENT_URL = "https://qt.gtimg.cn/q={codes}"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
MOF_MONTHLY_URL = (
    "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"
)
MOF_ALL_URL = (
    "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/"
    "historical/jgbcme_all.csv"
)
MOF_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "global"


def yahoo_proxy() -> dict | None:
    """YAHOO_PROXY 环境变量 -> requests proxies dict；未配置返回 None"""
    p = (os.getenv("YAHOO_PROXY") or "").strip()
    return {"http": p, "https": p} if p else None


def fetch_tencent_quotes(codes: list[str]) -> dict[str, dict | None]:
    """腾讯批量行情(GBK)；单代码失败返回 None，不抛异常"""
    out = {c: None for c in codes}
    try:
        r = std_requests.get(TENCENT_URL.format(codes=",".join(codes)), timeout=8)
        if r.status_code != 200:
            return out
        text = r.content.decode("gbk", errors="replace")
    except Exception as e:  # noqa: BLE001
        logger.warning("tencent quotes failed: %s", e)
        return out
    for code in codes:
        m = re.search(rf'v_{code}="([^"]*)"', text)
        if not m:
            continue
        f = m.group(1).split("~")
        if len(f) < 33:
            continue
        try:
            out[code] = {
                "name": f[1],
                "price": float(f[3]),
                "prev_close": float(f[4]),
                "chg_pct": safe_num(f[32], 2),
            }
        except (ValueError, IndexError):
            continue
    return out
```

- [ ] **Step 4: GREEN 确认**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_global_common.py -v`
Expected: 3 passed。

- [ ] **Step 5: Commit**

```bash
git add utils/global_common.py tests/test_global_common.py
git commit -m "feat: add proxy config and tencent batch quote fetcher"
```

---

### Task 2: global_common.py — Yahoo fetcher

**Files:**
- Modify: `utils/global_common.py`（追加）
- Test: `tests/test_global_common.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `yahoo_proxy`。
- Produces: `fetch_yahoo_quote(symbol, include_pre_post=False)`（Task 5/7/8 消费）。

- [ ] **Step 1: 追加失败测试**

`tests/test_global_common.py` 追加：

```python
def _yahoo_meta():
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "currency": "USD",
                        "shortName": "NVIDIA Corporation",
                        "regularMarketPrice": 228.45,
                        "chartPreviousClose": 227.97,
                        "postMarketPrice": 229.0,
                    }
                }
            ],
            "error": None,
        }
    }


def test_fetch_yahoo_quote(monkeypatch):
    monkeypatch.setenv("YAHOO_PROXY", "http://p:7890")
    fake = SimpleNamespace(
        get=lambda url, params, impersonate, timeout, proxies: SimpleNamespace(
            status_code=200, json=lambda: _yahoo_meta()
        )
    )
    monkeypatch.setattr(gc, "cr_requests", fake)
    q = gc.fetch_yahoo_quote("NVDA", include_pre_post=True)
    assert q["close"] == 228.45
    assert q["chg_pct"] == round((228.45 / 227.97 - 1) * 100, 2)
    assert q["after_hours_pct"] == round((229.0 / 228.45 - 1) * 100, 2)
    assert q["currency"] == "USD"


def test_fetch_yahoo_quote_no_proxy(monkeypatch):
    monkeypatch.delenv("YAHOO_PROXY", raising=False)
    assert gc.fetch_yahoo_quote("NVDA") is None


def test_fetch_yahoo_quote_http_fail(monkeypatch):
    monkeypatch.setenv("YAHOO_PROXY", "http://p:7890")
    fake = SimpleNamespace(
        get=lambda url, **kw: SimpleNamespace(status_code=403)
    )
    monkeypatch.setattr(gc, "cr_requests", fake)
    assert gc.fetch_yahoo_quote("NVDA") is None
```

- [ ] **Step 2: RED 确认**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_global_common.py -v`
Expected: 新增 3 条 FAIL（无 `fetch_yahoo_quote`）。

- [ ] **Step 3: 追加实现**

`utils/global_common.py` 追加：

```python
def fetch_yahoo_quote(symbol: str, include_pre_post: bool = False) -> dict | None:
    """Yahoo 单只行情(必须经 YAHOO_PROXY，curl_cffi 浏览器指纹)；
    未配置代理/请求失败/非200 一律返回 None 由调用方降级"""
    proxies = yahoo_proxy()
    if proxies is None:
        return None
    params = {"range": "5d", "interval": "1d"}
    if include_pre_post:
        params["includePrePost"] = "true"
    try:
        r = cr_requests.get(
            YAHOO_CHART_URL.format(symbol=symbol),
            params=params,
            impersonate="chrome",
            timeout=8,
            proxies=proxies,
        )
        if r.status_code != 200:
            return None
        meta = r.json()["chart"]["result"][0]["meta"]
    except Exception as e:  # noqa: BLE001
        logger.warning("yahoo %s failed: %s", symbol, e)
        return None
    px = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose")
    quote = {
        "symbol": symbol,
        "name": meta.get("shortName", ""),
        "currency": meta.get("currency", ""),
        "close": safe_num(px, 4),
        "chg_pct": round((px / prev - 1) * 100, 2) if px and prev else None,
    }
    if include_pre_post:
        post = meta.get("postMarketPrice")
        pre = meta.get("preMarketPrice")
        if post and px:
            quote["after_hours_pct"] = round((post / px - 1) * 100, 2)
        elif pre and px:
            quote["after_hours_pct"] = round((pre / px - 1) * 100, 2)
    return quote
```

- [ ] **Step 4: GREEN 确认**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_global_common.py -v`
Expected: 6 passed。

- [ ] **Step 5: Commit**

```bash
git add utils/global_common.py tests/test_global_common.py
git commit -m "feat: add yahoo quote fetcher with proxy degradation"
```

---

### Task 3: global_common.py — MOF 日债三级链 + 本地缓存

**Files:**
- Modify: `utils/global_common.py`（追加）
- Modify: `.gitignore`（追加 `data/`）
- Test: `tests/test_global_common.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `yahoo_proxy`。
- Produces: `fetch_jgb_yields()`（Task 5 bond_group 消费）、`jgb_yield_on(day)`（Task 8 消费）。

- [ ] **Step 1: 追加失败测试**

`tests/test_global_common.py` 追加：

```python
from datetime import date

MOF_TEXT = "\n".join(
    [
        "Interest Rate (September 2026),,,,,,,,,,,,,,,(Unit : %)",
        "Date,1Y,2Y,3Y,4Y,5Y,6Y,7Y,8Y,9Y,10Y,15Y,20Y,25Y,30Y,40Y",
        "2026/9/2,1.56,1.854,2.009,2.199,2.332,2.45,2.585,2.743,2.874,3.006,3.554,3.864,4.141,4.122,4.134",
        "2026/9/3,1.563,1.85,1.994,2.14,2.28,2.411,2.559,2.718,2.848,2.987,3.544,3.859,4.143,4.131,4.145",
        "※If you cannot download the latest csv data, please clear the browser's cache and download again.,",
    ]
)


def test_parse_jgb_csv():
    df = gc._parse_jgb_csv(MOF_TEXT)
    assert len(df) == 2  # 注释行/表头行被过滤
    assert str(df["date"].iloc[-1]) == "2026-09-03"
    assert df["10Y"].iloc[-1] == 2.987
    assert df["40Y"].iloc[-1] == 4.145


def test_fetch_jgb_yields_direct(monkeypatch, tmp_path):
    monkeypatch.setattr(gc, "MOF_CACHE_DIR", tmp_path / "global")
    monkeypatch.setattr(gc, "_mof_get_text", lambda url, use_proxy=False: MOF_TEXT)
    gc._monthly_cache = None
    out = gc.fetch_jgb_yields()
    assert out["date"] == "2026-09-03"
    assert out["japan10y"] == 2.987
    assert out["japan10y_chg_bp"] == round((2.987 - 3.006) * 100, 1)
    assert out["japan20y"] == 3.859 and out["japan30y"] == 4.131


def test_fetch_jgb_yields_fallback_local(monkeypatch, tmp_path):
    d = tmp_path / "global"
    d.mkdir()
    (d / "jgbcme_all.csv").write_text(MOF_TEXT, encoding="cp932")
    monkeypatch.setattr(gc, "MOF_CACHE_DIR", d)
    monkeypatch.setattr(gc, "_mof_get_text", lambda url, use_proxy=False: None)
    gc._monthly_cache = None
    out = gc.fetch_jgb_yields()
    assert out["japan10y"] == 2.987
    assert any("本地缓存" in n for n in out["notes"])


def test_fetch_jgb_yields_all_fail(monkeypatch, tmp_path):
    monkeypatch.setattr(gc, "MOF_CACHE_DIR", tmp_path / "empty")
    monkeypatch.setattr(gc, "_mof_get_text", lambda url, use_proxy=False: None)
    monkeypatch.setattr(gc, "_load_jgb_local", lambda: None)
    gc._monthly_cache = None
    try:
        gc.fetch_jgb_yields()
        assert False
    except ValueError:
        pass


def test_jgb_yield_on_from_local_history(monkeypatch, tmp_path):
    d = tmp_path / "global"
    d.mkdir()
    (d / "jgbcme_all.csv").write_text(MOF_TEXT, encoding="cp932")
    monkeypatch.setattr(gc, "MOF_CACHE_DIR", d)
    monkeypatch.setattr(gc, "_mof_get_text", lambda url, use_proxy=False: None)
    gc._monthly_cache = None
    out = gc.jgb_yield_on(date(2026, 9, 2))
    assert out == {"date": "2026-09-02", "y10": 3.006, "y20": 3.864, "y30": 4.122}
    assert gc.jgb_yield_on(date(2026, 8, 31)) is None
```

- [ ] **Step 2: RED 确认**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_global_common.py -v`
Expected: 新增 5 条 FAIL。

- [ ] **Step 3: 追加实现 + .gitignore**

`.gitignore` 末尾追加一行（注意先确认文件以换行结尾，避免上轮的行合并事故）：

```txt
data/
```

`utils/global_common.py` 追加：

```python
_JGB_TENORS = [
    "1Y", "2Y", "3Y", "4Y", "5Y", "6Y", "7Y", "8Y", "9Y",
    "10Y", "15Y", "20Y", "25Y", "30Y", "40Y",
]

_monthly_cache: CachedData | None = None  # 当月日债 DataFrame，TTL 3600s


def _mof_get_text(url: str, use_proxy: bool = False) -> str | None:
    """MOF CSV 文本；直连或经 YAHOO_PROXY；失败返回 None"""
    proxies = yahoo_proxy() if use_proxy else None
    if use_proxy and proxies is None:
        return None
    try:
        r = std_requests.get(
            url, timeout=15, proxies=proxies,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if r.status_code == 200 and len(r.content) > 100:
            return r.content.decode("cp932", errors="replace")
    except Exception as e:  # noqa: BLE001
        logger.warning("MOF fetch failed(%s): %s", "proxy" if use_proxy else "direct", e)
    return None


def _parse_jgb_csv(text: str) -> pd.DataFrame | None:
    """解析 MOF 收益率 CSV：只取 `YYYY/M/D,...` 数据行（16 列），忽略表头/注释行"""
    rows = []
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})\s*,", s)
        if not m:
            continue
        vals = [v.strip() for v in s.split(",")]
        if len(vals) < 16:
            continue
        try:
            d = date_type(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        rows.append([d] + [pd.to_numeric(v, errors="coerce") for v in vals[1:16]])
    if not rows:
        return None
    return pd.DataFrame(rows, columns=["date"] + _JGB_TENORS)


def _monthly_df() -> pd.DataFrame | None:
    """当月日债数据（直连→代理；缓存 3600s）"""
    global _monthly_cache
    if _monthly_cache is not None and not _monthly_cache.is_expired():
        return _monthly_cache.data
    text = _mof_get_text(MOF_MONTHLY_URL) or _mof_get_text(
        MOF_MONTHLY_URL, use_proxy=True
    )
    df = _parse_jgb_csv(text) if text else None
    _monthly_cache = CachedData(df, ttl=3600)
    return df


def _load_jgb_local() -> pd.DataFrame | None:
    """本地全历史缓存；文件缺失时按 直连→代理 下载并落盘（存在即不重复下载）"""
    p = MOF_CACHE_DIR / "jgbcme_all.csv"
    if not p.exists():
        text = _mof_get_text(MOF_ALL_URL) or _mof_get_text(MOF_ALL_URL, use_proxy=True)
        if text is None:
            return None
        df = _parse_jgb_csv(text)
        if df is None or df.empty:
            return None  # 非 CSV 内容(如代理错误页)不落盘, 避免永久污染本地缓存
        try:
            MOF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="cp932", errors="replace")
        except (OSError, UnicodeEncodeError) as e:  # noqa: BLE001 磁盘/编码问题不阻断返回
            logger.warning("MOF cache write failed: %s", e)
        return df
    try:
        return _parse_jgb_csv(p.read_text(encoding="cp932"))
    except Exception as e:  # noqa: BLE001
        logger.warning("MOF cache read failed: %s", e)
        return None


def fetch_jgb_yields() -> dict:
    """日本国债收益率最新值：月文件(直连→代理) → 本地全历史缓存 三级链"""
    df = _monthly_df()
    note = None
    if df is None or df.empty:
        df = _load_jgb_local()
        note = "使用本地缓存数据(数据日期见 date 字段)"
    if df is None or df.empty:
        raise ValueError("MOF日债数据不可用(直连/代理/本地缓存均失败)")

    last_day = df["date"].max()
    prev_series = df[df["date"] < last_day]["date"]
    prev_day = prev_series.max() if not prev_series.empty else None

    def _yield_on(d: date_type) -> pd.Series:
        return df[df["date"] == d].iloc[-1]

    r = _yield_on(last_day)
    p = _yield_on(prev_day) if prev_day is not None else None

    def _bp(tenor: str):
        cur, pre = r[tenor], (p[tenor] if p is not None else None)
        if pd.notna(cur) and pre is not None and pd.notna(pre):
            return round((cur - pre) * 100, 1)
        return None

    out = {
        "date": str(last_day),
        "japan10y": safe_num(r["10Y"], 3),
        "japan10y_chg_bp": _bp("10Y"),
        "japan20y": safe_num(r["20Y"], 3),
        "japan30y": safe_num(r["30Y"], 3),
        "notes": (
            ([note] if note else [])
            + ["日本财务省(MOF)基准收益率, T+0日频"]
        ),
    }
    return out


def jgb_yield_on(day: date_type) -> dict | None:
    """指定日期的日债 10Y/20Y/30Y：当月走月文件，更早走本地全历史"""
    df = _monthly_df()
    if df is not None and not df.empty:
        hit = df[df["date"] == day]
        if not hit.empty:
            r = hit.iloc[-1]
            return {
                "date": str(day), "y10": safe_num(r["10Y"], 3),
                "y20": safe_num(r["20Y"], 3), "y30": safe_num(r["30Y"], 3),
            }
    local = _load_jgb_local()
    if local is None or local.empty:
        return None
    hit = local[local["date"] == day]
    if hit.empty:
        return None
    r = hit.iloc[-1]
    return {
        "date": str(day), "y10": safe_num(r["10Y"], 3),
        "y20": safe_num(r["20Y"], 3), "y30": safe_num(r["30Y"], 3),
    }
```

（`df["date"]` 列为 object dtype 存 `datetime.date`，`<` 比较与 `.max()` 均可用。）

- [ ] **Step 4: GREEN 确认**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_global_common.py -v`
Expected: 11 passed。

- [ ] **Step 5: Commit**

```bash
git add utils/global_common.py tests/test_global_common.py .gitignore
git commit -m "feat: add MOF JGB yield fetcher with 3-level fallback and local cache"
```

---

### Task 4: global_rates.py — 美债收益率 + FedWatch

**Files:**
- Create: `utils/global_rates.py`
- Modify: `utils/__init__.py`（追加导出）
- Test: `tests/test_global_rates.py`

**Interfaces:**
- Consumes: `akshare.bond_zh_us_rate`、`review_common`（col_like/safe_num/json 信封/parse_day）、`tools.CachedData`。
- Produces: `us_treasury_section(day=None)`（Task 5 bond_group、Task 8 消费）；`get_fed_watch()`（Task 9 注册）。

- [ ] **Step 1: 写失败测试**

`tests/test_global_rates.py`：

```python
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
```

- [ ] **Step 2: RED 确认**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_global_rates.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现**

`utils/global_rates.py`：

```python
#!/usr/bin/env python3
"""
复盘·美债收益率模块：美债 2Y/10Y/30Y + 10Y-2Y 利差（FedWatch 简版数据源）。

数据源：bond_zh_us_rate（东财 datacenter 直连零积分，日频，19页分页约4s，
内部 tqdm 进度条需 redirect_stderr 静默）。
"""

import contextlib
import io
import logging

import pandas as pd
from akshare import bond_zh_us_rate

from .review_common import col_like, json_err, json_ok, safe_num
from .tools import CachedData, RateLimiter, with_retry

logger = logging.getLogger(__name__)

_us_treasury_cache: CachedData | None = None


def _us_rows() -> pd.DataFrame:
    """美债收益率全序列（缓存 3600s；akshare 内部 tqdm 进度条写 stderr，需静默）"""
    global _us_treasury_cache
    if _us_treasury_cache is not None and not _us_treasury_cache.is_expired():
        return _us_treasury_cache.data
    with contextlib.redirect_stderr(io.StringIO()):
        df = bond_zh_us_rate()
    if df is None or df.empty:
        raise ValueError("美债收益率接口无数据")
    df = df.copy()
    df["_d"] = df[col_like(df, "日期")].astype(str).str[:10]
    _us_treasury_cache = CachedData(df, ttl=3600)
    return df


def us_treasury_section(day=None) -> dict:
    """美债收益率：day=None 取最新；返回收益率%、bp 日变动"""
    df = _us_rows()
    if day is None:
        idx = df.index[-1]
    else:
        hit = df[df["_d"] == str(day)]
        if hit.empty:
            raise ValueError(f"{day} 无美债收益率数据(可能非交易日)")
        idx = hit.index[-1]
    r = df.loc[idx]
    prev = df.loc[idx - 1] if idx > df.index[0] else None

    def _get(row, keyword):
        c = col_like(df, keyword)
        try:
            return float(row[c]) if c else None
        except (TypeError, ValueError):
            return None

    def _bp(keyword):
        cur = _get(r, keyword)
        p = _get(prev, keyword) if prev is not None else None
        if cur is not None and p is not None:
            return round((cur - p) * 100, 1)
        return None

    return {
        "date": r["_d"],
        "us2y": safe_num(_get(r, "美国国债收益率2年"), 3),
        "us10y": safe_num(_get(r, "美国国债收益率10年"), 3),
        "us10y_chg_bp": _bp("美国国债收益率10年"),
        "us30y": safe_num(_get(r, "美国国债收益率30年"), 3),
        "us30y_chg_bp": _bp("美国国债收益率30年"),
        "spread_10y_2y": safe_num(_get(r, "美国国债收益率10年-2年"), 3),
        "notes": ["东财日频口径(日期=美东交易日)"],
    }


@RateLimiter(max_calls=10, time_window=60)
@with_retry(max_retries=3, delay=1.0, backoff=2.0)
def get_fed_watch() -> str:
    """美联储预期简版：美债 2Y 收益率 + 10Y-2Y 利差 + 2Y 周变动(bp)"""
    try:
        df = _us_rows()
        last = df.iloc[-1]
        first = df.index[0]
        week_ago = df.loc[max(df.index[-1] - 5, first)]
        c2 = col_like(df, "美国国债收益率2年")

        def _v(row):
            try:
                return float(row[c2])
            except (TypeError, ValueError):
                return None

        cur, prev = _v(last), _v(week_ago)
        sec = us_treasury_section()
        sec.pop("notes", None)
        data = {
            **sec,
            "us2y_chg_1w_bp": (
                round((cur - prev) * 100, 1)
                if cur is not None and prev is not None
                else None
            ),
            "note": "预期变化请结合 FOMC 日程解读(不做概率推算)",
        }
        return json_ok(data)
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_fed_watch: %s", e)
        return json_err(f"查询美债预期失败: {e}")
```

`utils/__init__.py` 追加：

```python
from .global_rates import get_fed_watch
```

及 `__all__` 加 `"get_fed_watch"`。

- [ ] **Step 4: GREEN 确认**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_global_rates.py -v`
Expected: 4 passed。

- [ ] **Step 5: Commit**

```bash
git add utils/global_rates.py utils/__init__.py tests/test_global_rates.py
git commit -m "feat: add US treasury section and fed watch tool function"
```

---

### Task 5: global_markets.py — 六组数据 section

**Files:**
- Create: `utils/global_markets.py`
- Test: `tests/test_global_markets.py`

**Interfaces:**
- Consumes: `global_common.fetch_tencent_quotes/fetch_yahoo_quote/fetch_jgb_yields`、`global_rates.us_treasury_section`、akshare `index_us_stock_sina/index_global_spot_em/futures_global_spot_em/fx_spot_quote`。
- Produces: 六个 `*_section()`（Task 6 组装消费）。

- [ ] **Step 1: 写失败测试**

`tests/test_global_markets.py`：

```python
import pandas as pd

from utils import global_markets as gm


def _tq(price=100.0, chg=1.0):
    return {"name": "x", "price": price, "prev_close": price / (1 + chg / 100), "chg_pct": chg}


def _sina_sox():
    return pd.DataFrame(
        {
            "date": ["2026-09-02", "2026-09-03"],
            "close": [11882.17, 11352.13],
            "volume": [0, 0],
        }
    )


def _em_global_df():
    return pd.DataFrame(
        {
            "名称": ["日经225", "韩国KOSPI", "美元指数", "恒生指数"],
            "最新价": [65059.06, 6709.87, 99.17, 25737.6],
            "涨跌幅": [-1.89, -1.16, 0.25, 2.08],
        }
    )


def _futures_df():
    return pd.DataFrame(
        {
            "名称": ["布伦特原油2712", "布伦特原油2801", "NYMEX原油2712", "COMEX黄金2706"],
            "最新价": [75.86, 75.38, 70.22, 4627.5],
            "涨跌幅": [0.11, 0.0, 0.05, -0.44],
            "成交量": [614, 3, 621, 9],
        }
    )


def _setup_tencent(monkeypatch, **over):
    table = {
        "usIXIC": _tq(26584.06, 1.40),
        "usINX": _tq(7747.71, 1.06),
        "usDJI": _tq(53686.11, 1.18),
        "usVIX": _tq(21.67, 0.0),
        "hkHSTECH": _tq(4580.76, 2.51),
    }
    table.update(over)
    monkeypatch.setattr(gm, "fetch_tencent_quotes", lambda codes: {c: table.get(c) for c in codes})
    gm._tencent_cache = None


def test_us_equities_section(monkeypatch):
    _setup_tencent(monkeypatch)
    monkeypatch.setattr(gm, "index_us_stock_sina", lambda symbol: _sina_sox())
    out = gm.us_equities_section()
    assert len(out["indexes"]) == 4
    by_name = {i["name"]: i for i in out["indexes"]}
    assert by_name["纳斯达克"]["close"] == 26584.06
    assert by_name["费城半导体"]["close"] == 11352.13
    assert by_name["费城半导体"]["chg_pct"] == round((11352.13 / 11882.17 - 1) * 100, 2)


def test_us_equities_section_tencent_fallback_yahoo(monkeypatch):
    _setup_tencent(monkeypatch, usDJI=None)
    monkeypatch.setattr(gm, "index_us_stock_sina", lambda symbol: _sina_sox())
    monkeypatch.setattr(
        gm, "fetch_yahoo_quote",
        lambda symbol, include_pre_post=False: {"close": 53686.0, "chg_pct": 1.18, "name": "道琼斯"},
    )
    out = gm.us_equities_section()
    by_name = {i["name"]: i for i in out["indexes"]}
    assert by_name["道琼斯"]["close"] == 53686.0  # 腾讯失败 -> Yahoo 备源成功
    assert "error" not in by_name["道琼斯"]


def test_fx_section(monkeypatch):
    gm._em_global_cache = None
    monkeypatch.setattr(gm, "index_global_spot_em", lambda: _em_global_df())
    monkeypatch.setattr(
        gm, "fetch_yahoo_quote",
        lambda symbol, include_pre_post=False: (
            {"close": 6.714, "chg_pct": -0.04} if symbol == "USDCNH=X" else None
        ),
    )
    out = gm.fx_section()
    assert out["dxy"] == 99.17 and out["dxy_chg_pct"] == 0.25
    assert out["usdcnh"] == 6.714


def test_fx_section_onshore_fallback(monkeypatch):
    gm._em_global_cache = None
    monkeypatch.setattr(gm, "index_global_spot_em", lambda: _em_global_df())
    monkeypatch.setattr(gm, "fetch_yahoo_quote", lambda symbol, include_pre_post=False: None)
    monkeypatch.setattr(
        gm, "fx_spot_quote",
        lambda: pd.DataFrame({"货币对": ["USD/CNY"], "买报价": [6.7163], "卖报价": [6.7164]}),
    )
    out = gm.fx_section()
    assert out["usdcnh"] == 6.7163
    assert any("在岸" in n for n in out["notes"])


def test_asia_section(monkeypatch):
    _setup_tencent(monkeypatch)
    gm._em_global_cache = None
    monkeypatch.setattr(gm, "index_global_spot_em", lambda: _em_global_df())
    out = gm.asia_section()
    by_name = {i["name"]: i for i in out["indexes"]}
    assert by_name["日经225"]["close"] == 65059.06
    assert by_name["KOSPI"]["close"] == 6709.87
    assert by_name["恒生科技"]["close"] == 4580.76


def test_commodities_section(monkeypatch):
    gm._futures_cache = None
    monkeypatch.setattr(gm, "futures_global_spot_em", lambda: _futures_df())
    out = gm.commodities_section()
    assert out["brent"]["price"] == 75.86  # 成交量最大主力
    assert out["wti"]["price"] == 70.22
    assert out["gold"]["price"] == 4627.5


def test_fear_section(monkeypatch):
    _setup_tencent(monkeypatch)
    out = gm.fear_section()
    assert out["vix"] == 21.67


def test_bond_group_section(monkeypatch):
    monkeypatch.setattr(
        gm, "us_treasury_section",
        lambda: {"date": "2026-09-03", "us10y": 4.77, "us10y_chg_bp": -1.0,
                 "us30y": 5.25, "us30y_chg_bp": -1.0, "us2y": 4.34,
                 "spread_10y_2y": 0.43, "notes": []},
    )
    monkeypatch.setattr(
        gm, "fetch_jgb_yields",
        lambda: {"date": "2026-09-03", "japan10y": 2.987, "japan10y_chg_bp": -1.9,
                 "japan20y": 3.859, "japan30y": 4.131, "notes": ["x"]},
    )
    out = gm.bond_group_section()
    assert out["us10y"] == 4.77 and out["japan10y"] == 2.987
```

- [ ] **Step 2: RED 确认**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_global_markets.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现**

`utils/global_markets.py`：

```python
#!/usr/bin/env python3
"""
全球市场快照模块：美股/美债/汇率/亚太/商品/恐慌 六组数据 section。

主源全部国内直连（腾讯批量/新浪SOX/东财全球指数走proxy1次/东财期货直连/
东财datacenter直连/MOF直连）；Yahoo(经代理)仅作备源；服务不依赖代理存活。
"""

import logging

import pandas as pd
from akshare import (
    fx_spot_quote,
    futures_global_spot_em,
    index_global_spot_em,
    index_us_stock_sina,
)

from .global_common import fetch_jgb_yields, fetch_tencent_quotes, fetch_yahoo_quote
from .global_rates import us_treasury_section
from .review_common import safe_num
from .tools import CachedData

logger = logging.getLogger(__name__)

# 腾讯一次批量拉满五码：美股三大 + VIX + 恒生科技（跨组共享，缓存60s）
_TENCENT_CODES = ["usIXIC", "usINX", "usDJI", "usVIX", "hkHSTECH"]
_tencent_cache: CachedData | None = None
_em_global_cache: CachedData | None = None
_futures_cache: CachedData | None = None


def _tencent_batch() -> dict[str, dict | None]:
    global _tencent_cache
    if _tencent_cache is not None and not _tencent_cache.is_expired():
        return _tencent_cache.data
    out = fetch_tencent_quotes(_TENCENT_CODES)
    _tencent_cache = CachedData(out, ttl=60)
    return out


def _em_global_indexes() -> pd.DataFrame | None:
    global _em_global_cache
    if _em_global_cache is not None and not _em_global_cache.is_expired():
        return _em_global_cache.data
    try:
        df = index_global_spot_em()
    except Exception as e:  # noqa: BLE001
        logger.warning("index_global_spot_em failed: %s", e)
        df = None
    _em_global_cache = CachedData(df, ttl=60)
    return df


def _em_index(kw: str, name: str) -> dict | None:
    df = _em_global_indexes()
    if df is None:
        return None
    hit = df[df["名称"].astype(str).str.contains(kw, na=False)]
    if hit.empty:
        return None
    r = hit.iloc[0]
    return {"name": name, "close": safe_num(r["最新价"], 2), "chg_pct": safe_num(r["涨跌幅"], 2)}


def _futures_df() -> pd.DataFrame | None:
    global _futures_cache
    if _futures_cache is not None and not _futures_cache.is_expired():
        return _futures_cache.data
    try:
        df = futures_global_spot_em()
    except Exception as e:  # noqa: BLE001
        logger.warning("futures_global_spot_em failed: %s", e)
        df = None
    _futures_cache = CachedData(df, ttl=60)
    return df


def _future_main(kw: str, name: str) -> dict | None:
    df = _futures_df()
    if df is None:
        return None
    hit = df[
        df["名称"].astype(str).str.contains(kw, na=False) & df["最新价"].notna()
    ]
    if hit.empty:
        return None
    r = hit.loc[pd.to_numeric(hit["成交量"], errors="coerce").idxmax()]
    return {"name": name, "price": safe_num(r["最新价"], 2), "chg_pct": safe_num(r["涨跌幅"], 2)}


def _tq_item(batch: dict, code: str, name: str, yahoo_symbol: str) -> dict:
    """腾讯条目 -> 统一格式；腾讯失败降级 Yahoo"""
    q = batch.get(code)
    if q:
        return {"name": name, "close": safe_num(q["price"], 2), "chg_pct": q["chg_pct"]}
    y = fetch_yahoo_quote(yahoo_symbol)
    if y and y.get("close") is not None:
        return {"name": name, "close": y["close"], "chg_pct": y["chg_pct"]}
    return {"name": name, "close": None, "chg_pct": None, "error": "腾讯与Yahoo均失败"}


def _sox() -> dict:
    """费城半导体：新浪日K主源 -> Yahoo 备源"""
    try:
        df = index_us_stock_sina(symbol=".SOX")
        if df is not None and len(df) >= 2:
            px, prev = float(df["close"].iloc[-1]), float(df["close"].iloc[-2])
            return {
                "name": "费城半导体",
                "close": safe_num(px, 2),
                "chg_pct": round((px / prev - 1) * 100, 2),
                "note": f"新浪日K {df['date'].iloc[-1]}",
            }
    except Exception as e:  # noqa: BLE001
        logger.warning("sina SOX failed: %s", e)
    y = fetch_yahoo_quote("^SOX")
    if y and y.get("close") is not None:
        return {"name": "费城半导体", "close": y["close"], "chg_pct": y["chg_pct"]}
    return {"name": "费城半导体", "close": None, "chg_pct": None, "error": "新浪与Yahoo均失败"}


def us_equities_section() -> dict:
    """美股组：纳指/标普/道指(腾讯批量) + 费半(新浪)"""
    batch = _tencent_batch()
    indexes = [
        _tq_item(batch, "usIXIC", "纳斯达克", "^IXIC"),
        _tq_item(batch, "usINX", "标普500", "^GSPC"),
        _tq_item(batch, "usDJI", "道琼斯", "^DJI"),
        _sox(),
    ]
    notes = []
    if any(i.get("close") is None for i in indexes):
        notes.append("部分美股指数降级或失败, 见 error 字段")
    return {"indexes": indexes, "note": "隔夜美股收盘", "notes": notes}


def fx_section() -> dict:
    """汇率组：DXY(东财) + USDCNH(Yahoo; 降级在岸USD/CNY近似)"""
    notes = []
    dxy = _em_index("美元指数", "美元指数")
    y = fetch_yahoo_quote("USDCNH=X")
    usdcnh = chg = None
    if y and y.get("close") is not None:
        usdcnh, chg = y["close"], y["chg_pct"]
    else:
        try:
            fx = fx_spot_quote()
            row = fx[fx["货币对"].astype(str) == "USD/CNY"].iloc[0]
            usdcnh = safe_num(row["买报价"], 4)
            chg = None
            notes.append("离岸CNH不可用, 以在岸USD/CNY近似")
        except Exception as e:  # noqa: BLE001
            notes.append(f"USDCNH获取失败: {e}")
    return {
        "dxy": dxy["close"] if dxy else None,
        "dxy_chg_pct": dxy["chg_pct"] if dxy else None,
        "usdcnh": usdcnh,
        "usdcnh_chg_pct": chg,
        "notes": notes,
    }


def asia_section() -> dict:
    """亚太组：日经/KOSPI(东财) + 恒生科技(腾讯)"""
    batch = _tencent_batch()
    indexes, notes = [], []
    for kw, name, ysym in (("日经", "日经225", "^N225"), ("KOSPI", "KOSPI", "^KS11")):
        item = _em_index(kw, name)
        if item is None:
            y = fetch_yahoo_quote(ysym)
            if y and y.get("close") is not None:
                item = {"name": name, "close": y["close"], "chg_pct": y["chg_pct"]}
                notes.append(f"{name} 东财降级Yahoo")
            else:
                item = {"name": name, "close": None, "chg_pct": None, "error": "东财与Yahoo均失败"}
        indexes.append(item)
    indexes.append(_tq_item(batch, "hkHSTECH", "恒生科技", "^HSTECH"))
    return {"indexes": indexes, "note": "亚太盘中或收盘", "notes": notes}


def commodities_section() -> dict:
    """商品组：布伦特/WTI/COMEX黄金 主力合约(成交量最大)"""
    notes = []
    out = {}
    for key, kw, name, ysym in (
        ("brent", "布伦特", "布伦特原油", "BZ=F"),
        ("wti", "NYMEX原油", "WTI原油", "CL=F"),
        ("gold", "COMEX黄金", "COMEX黄金", "GC=F"),
    ):
        item = _future_main(kw, name)
        if item is None:
            y = fetch_yahoo_quote(ysym)
            if y and y.get("close") is not None:
                item = {"name": name, "price": y["close"], "chg_pct": y["chg_pct"]}
                notes.append(f"{name} 东财降级Yahoo")
            else:
                item = {"name": name, "price": None, "chg_pct": None, "error": "东财与Yahoo均失败"}
        out[key] = item
    out["notes"] = notes
    return out


def fear_section() -> dict:
    """恐慌组：VIX(腾讯) -> Yahoo 备源"""
    batch = _tencent_batch()
    q = batch.get("usVIX")
    if q:
        return {"vix": safe_num(q["price"], 2), "vix_chg_pct": q["chg_pct"], "notes": []}
    y = fetch_yahoo_quote("^VIX")
    if y and y.get("close") is not None:
        return {"vix": y["close"], "vix_chg_pct": y["chg_pct"], "notes": ["VIX降级Yahoo"]}
    return {"vix": None, "vix_chg_pct": None, "notes": ["VIX获取失败(腾讯与Yahoo)"]}


def bond_group_section() -> dict:
    """美债组：美债(东财datacenter) + 日债(MOF三级链)，各自独立降级"""
    out, notes = {}, []
    try:
        us = us_treasury_section()
        notes += [f"[us] {n}" for n in us.pop("notes", [])]
        out.update(us)
    except Exception as e:  # noqa: BLE001
        out.update({"us10y": None, "us10y_chg_bp": None, "us30y": None, "us30y_chg_bp": None})
        notes.append(f"[us] 失败: {e}")
    try:
        jp = fetch_jgb_yields()
        notes += [f"[japan] {n}" for n in jp.pop("notes", [])]
        out.update(jp)
    except Exception as e:  # noqa: BLE001
        out.update({"japan10y": None, "japan10y_chg_bp": None, "japan20y": None, "japan30y": None})
        notes.append(f"[japan] 失败: {e}")
    if out.get("us10y") is None and out.get("japan10y") is None:
        raise ValueError("美债与日债均失败")
    out["notes"] = notes
    return out
```

- [ ] **Step 4: GREEN 确认**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_global_markets.py -v`
Expected: 8 passed。

- [ ] **Step 5: Commit**

```bash
git add utils/global_markets.py tests/test_global_markets.py
git commit -m "feat: add six global market group sections with source fallbacks"
```

---

### Task 6: global_markets.py — 组装 get_global_markets

**Files:**
- Modify: `utils/global_markets.py`（追加）
- Modify: `utils/__init__.py`（追加导出）
- Test: `tests/test_global_markets.py`（追加）

**Interfaces:**
- Consumes: Task 5 六个 section。
- Produces: `get_global_markets(groups="全部") -> str`（Task 9 注册）。

- [ ] **Step 1: 追加失败测试**

`tests/test_global_markets.py` 追加：

```python
def _patch_all_sections(monkeypatch, recorder):
    for name in ("us_equities_section", "fx_section", "asia_section",
                 "commodities_section", "fear_section", "bond_group_section"):
        def make(n):
            def fn():
                recorder.append(n)
                return {"notes": [], "marker": n}
            return fn
        monkeypatch.setattr(gm, name, make(name))


def test_get_global_markets_all(monkeypatch):
    gm._result_cache = {}
    recorder = []
    _patch_all_sections(monkeypatch, recorder)
    import json
    out = json.loads(gm.get_global_markets())
    assert out["success"] is True
    assert set(recorder) == {
        "us_equities_section", "fx_section", "asia_section",
        "commodities_section", "fear_section", "bond_group_section",
    }
    d = out["data"]
    assert d["美股"]["marker"] == "us_equities_section"
    from datetime import datetime as _dt
    assert _dt.fromisoformat(d["as_of"]).utcoffset() is not None  # 带时区的ISO8601


def test_get_global_markets_groups_filter(monkeypatch):
    gm._result_cache = {}
    recorder = []
    _patch_all_sections(monkeypatch, recorder)
    import json
    out = json.loads(gm.get_global_markets("美股,恐慌"))
    assert out["success"] is True
    assert set(recorder) == {"us_equities_section", "fear_section"}
    assert "美股" in out["data"] and "恐慌" in out["data"] and "亚太" not in out["data"]


def test_get_global_markets_cache(monkeypatch):
    gm._result_cache = {}
    recorder = []
    _patch_all_sections(monkeypatch, recorder)
    gm.get_global_markets("美股")
    gm.get_global_markets("美股")
    assert recorder.count("us_equities_section") == 1  # 第二次命中缓存


def test_get_global_markets_bad_group():
    import json
    out = json.loads(gm.get_global_markets("月球"))
    assert out["success"] is False
```

- [ ] **Step 2: RED 确认**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_global_markets.py -v`
Expected: 新增 4 条 FAIL。

- [ ] **Step 3: 追加实现**

`utils/global_markets.py` 追加：

```python
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .review_common import json_err, json_ok

GROUP_SECTIONS = {
    "美股": us_equities_section,
    "美债": bond_group_section,
    "汇率": fx_section,
    "亚太": asia_section,
    "商品": commodities_section,
    "恐慌": fear_section,
}

_result_cache: dict[str, CachedData] = {}


def get_global_markets(groups: str = "全部") -> str:
    """全球市场快照：六组并行聚合（盘前一键拉取）

    Args:
        groups: 逗号分隔的组名(美股/美债/汇率/亚太/商品/恐慌)，默认"全部"
    """
    try:
        g = (groups or "全部").strip()
        if g in ("", "全部", "all"):
            selected = list(GROUP_SECTIONS)
        else:
            selected = [s.strip() for s in g.replace("，", ",").split(",") if s.strip()]
            bad = [s for s in selected if s not in GROUP_SECTIONS]
            if bad:
                return json_err(
                    f"groups 仅支持 {'/'.join(GROUP_SECTIONS)} 或 全部，无法识别: {','.join(bad)}"
                )
        key = ",".join(selected)
        cached = _result_cache.get(key)
        if cached is not None and not cached.is_expired():
            return cached.data

        # jobs 必须在函数体内按名解析模块全局（globals()），保证测试 monkeypatch 生效
        _name_to_fn = {
            "美股": "us_equities_section",
            "美债": "bond_group_section",
            "汇率": "fx_section",
            "亚太": "asia_section",
            "商品": "commodities_section",
            "恐慌": "fear_section",
        }
        jobs = [(name, globals()[_name_to_fn[name]]) for name in selected]

        def run(name, fn):
            try:
                data = fn()
                mod_notes = data.pop("notes", [])
                return name, data, [f"[{name}] {n}" for n in mod_notes], None
            except Exception as e:  # noqa: BLE001
                logger.warning("group %s failed: %s", name, e)
                return name, None, [], str(e)

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda nf: run(nf[0], nf[1]), jobs))
        data = {
            "as_of": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        notes, errors = [], {}
        for name, section, mod_notes, err in results:
            data[name] = section
            notes.extend(mod_notes)
            if err:
                errors[name] = err
        if all(data.get(name) is None for name, _ in jobs):
            return json_err("全球市场六组数据均失败")
        if notes:
            data["notes"] = notes
        if errors:
            data["errors"] = errors
        out = json_ok(data)
        _result_cache[key] = CachedData(out, ttl=300)
        return out
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_global_markets: %s", e)
        return json_err(f"查询全球市场失败: {e}")
```

（`GROUP_SECTIONS` 模块级映射的 value 放 section 函数对象，仅用于 groups 校验与合法值列表；jobs 一律 `globals()[_name_to_fn[name]]` 按名解析以满足 monkeypatch 约束。）

`utils/__init__.py` 追加：

```python
from .global_markets import get_global_markets
```

及 `__all__` 加 `"get_global_markets"`。

- [ ] **Step 4: GREEN 确认**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_global_markets.py -v`
Expected: 12 passed。

- [ ] **Step 5: Commit**

```bash
git add utils/global_markets.py utils/__init__.py tests/test_global_markets.py
git commit -m "feat: add global markets aggregator with group filter and caching"
```

---

### Task 7: global_stocks.py — 全球个股快照

**Files:**
- Create: `utils/global_stocks.py`
- Modify: `utils/__init__.py`（追加导出）
- Test: `tests/test_global_stocks.py`

**Interfaces:**
- Consumes: `global_common.fetch_yahoo_quote/yahoo_proxy`。
- Produces: `get_stock_global_snapshot(symbols="", preset="ai_chain") -> str`（Task 9 注册）。

- [ ] **Step 1: 写失败测试**

`tests/test_global_stocks.py`：

```python
import json

from utils import global_stocks as gs


def test_presets():
    assert "NVDA" in gs.PRESETS["ai_chain"] and "2330.TW" in gs.PRESETS["ai_chain"]
    assert "000660.KS" in gs.PRESETS["storage"] and "285A.T" in gs.PRESETS["storage"]
    assert set(gs.PRESETS["etf"]) == {"SMH", "AIQ", "BOTZ"}
    assert "SMH" in gs.PRESETS["all"]


def test_get_stock_global_snapshot(monkeypatch):
    monkeypatch.setenv("YAHOO_PROXY", "http://p:7890")

    def fake(symbol, include_pre_post=False):
        if symbol == "NVDA":
            return {
                "symbol": "NVDA", "name": "NVIDIA", "currency": "USD",
                "close": 228.45, "chg_pct": 0.21,
                "after_hours_pct": 0.3,
            }
        if symbol == "000660.KS":
            return {
                "symbol": "000660.KS", "name": "SK hynix", "currency": "KRW",
                "close": 1664000.0, "chg_pct": 0.67,
            }
        return None

    monkeypatch.setattr(gs, "fetch_yahoo_quote", fake)
    out = json.loads(gs.get_stock_global_snapshot(symbols="NVDA,000660.KS"))
    assert out["success"] is True
    q = {i["symbol"]: i for i in out["data"]["quotes"]}
    assert q["NVDA"]["after_hours_pct"] == 0.3
    assert q["000660.KS"]["market"] == "KR"
    assert q["NVDA"]["market"] == "US"


def test_get_stock_global_snapshot_partial_fail(monkeypatch):
    monkeypatch.setenv("YAHOO_PROXY", "http://p:7890")

    def fake(symbol, include_pre_post=False):
        return {"symbol": symbol, "name": symbol, "currency": "USD",
                "close": 1.0, "chg_pct": 0.0} if symbol == "NVDA" else None

    monkeypatch.setattr(gs, "fetch_yahoo_quote", fake)
    out = json.loads(gs.get_stock_global_snapshot(symbols="NVDA,SMH"))
    q = {i["symbol"]: i for i in out["data"]["quotes"]}
    assert q["NVDA"]["close"] == 1.0
    assert q["SMH"]["close"] is None and "error" in q["SMH"]


def test_get_stock_global_snapshot_no_proxy(monkeypatch):
    monkeypatch.delenv("YAHOO_PROXY", raising=False)
    out = json.loads(gs.get_stock_global_snapshot())
    assert out["success"] is False
    assert "YAHOO_PROXY" in out["error"]
```

- [ ] **Step 2: RED 确认**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_global_stocks.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现**

`utils/global_stocks.py`：

```python
#!/usr/bin/env python3
"""
全球个股快照：光模块/存储链等海外对标组股价（Yahoo 经代理，唯一源）。

preset 服务端内置；美股附盘后/盘前涨跌(after_hours_pct)；单只失败该行 error 占位。
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import logging

from .global_common import fetch_yahoo_quote, yahoo_proxy
from .review_common import json_err, json_ok

logger = logging.getLogger(__name__)

PRESETS: dict[str, list[str]] = {
    "ai_chain": [  # AI算力链对标(新易盛/中际旭创映射) + 台积电
        "NVDA", "AVGO", "MRVL", "COHR", "LITE", "APH", "ANET", "2330.TW",
    ],
    "storage": [  # 存储链对标(东芯映射)
        "000660.KS", "005930.KS", "285A.T", "MU", "SNDK",
    ],
    "etf": ["SMH", "AIQ", "BOTZ"],  # AI 情绪代理 ETF
}
PRESETS["all"] = sum(PRESETS[k] for k in ("ai_chain", "storage", "etf"))


def _market_of(symbol: str) -> str:
    if symbol.endswith(".KS"):
        return "KR"
    if symbol.endswith(".T"):
        return "JP"
    if symbol.endswith(".TW"):
        return "TW"
    return "US"


def get_stock_global_snapshot(symbols: str = "", preset: str = "ai_chain") -> str:
    """查询全球个股快照（Yahoo 经代理，逐只并行）

    Args:
        symbols: 显式 Yahoo 代码(逗号分隔)，优先于 preset
        preset: ai_chain / storage / etf / all，默认 ai_chain
    """
    try:
        if yahoo_proxy() is None:
            return json_err("未配置 YAHOO_PROXY，本工具不可用(海外个股唯一数据源)")
        s = (symbols or "").strip().replace("，", ",")
        sym_list: list[str] = []
        if s:
            sym_list = [x.strip() for x in s.split(",") if x.strip()]
        else:
            p = (preset or "").strip()
            if p not in PRESETS:
                return json_err(f"preset 仅支持 {'/'.join(k for k in PRESETS if k != 'all')} 或 all，当前: {preset}")
            sym_list = PRESETS[p]

        def fetch(sym: str) -> dict:
            market = _market_of(sym)
            q = fetch_yahoo_quote(sym, include_pre_post=(market == "US"))
            if q is None or q.get("close") is None:
                return {"symbol": sym, "name": "", "market": market,
                        "close": None, "chg_pct": None, "error": "获取失败"}
            return {
                "symbol": sym,
                "name": q["name"],
                "market": market,
                "currency": q["currency"],
                "close": q["close"],
                "chg_pct": q["chg_pct"],
                **({"after_hours_pct": q["after_hours_pct"]}
                   if q.get("after_hours_pct") is not None else {}),
            }

        with ThreadPoolExecutor(max_workers=8) as pool:
            quotes = list(pool.map(fetch, sym_list))
        failed = [q["symbol"] for q in quotes if q.get("close") is None]
        data = {
            "as_of": datetime.now().astimezone().isoformat(timespec="seconds"),
            "quotes": quotes,
            "notes": (["以下代码获取失败(可能非交易时段或代码无效): " + ",".join(failed)]
                      if failed else []),
        }
        return json_ok(data)
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_stock_global_snapshot: %s", e)
        return json_err(f"查询全球个股失败: {e}")
```

`utils/__init__.py` 追加：

```python
from .global_stocks import get_stock_global_snapshot
```

及 `__all__` 加 `"get_stock_global_snapshot"`。

- [ ] **Step 4: GREEN 确认**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_global_stocks.py -v`
Expected: 4 passed。

- [ ] **Step 5: Commit**

```bash
git add utils/global_stocks.py utils/__init__.py tests/test_global_stocks.py
git commit -m "feat: add global stock snapshot with presets"
```

---

### Task 8: global_linkage.py — 共振复盘

**Files:**
- Create: `utils/global_linkage.py`
- Modify: `utils/__init__.py`（追加导出）
- Test: `tests/test_global_linkage.py`

**Interfaces:**
- Consumes: `akshare.index_us_stock_sina`、`global_rates.us_treasury_section`、`global_common.jgb_yield_on`、现有 `review_market.indices_section/breadth_section`、`review_funds.sector_fund_flow_section`、`review_common.parse_day/prev_trading_days`。
- Produces: `get_global_linkage_review(date="") -> str`（Task 9 注册）。

- [ ] **Step 1: 写失败测试**

`tests/test_global_linkage.py`：

```python
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
```

- [ ] **Step 2: RED 确认**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_global_linkage.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现**

`utils/global_linkage.py`：

```python
#!/usr/bin/env python3
"""
共振复盘：隔夜全球(美股/美债/日债) vs 当日 A 股 对照表（薄逻辑，判读留给 agent）。

隔夜腿 = 目标日前一交易日；A股腿复用现有 review sections；
板块资金流(电子/通信)依赖当日口径，历史日期自动降级为 null+note。
"""

from datetime import date as date_type
import logging

from akshare import index_us_stock_sina

from .global_common import jgb_yield_on
from .global_rates import us_treasury_section
from .review_common import json_err, json_ok, parse_day, prev_trading_days, safe_num
from .review_funds import sector_fund_flow_section
from .review_market import breadth_section, indices_section

logger = logging.getLogger(__name__)


def _us_pct(symbol: str, day: date_type) -> float | None:
    """新浪日K中目标日收盘涨跌幅（在完整序列里取目标行与其前一行）"""
    try:
        df = index_us_stock_sina(symbol=symbol)
        hit = df.index[df["date"].astype(str) == str(day)]
        if len(hit) == 0 or hit[-1] == df.index[0]:
            return None
        i = hit[-1]
        px, prev = float(df.loc[i, "close"]), float(df.loc[i - 1, "close"])
        return round((px / prev - 1) * 100, 2)
    except Exception as e:  # noqa: BLE001
        logger.warning("sina %s(%s) failed: %s", symbol, day, e)
        return None


def _sector_flow(day: date_type, keywords: tuple[str, ...]) -> list[dict]:
    """电子/通信板块主力净流入(亿元)；从 sector 全表 top5+bottom5 里找"""
    try:
        sec = sector_fund_flow_section(day, "今日")
        rows = (sec.get("top5") or []) + (sec.get("bottom5") or [])
        return [r for r in rows if any(k in str(r.get("name", "")) for k in keywords)]
    except Exception as e:  # noqa: BLE001
        logger.warning("sector flow failed: %s", e)
        return []


def get_global_linkage_review(date: str = "") -> str:
    """共振复盘：输入 A 股交易日，输出 隔夜全球 vs 当日 A 股 对照

    Args:
        date: A股交易日 YYYY-MM-DD，默认今天
    """
    try:
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        prev = prev_trading_days(day, 1)[0]
        prev2 = prev_trading_days(prev, 1)[0]

        overnight, notes = {}, []
        overnight["nasdaq_pct"] = _us_pct(".IXIC", prev)
        overnight["sox_pct"] = _us_pct(".SOX", prev)
        try:
            us = us_treasury_section(prev)
            overnight["us10y_chg_bp"] = us.get("us10y_chg_bp")
        except Exception as e:  # noqa: BLE001
            overnight["us10y_chg_bp"] = None
            notes.append(f"[us10y] 失败: {e}")
        j_prev = jgb_yield_on(prev)
        j_prev2 = jgb_yield_on(prev2)
        if j_prev:
            overnight["japan10y"] = j_prev["y10"]
            overnight["japan10y_chg_bp"] = (
                round((j_prev["y10"] - j_prev2["y10"]) * 100, 1)
                if j_prev2 and j_prev2.get("y10") is not None
                else None
            )
        else:
            overnight["japan10y"] = None
            notes.append("日债历史值不可用")
        overnight["vix"] = None
        notes.append("VIX无历史序列源, 隔夜值见 get_global_markets 恐慌组")

        a_share = {}
        try:
            idx = indices_section(day)
            items = {i["name"]: i["chg_pct"] for i in idx.get("items", [])}
            a_share["sse_pct"] = items.get("上证指数")
            a_share["chiNext_pct"] = items.get("创业板指")
        except Exception as e:  # noqa: BLE001
            a_share["sse_pct"] = a_share["chiNext_pct"] = None
            notes.append(f"[indices] 失败: {e}")
        try:
            br = breadth_section(day)
            a_share["up_down_ratio"] = (
                f"{br.get('up')}/{br.get('down')}"
                if br.get("up") is not None
                else None
            )
        except Exception as e:  # noqa: BLE001
            a_share["up_down_ratio"] = None
            notes.append(f"[breadth] 失败: {e}")
        elec = _sector_flow(day, ("电子",))
        comm = _sector_flow(day, ("通信",))
        a_share["elec_fund_flow_yi"] = elec[0]["main_net_yi"] if elec else None
        a_share["comm_fund_flow_yi"] = comm[0]["main_net_yi"] if comm else None
        if not elec and not comm:
            notes.append("电子/通信板块资金流仅当日口径可得, 历史日期降级为空")

        return json_ok(
            {
                "date": str(day),
                "overnight_prev_day": str(prev),
                "overnight": overnight,
                "a_share": a_share,
                "notes": notes,
            }
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_global_linkage_review: %s", e)
        return json_err(f"共振复盘失败: {e}")
```

`utils/__init__.py` 追加：

```python
from .global_linkage import get_global_linkage_review
```

及 `__all__` 加 `"get_global_linkage_review"`。

- [ ] **Step 4: GREEN 确认**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_global_linkage.py -v`
Expected: 2 passed。

- [ ] **Step 5: Commit**

```bash
git add utils/global_linkage.py utils/__init__.py tests/test_global_linkage.py
git commit -m "feat: add global linkage review combining overnight and A-share legs"
```

---

### Task 9: server.py 注册 + env.template + README + live 冒烟

**Files:**
- Modify: `server.py`
- Modify: `env.template`
- Modify: `README.md`
- Create: `tests/test_live_global_smoke.py`

**Interfaces:**
- Consumes: Task 4/6/7/8 的 `utils` 导出（`get_fed_watch/get_global_markets/get_stock_global_snapshot/get_global_linkage_review`，均无 `_tool` 后缀）。
- Produces: MCP 注册 `get_global_markets_tool/get_stock_global_snapshot_tool/get_fed_watch_tool/get_global_linkage_review_tool`。

- [ ] **Step 1: server.py 扩展导入**

`from utils import (...)` 块按字母序插入 4 个新名（保留全部现有项）：

```python
    get_fed_watch,
    get_global_linkage_review,
    get_global_markets,
    get_stock_global_snapshot,
```

- [ ] **Step 2: 追加 4 个注册（放在 `get_index_derivatives_tool` 之后、`@mcp.prompt()` 之前）**

```python
@mcp.tool()
def get_global_markets_tool(groups: str = "全部") -> str:
    """全球市场快照：美股(含费半)/美债(含日债10Y-30Y)/汇率(DXY+离岸人民币)/亚太(日经KOSPI恒科)/商品(布伦特WTI黄金)/恐慌(VIX) 六组并行聚合

    盘前场景一键拉取，判断当日A股输入性风险。单组失败不影响其他组(见errors/notes)。
    国内直连为主源，Yahoo(经代理)为备源。

    Args:
        groups: 逗号分隔组名(美股/美债/汇率/亚太/商品/恐慌)，默认"全部"
    """
    return get_global_markets(groups)


@mcp.tool()
def get_stock_global_snapshot_tool(symbols: str = "", preset: str = "ai_chain") -> str:
    """全球个股快照：光模块/存储链海外对标组（Yahoo经代理，需配置YAHOO_PROXY）

    Args:
        symbols: 显式Yahoo代码(逗号分隔，如 "NVDA,000660.KS")，优先于preset
        preset: ai_chain(NVDA/AVGO/MRVL/COHR/LITE/APH/ANET/台积电) /
                storage(SK海力士/三星/铠侠/美光/闪迪) / etf(SMH/AIQ/BOTZ) / all
    """
    return get_stock_global_snapshot(symbols, preset)


@mcp.tool()
def get_fed_watch_tool() -> str:
    """美联储预期简版：美债2Y收益率 + 10Y-2Y利差 + 2Y周变动(bp)，不做概率推算"""
    return get_fed_watch()


@mcp.tool()
def get_global_linkage_review_tool(date: str = "") -> str:
    """共振复盘：隔夜全球(美股/美债/日债) vs 当日A股对照表（判读留给AI）

    Args:
        date: A股交易日 YYYY-MM-DD，默认今天
    """
    return get_global_linkage_review(date)
```

- [ ] **Step 3: env.template 追加**

文件末尾追加（先读文件确认格式）：

```txt

# 海外行情代理（Yahoo全球个股/离岸汇率/日债fallback；空=禁用Yahoo路径，服务自动降级国内源）
YAHOO_PROXY=http://192.168.3.10:7890
```

- [ ] **Step 4: 写 live 冒烟**

`tests/test_live_global_smoke.py`：

```python
"""全球市场工具真实接口冒烟（默认跳过；-m live 运行）。需 YAHOO_PROXY 与 AKPROXY_TOKEN。"""

import json

import pytest

from utils import (
    get_fed_watch,
    get_global_linkage_review,
    get_global_markets,
    get_stock_global_snapshot,
)


@pytest.mark.live
def test_global_markets_live():
    payload = json.loads(get_global_markets())
    assert payload["success"] is True
    assert payload["data"]["美股"]["indexes"][0]["close"] is not None


@pytest.mark.live
def test_global_stocks_live():
    payload = json.loads(get_stock_global_snapshot(preset="ai_chain"))
    assert payload["success"] is True
    ok = [q for q in payload["data"]["quotes"] if q.get("close") is not None]
    assert len(ok) >= 5  # 允许个别失败


@pytest.mark.live
def test_fed_watch_live():
    assert json.loads(get_fed_watch())["success"] is True


@pytest.mark.live
def test_global_linkage_live():
    payload = json.loads(get_global_linkage_review())
    assert payload["success"] is True
```

- [ ] **Step 5: README 工具文档**

"工具列表"章节末尾追加：

```markdown
### `get_global_markets_tool`

全球市场快照（盘前一键）：六组并行聚合，单组失败不影响整体

- **groups**: 美股/美债/汇率/亚太/商品/恐慌（逗号分隔），默认"全部"

美股(纳指/标普/道指/费半)、美债(10Y/30Y+bp变动, 日债10Y/20Y/30Y来自日本财务省)、
汇率(美元指数+离岸人民币)、亚太(日经/KOSPI/恒生科技)、商品(布伦特/WTI/COMEX黄金主力)、
恐慌(VIX)。国内直连为主源，Yahoo(经`YAHOO_PROXY`代理)为备源。

### `get_stock_global_snapshot_tool`

全球个股快照（Yahoo 经 `YAHOO_PROXY` 代理，美股含盘后/盘前涨跌）

- **symbols**: 显式 Yahoo 代码（如 `NVDA,000660.KS`），优先于 preset
- **preset**: `ai_chain`(算力链8只含台积电) / `storage`(存储链5只) / `etf`(SMH/AIQ/BOTZ) / `all`

### `get_fed_watch_tool`

美联储预期简版：美债 2Y + 10Y-2Y 利差 + 2Y 周变动(bp)

### `get_global_linkage_review_tool`

共振复盘：隔夜全球（纳指/费半涨跌、美债/日债 bp 变动）vs 当日 A 股（指数/涨跌家数/电子通信板块资金流）对照

- **date**: A股交易日 YYYY-MM-DD，默认今天（板块资金流仅当日口径可得）
```

同时在 README「环境变量」表格追加一行：`YAHOO_PROXY | ❌ | 海外行情代理（Yahoo个股/离岸汇率/日债fallback），空=禁用自动降级 | http://192.168.3.10:7890`。

- [ ] **Step 6: 全量测试 + 导入冒烟**

Run: `./.venv/Scripts/python.exe -m pytest -v && ./.venv/Scripts/python.exe -c "import server; print('server import OK')"`
Expected: 既有 39 + 本计划新增（3+3+5+4+8+4+4+2=33）= 72 passed，live 全部 deselected，`server import OK`。

- [ ] **Step 7: Commit**

```bash
git add server.py env.template README.md tests/test_live_global_smoke.py
git commit -m "feat: register 4 global-market MCP tools with docs and live smoke tests"
```

---

## 验收清单（对照 spec §8）

1. 工具1 六组并行、字段级降级、10s 内返回 — Task 5/6
2. 工具2 preset=ai_chain 返回美股报价（8 只含台积电）— Task 7
3. 工具3 返回 2Y+利差+周变动 — Task 4
4. 工具4 隔夜 vs 当日对照（电子/通信资金流历史降级有 note）— Task 8
5. 中文描述 + success/data/error 信封 — 全部任务
6. 需求方 skill 工具表更新 — 部署后人工动作（不在本仓库）
7. MOF 三级链 + 本地缓存（data/ gitignore，全历史只下载一次）— Task 3
8. YAHOO_PROXY 未配置/失败全链路降级 — Task 2/5/7
