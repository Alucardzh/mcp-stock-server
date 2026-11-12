# 东财限流感知与板块数据升级 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把易限流的东财板块行情/涨停池调用收归 MCP 服务端，带限流感知调度、缓存、single-flight 与明确的 unavailable/no_open/stale 语义。

**Architecture:** 新增 `utils/push2_client.py` 作为东财 push2 家族唯一上游出口（pacing + 空响应识别 + 退避两轮 + TTL 缓存 + single-flight + stale 降级），`utils/board_quotes.py` 在其上构建板块快照（clist 列表 + ulist 批量开盘价，自动退化 stock/get），`utils/review_market.py` 给三池加 `full` 参数与缓存。响应统一带 `as_of/stale/cached`。

**Tech Stack:** Python 3.12 / FastMCP / requests（被 akshare-proxy-patch 接管 → akproxy）/ pandas / pytest（monkeypatch 单测 + `@pytest.mark.live` 冒烟）。

**设计文档:** `docs/superpowers/specs/2026-11-12-em-rate-limit-and-boards-design.md`（本计划的唯一需求来源）

## Global Constraints

- 无新依赖（requests/pandas/akshare/efinance/fastmcp 均已有）。
- 所有测试命令：`uv run pytest ...`（默认 `-m 'not live'` 已由 pyproject addopts 生效）。
- MCP 工具返回 JSON 信封 `{success, data|error}`；金额亿元；date 参数 `YYYY-MM-DD`。
- `board_name` **原文透传**（含 `游戏Ⅱ` 罗马数字、`自动化设` 截断名），禁止规范化/截断/补全。
- 错误三态（与设计文档 §4 完全一致）：
  - 业务错误 `{"success": false, "error": "...", "error_code": "BOARD_NOT_FOUND"}`（客户端不重试）
  - 上游失败无缓存 `{"success": true, "data": null, "status": "unavailable", "reason": "rate_limited"|"upstream_error"}`（终态）
  - 上游失败有旧缓存 → 正常 data + `stale: true`
  - 入口限流沿用现有 `RateLimiter`（`error_type: "RateLimitError"`，客户端可重试）
- 除 `get_zt_pool_tool` 加 `full` 参数、响应新增 `as_of/stale/cached` 字段外，不改任何现有工具返回结构。
- 代码风格：中文 docstring/注释（与现有库一致）；每个 commit 前跑 `uv run black <改动文件>`。
- pacing/退避常量：host 间最小间隔 `EM_PUSH2_MIN_INTERVAL`（默认 1.5s）；空响应退避 4s；最多 2 轮；不无限重试。

---

### Task 1: `utils/push2_client.py` —— 东财 push2 统一客户端

**Files:**
- Create: `utils/push2_client.py`
- Modify: `env.template`（追加 EM_PUSH2_MIN_INTERVAL 说明）
- Test: `tests/test_push2_client.py`

**Interfaces:**
- Consumes: `requests`（已被 `akshare_proxy_patch` 接管，容器内走 akproxy；本模块不感知）
- Produces（后续任务依赖的精确签名）:
  - `@dataclass EmResult: ok: bool; data: object = None; reason: str = ""; as_of: str = ""; cached: bool = False; stale: bool = False`
  - `em_push2_get(path: str, params: dict) -> EmResult`
  - `em_push2_cached(path: str, params: dict, cache_key: str, ttl: float) -> EmResult`
  - `class Push2Cache` with `get(key: str, ttl: float, fetch_fn) -> EmResult` / `clear()`
  - `CACHE: Push2Cache`（进程级共享实例）
  - `now_iso() -> str`、`reset_pacer()`
  - 常量 `PUSH2_HOSTS / EMPTY_BACKOFF_SECONDS=4.0 / EMPTY_MAX_ROUNDS=2`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_push2_client.py`：

```python
"""push2 客户端单测：空响应识别/退避/缓存/single-flight/stale 降级（全部 mock，无网络）。"""

import threading

import pytest

from utils import push2_client as pc


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    """默认快进：无 pacing、无真实 sleep、干净缓存。"""
    monkeypatch.setattr(pc, "_min_interval", lambda: 0.0)
    monkeypatch.setattr(pc.time, "sleep", lambda s: None)
    pc.CACHE.clear()
    pc.reset_pacer()
    yield
    pc.CACHE.clear()


def _mock_get(monkeypatch, payloads_or_exc, counter):
    """payloads_or_exc: 每次请求依次取一个（dict=响应体，Exception=抛出）；超出后重复最后一个。"""

    def fake(url, params=None, **kw):
        i = min(counter["n"], len(payloads_or_exc) - 1)
        item = payloads_or_exc[i]
        counter["n"] += 1
        if isinstance(item, Exception):
            raise item
        return _Resp(item)

    monkeypatch.setattr(pc.requests, "get", fake)


def test_http_get_ok(monkeypatch):
    c = {"n": 0}
    _mock_get(monkeypatch, [{"data": {"diff": [1]}}], c)
    res = pc._http_get(pc.PUSH2_HOSTS[0], "/x", {})
    assert res.ok and res.data == {"diff": [1]} and res.as_of


def test_http_get_empty_data_is_rate_limited(monkeypatch):
    c = {"n": 0}
    _mock_get(monkeypatch, [{"data": None}, {}], c)
    assert pc._http_get(pc.PUSH2_HOSTS[0], "/x", {}).reason == "rate_limited"
    assert pc._http_get(pc.PUSH2_HOSTS[0], "/x", {}).reason == "rate_limited"


def test_http_get_exception_is_upstream_error(monkeypatch):
    c = {"n": 0}
    _mock_get(monkeypatch, [ConnectionError("boom")], c)
    assert pc._http_get(pc.PUSH2_HOSTS[0], "/x", {}).reason == "upstream_error"


def test_em_get_two_rounds_then_fail(monkeypatch):
    """两轮 × 双 host 全空 → 失败，共 4 次上游请求，不无限重试。"""
    c = {"n": 0}
    _mock_get(monkeypatch, [{"data": None}] * 10, c)
    res = pc.em_push2_get("/api/x", {})
    assert not res.ok and res.reason == "rate_limited"
    assert c["n"] == len(pc.PUSH2_HOSTS) * pc.EMPTY_MAX_ROUNDS


def test_em_get_success_first_host(monkeypatch):
    c = {"n": 0}
    _mock_get(monkeypatch, [{"data": {"total": 1}}], c)
    assert pc.em_push2_get("/api/x", {}).ok
    assert c["n"] == 1


def test_pacing_enforced(monkeypatch):
    """同族第二个请求前必须等满最小间隔（sleep 被记录而非真实等待）。"""
    sleeps = []
    monkeypatch.setattr(pc, "_min_interval", lambda: 1.5)
    monkeypatch.setattr(pc.time, "sleep", lambda s: sleeps.append(s))
    c = {"n": 0}
    _mock_get(monkeypatch, [{"data": {"a": 1}}], c)
    assert pc.em_push2_get("/api/x", {}).ok  # 双 host，第二个前 _pace 等待
    assert len(sleeps) == 1 and sleeps[0] >= 1.4


def test_cache_hit_single_upstream(monkeypatch):
    c = {"n": 0}
    _mock_get(monkeypatch, [{"data": {"v": 1}}], c)
    r1 = pc.em_push2_cached("/api/x", {}, "k1", ttl=60)
    r2 = pc.em_push2_cached("/api/x", {}, "k1", ttl=60)
    assert c["n"] == 1
    assert r1.ok and not r1.cached and not r1.stale
    assert r2.ok and r2.cached and r2.as_of == r1.as_of


def test_failure_not_cached(monkeypatch):
    c = {"n": 0}
    _mock_get(monkeypatch, [{"data": None}] * 4 + [{"data": {"v": 1}}], c)
    r1 = pc.em_push2_cached("/api/x", {}, "k1", ttl=60)
    assert not r1.ok  # 两轮失败不写缓存
    r2 = pc.em_push2_cached("/api/x", {}, "k1", ttl=60)
    assert r2.ok and not r2.cached  # 失败后下次仍会打上游
    assert c["n"] == 5


def test_stale_degradation(monkeypatch):
    """缓存过期后重拉失败 → 返回旧值并标 stale。"""
    c = {"n": 0}
    _mock_get(monkeypatch, [{"data": {"v": "old"}}], c)
    pc.em_push2_cached("/api/x", {}, "k1", ttl=60)
    # 人工把缓存做过期
    pc.CACHE._entries["k1"].fetched_at -= 100
    _mock_get(monkeypatch, [{"data": None}] * 10, c)
    res = pc.em_push2_cached("/api/x", {}, "k1", ttl=60)
    assert res.ok and res.stale and res.data == {"v": "old"}


def test_single_flight_one_upstream(monkeypatch):
    """并发 5 线程同 key：fetch_fn 只执行一次，其余拿到 cached 结果。"""
    calls = {"n": 0}

    def slow_fetch():
        calls["n"] += 1
        _spin(0.08)  # 真实忙等，让其他线程在 key 锁上排队
        return pc.EmResult(ok=True, data={"v": 1}, as_of=pc.now_iso())

    results = []

    def worker():
        results.append(pc.CACHE.get("k", 60, slow_fetch))

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls["n"] == 1
    assert all(r.ok for r in results) and sum(r.cached for r in results) == 4


def _spin(seconds):
    import time as _t

    end = _t.perf_counter() + seconds
    while _t.perf_counter() < end:
        pass
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_push2_client.py -v`
Expected: 全部 FAIL（`ModuleNotFoundError: No module named 'utils.push2_client'`）

- [ ] **Step 3: 实现 `utils/push2_client.py`**

```python
#!/usr/bin/env python3
"""东财 push2 家族统一客户端：限流感知调度 + TTL 缓存 + single-flight。

所有板块行情上游请求（clist / ulist / stock-get）的唯一出口：
- 走已被 akshare_proxy_patch 接管的 requests（→ akproxy 出口）；
- HTTP 200 但 data 为空 = 东财限流实测形态（非错误码），退避后二轮重试，仍空则失败返回；
- 家族级 pacing：连续上游请求强制最小间隔（env EM_PUSH2_MIN_INTERVAL，默认 1.5 秒）；
- 多 key TTL 缓存 + single-flight（同 key 并发只出一次上游请求）；失败不写缓存；
- 缓存过期后重拉失败但有旧缓存 → 降级返回旧数据并标 stale。

涨停池（push2ex，akshare 直连不经 akproxy）不经过本模块 pacing，
但复用 Push2Cache 做缓存（见 review_market._pool_cached）。
"""

from dataclasses import dataclass
from datetime import datetime
import logging
import os
import threading
import time

import requests

logger = logging.getLogger(__name__)

PUSH2_HOSTS = ["https://push2.eastmoney.com", "https://82.push2.eastmoney.com"]

EMPTY_BACKOFF_SECONDS = 4.0  # 一轮全空后的退避（实测密集重试会延长限流窗口）
EMPTY_MAX_ROUNDS = 2         # 最多两轮，不无限重试


def _min_interval() -> float:
    """家族级最小请求间隔（秒），env EM_PUSH2_MIN_INTERVAL 可调。"""
    try:
        return max(0.0, float(os.getenv("EM_PUSH2_MIN_INTERVAL", "1.5")))
    except ValueError:
        return 1.5


def now_iso() -> str:
    """数据获取时刻，ISO8601 带本地时区（容器 TZ=Asia/Shanghai）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class EmResult:
    """push2 客户端统一结果。

    ok=True: data 为上游 data 字段（已解析，dict/list，缓存后为规范化快照）；
    ok=False: reason="rate_limited"（200 空响应形态）或 "upstream_error"（HTTP/解析异常）。
    cached=True: 命中未过期缓存；stale=True: 过期后重拉失败降级返回的旧值。
    """

    ok: bool
    data: object = None
    reason: str = ""
    as_of: str = ""
    cached: bool = False
    stale: bool = False


# ---- 家族级 pacing：连续上游请求间隔 >= _min_interval() ----

_pace_lock = threading.Lock()
_last_request_at = 0.0  # time.monotonic()


def _pace():
    global _last_request_at
    with _pace_lock:
        wait = _last_request_at + _min_interval() - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def reset_pacer():
    """测试用：清空 pacing 基准。"""
    global _last_request_at
    with _pace_lock:
        _last_request_at = 0.0


def _http_get(host: str, path: str, params: dict) -> EmResult:
    """单次 GET：异常 → upstream_error；200 且 data 非空 → ok；data 空 → rate_limited。"""
    try:
        r = requests.get(f"{host}{path}", params=params, timeout=10)
        payload = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("push2 %s failed: %s", path, e)
        return EmResult(ok=False, reason="upstream_error")
    data = payload.get("data") if isinstance(payload, dict) else None
    if data:
        return EmResult(ok=True, data=data, as_of=now_iso())
    return EmResult(ok=False, reason="rate_limited")


def em_push2_get(path: str, params: dict) -> EmResult:
    """东财 push2 GET：pacing + host 轮询 + 空响应退避两轮。

    一轮 = 依次请求 PUSH2_HOSTS（每次前 _pace）；任一 host 拿到非空 data 即成功。
    一轮全失败 → sleep EMPTY_BACKOFF_SECONDS → 第二轮 → 仍失败 → 返回最后一次失败。
    """
    last = EmResult(ok=False, reason="upstream_error")
    for round_no in range(EMPTY_MAX_ROUNDS):
        for host in PUSH2_HOSTS:
            _pace()
            last = _http_get(host, path, params)
            if last.ok:
                return last
        if round_no < EMPTY_MAX_ROUNDS - 1:
            time.sleep(EMPTY_BACKOFF_SECONDS)
    return last


class _CacheEntry:
    __slots__ = ("data", "as_of", "fetched_at")

    def __init__(self, data, as_of: str, fetched_at: float):
        self.data = data
        self.as_of = as_of
        self.fetched_at = fetched_at


class Push2Cache:
    """多 key TTL 缓存 + single-flight：同 key 并发只出一次上游请求，失败不写缓存。"""

    def __init__(self):
        self._entries: dict = {}   # key -> _CacheEntry
        self._key_locks: dict = {}  # key -> threading.Lock（single-flight）
        self._guard = threading.Lock()

    def _key_lock(self, key: str) -> threading.Lock:
        with self._guard:
            lk = self._key_locks.get(key)
            if lk is None:
                lk = threading.Lock()
                self._key_locks[key] = lk
            return lk

    @staticmethod
    def _fresh(entry, ttl: float) -> bool:
        return entry is not None and time.time() - entry.fetched_at <= ttl

    def get(self, key: str, ttl: float, fetch_fn) -> EmResult:
        """读缓存 → 过期则 single-flight 拉取。

        fetch_fn: () -> EmResult（内部含 pacing/重试；不抛异常）。
        命中未过期缓存 → cached=True；拉取成功 → cached=False 且写缓存；
        拉取失败不写缓存，有旧缓存 → 旧值 stale=True；无旧缓存 → 失败 EmResult。
        """
        with self._guard:
            entry = self._entries.get(key)
        if self._fresh(entry, ttl):
            return EmResult(ok=True, data=entry.data, as_of=entry.as_of, cached=True)

        with self._key_lock(key):
            # double-check：等锁期间可能已被并发请求刷新
            with self._guard:
                entry = self._entries.get(key)
            if self._fresh(entry, ttl):
                return EmResult(
                    ok=True, data=entry.data, as_of=entry.as_of, cached=True
                )

            res = fetch_fn()
            if res.ok:
                with self._guard:
                    self._entries[key] = _CacheEntry(res.data, res.as_of, time.time())
                return res
            with self._guard:
                entry = self._entries.get(key)
            if entry is not None:
                logger.warning("push2 cache key=%s refresh failed, serving stale", key)
                return EmResult(
                    ok=True, data=entry.data, as_of=entry.as_of, stale=True
                )
            return res

    def clear(self):
        with self._guard:
            self._entries.clear()
            self._key_locks.clear()


CACHE = Push2Cache()  # 进程级共享缓存


def em_push2_cached(path: str, params: dict, cache_key: str, ttl: float) -> EmResult:
    """带缓存的 push2 GET（缓存/stale 语义见 Push2Cache.get）。"""
    return CACHE.get(cache_key, ttl, lambda: em_push2_get(path, params))
```

`env.template` 末尾追加一行：

```
# 东财 push2 家族上游请求最小间隔（秒），默认 1.5；竞价限流时可调大
EM_PUSH2_MIN_INTERVAL=1.5
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_push2_client.py -v`
Expected: 10 passed

- [ ] **Step 5: 格式化并提交**

```bash
uv run black utils/push2_client.py tests/test_push2_client.py
uv run pytest tests/test_push2_client.py -q
git add utils/push2_client.py tests/test_push2_client.py env.template
git commit -m "feat: push2 client with rate-limit awareness, TTL cache and single-flight"
```

---

### Task 2: 三池 `full` 参数 + 池缓存 + `zt_industry_counts`

**Files:**
- Modify: `utils/review_common.py`（追加 2 个信封助手）
- Modify: `utils/review_market.py`（`get_zt_pool` 加 full + 缓存 + as_of/stale/cached；`breadth_section` 改用缓存池；新增 `zt_industry_counts`）
- Modify: `mcp_tools/review.py`（`get_zt_pool_tool` 透传 full）
- Test: `tests/test_review_market.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `CACHE`、`EmResult`、`now_iso`（`from .push2_client import CACHE, EmResult, now_iso`）
- Produces:
  - `get_zt_pool(pool: str = "涨停", date: str = "", full: bool = False) -> str`（信封 data 新增 `as_of/stale/cached/source`）
  - `zt_industry_counts(date: str = "") -> dict[str, int] | None`（涨停池按所属行业 4 字截断名分桶计数；失败/无数据返回 None）——Task 3 依赖
  - `review_common.json_unavailable(reason: str) -> str`、`review_common.json_err_code(code: str, msg: str) -> str`——Task 3 依赖

- [ ] **Step 1: 写失败测试**

在 `tests/test_review_market.py` 追加（文件顶部已 `from utils import review_market as rm`，再补 import；若文件内已有 get_zt_pool 相关测试与缓存冲突，靠 autouse fixture 清缓存解决）：

```python
# ---- 追加到 tests/test_review_market.py（pytest/json/pd/rm 顶部已 import）----


@pytest.fixture(autouse=True)
def _clear_push2_cache():
    """三池/push2 缓存跨用例隔离（对既有用例无副作用）。"""
    from utils.push2_client import CACHE

    CACHE.clear()
    yield
    CACHE.clear()


def _pool_df(n=70):
    return pd.DataFrame(
        {
            "代码": [f"60000{i}" for i in range(n)],
            "名称": [f"股{i}" for i in range(n)],
            "涨跌幅": [10.0] * n,
            "最新价": [10.0 + i for i in range(n)],
            "成交额": [1e8] * n,
            "换手率": [1.0] * n,
            "封板资金": [6.5e8] * n,
            "首次封板时间": ["092500"] * n,
            "最后封板时间": ["093000"] * n,
            "炸板次数": [0] * n,
            "涨停统计": ["1/1"] * n,
            "连板数": [1] * n,
            "所属行业": ["元件" if i % 2 else "半导体" for i in range(n)],
        }
    )


def test_zt_pool_truncated_by_default(monkeypatch):
    monkeypatch.setitem(rm.POOL_FETCHERS, "涨停", lambda date: _pool_df(70))
    out = json.loads(rm.get_zt_pool("涨停", "2026-11-12"))
    assert out["success"] and out["data"]["count"] == 70
    assert len(out["data"]["items"]) == rm.MAX_POOL_ROWS  # 默认截断 60
    assert out["data"]["as_of"] and out["data"]["source"] == "eastmoney_push2ex"


def test_zt_pool_full_returns_all(monkeypatch):
    monkeypatch.setitem(rm.POOL_FETCHERS, "涨停", lambda date: _pool_df(85))
    out = json.loads(rm.get_zt_pool("涨停", "2026-11-12", full=True))
    assert out["data"]["count"] == 85 and len(out["data"]["items"]) == 85


def test_zt_pool_cached_second_call(monkeypatch):
    counter = {"n": 0}

    def fetch(date):
        counter["n"] += 1
        return _pool_df(3)

    monkeypatch.setitem(rm.POOL_FETCHERS, "涨停", fetch)
    rm.get_zt_pool("涨停", "2026-11-12")
    out = json.loads(rm.get_zt_pool("涨停", "2026-11-12"))
    assert counter["n"] == 1 and out["data"]["cached"] is True


def test_zt_industry_counts(monkeypatch):
    monkeypatch.setitem(rm.POOL_FETCHERS, "涨停", lambda date: _pool_df(6))
    counts = rm.zt_industry_counts("2026-11-12")
    assert counts == {"元件": 3, "半导体": 3}


def test_zt_industry_counts_failure_returns_none(monkeypatch):
    def boom(date):
        raise ValueError("upstream down")

    monkeypatch.setitem(rm.POOL_FETCHERS, "涨停", boom)
    assert rm.zt_industry_counts("2026-11-12") is None
```

（若既有用例 `test_zt_pool_*` 因缓存引入互相污染而挂，autouse fixture 已处理；仍挂则在对应既有用例内手动 `rm` 模块级 `CACHE.clear()`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_review_market.py -v`
Expected: 新增 5 个 FAIL（`TypeError: get_zt_pool() got an unexpected keyword argument 'full'` / `AttributeError: ... zt_industry_counts`）

- [ ] **Step 3: 实现**

`utils/review_common.py` 末尾追加：

```python
def json_unavailable(reason: str) -> str:
    """上游不可用（限流/接口失败）：data=null + status/reason，与业务错误区分。

    上层必须把该形态当「缺数据」而非「条件不达标」处理（设计文档 §4）。
    """
    return json.dumps(
        {"success": True, "data": None, "status": "unavailable", "reason": reason},
        ensure_ascii=False,
        indent=2,
    )


def json_err_code(code: str, msg: str) -> str:
    """业务错误（客户端不可重试类），带机器可读 error_code。"""
    return json.dumps(
        {"success": False, "error": msg, "error_code": code},
        ensure_ascii=False,
        indent=2,
    )
```

`utils/review_market.py` 改动（保留其余代码不动）：

顶部 import 区追加：

```python
from .push2_client import CACHE as PUSH2_CACHE
from .push2_client import EmResult, now_iso
```

`MAX_POOL_ROWS = 60` 之后追加：

```python
POOL_CACHE_TTL_TODAY = 60    # 当日三池缓存（秒）
POOL_CACHE_TTL_HISTORY = 600  # 历史日期三池缓存（数据不变，放宽）


def _pool_ttl(day: date_type) -> int:
    return POOL_CACHE_TODAY if day == datetime.now().date() else POOL_CACHE_TTL_HISTORY


def _pool_cached(pool: str, day: date_type):
    """三池 DataFrame 缓存（single-flight；拉取失败不写缓存，stale 降级）。

    akshare push2ex 直连不经 push2_client pacing（不同限流桶），仅复用缓存层。
    返回 (df|None, as_of, cached, stale)。
    """

    def _fetch() -> EmResult:
        df = _pool_rows(pool, day)
        if df is None or df.empty:
            # 空数据不缓存：可能是非交易日，也可能是拉取失败（_pool_rows 内部已吞异常）
            return EmResult(ok=False, reason="upstream_error")
        return EmResult(ok=True, data=df, as_of=now_iso())

    res = PUSH2_CACHE.get(f"pool:{pool}:{day}", _pool_ttl(day), _fetch)
    df = res.data if res.ok else None
    return df, (res.as_of if res.ok else ""), res.cached, res.stale
```

`_pool_with_fallback` 整体替换为：

```python
def _pool_with_fallback(
    pool: str, day: date_type
) -> tuple[pd.DataFrame | None, date_type, list[str], str, bool, bool]:
    """(df, actual_day, notes, as_of, cached, stale)；无数据时 df=None。"""
    notes = []
    for d in [day, *prev_trading_days(day, 5)]:
        df, as_of, cached, stale = _pool_cached(pool, d)
        if df is not None and not df.empty:
            if d != day:
                notes.append(f"{day} 无数据，返回最近交易日 {d} 的{pool}股池")
            return df, d, notes, as_of, cached, stale
    return None, day, notes, "", False, False
```

`breadth_section` 中三处 `_pool_rows(...)` 调用替换为 `_pool_cached(...)[0]`：

```python
    zt = _pool_cached("涨停", day)[0]
    ...
    for key, pool in (("zhaban", "炸板"), ("limit_down", "跌停")):
        df = _pool_cached(pool, day)[0]
```

（保持其余逻辑不变。）

`get_zt_pool` 整体替换（注意：去掉永不触发的 `@with_retry`——函数体全捕获异常返回信封；保留 `@RateLimiter`）：

```python
@RateLimiter(max_calls=10, time_window=60)
def get_zt_pool(pool: str = "涨停", date: str = "", full: bool = False) -> str:
    """查询涨停生态股池明细（金额字段单位: 亿元）

    Args:
        pool: 涨停/炸板/跌停/强势/昨涨停，默认"涨停"
        date: 查询日期 YYYY-MM-DD，默认今天；无数据时自动回退最近交易日
        full: 默认 false 截断前 60 条（count 仍为全量家数）；true 返回全量行，
              链强度/炸板率/晋级率/连板梯队必须用全量
    """
    try:
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        key = (pool or "").strip()
        if key not in POOL_FETCHERS:
            return json_err(f"pool 仅支持 {'/'.join(POOL_FETCHERS)}，当前: {pool}")
        df, actual_day, notes, as_of, cached, stale = _pool_with_fallback(key, day)
        if df is None or df.empty:
            return json_err(f"{day} 前后数个交易日内无{key}股池数据")
        fields = [c for c in _POOL_FIELDS[key] if c in df.columns]
        rows = []
        for _, r in (df if full else df.head(MAX_POOL_ROWS)).iterrows():
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
                "as_of": as_of,
                "stale": stale,
                "cached": cached,
                "source": "eastmoney_push2ex",
            }
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_zt_pool: %s", e)
        return json_err(f"查询{pool}股池失败: {e}")


def zt_industry_counts(date: str = "") -> dict | None:
    """涨停池按所属行业分桶计数（行业名为东财 4 字截断原文）。

    供板块行情计算 limit_up（板块内涨停家数）；失败/无数据返回 None，
    调用方省略字段即可，不报错。缓存跟随三池当日 TTL。
    """
    day = parse_day(date) or datetime.now().date()
    try:
        df = _pool_cached("涨停", day)[0]
        if df is None or df.empty or "所属行业" not in df.columns:
            return None
        return {str(k): int(v) for k, v in df["所属行业"].value_counts().items()}
    except Exception:  # noqa: BLE001
        return None
```

`mcp_tools/review.py` 的 `get_zt_pool_tool` 替换为：

```python
@mcp.tool()
def get_zt_pool_tool(pool: str = "涨停", date: str = "", full: bool = False) -> str:
    """查询涨停生态股池明细（金额单位: 亿元）

    Args:
        pool: 涨停/炸板/跌停/强势/昨涨停，默认"涨停"
        date: 查询日期 YYYY-MM-DD，默认今天；无数据时自动回退最近交易日
        full: 默认 false 截断前 60 条；true 返回全量（链强度/炸板率/晋级率必须全量）
    """
    return get_zt_pool(pool, date, full)
```

- [ ] **Step 4: 跑测试确认通过（含既有用例回归）**

Run: `uv run pytest tests/test_review_market.py tests/test_review.py -v`
Expected: 全部 passed（含既有用例；若既有 zt_pool 用例因 mock 的 POOL_FETCHERS 与缓存交互挂掉，在 fixture `_clear_push2_cache` 内同时 `PUSH2_CACHE.clear()` 已覆盖——同一实例，无需额外处理）

- [ ] **Step 5: 格式化并提交**

```bash
uv run black utils/review_common.py utils/review_market.py mcp_tools/review.py tests/test_review_market.py
git add utils/review_common.py utils/review_market.py mcp_tools/review.py tests/test_review_market.py
git commit -m "feat: zt pool full mode with shared cache and industry counts"
```

---

### Task 3: `utils/board_quotes.py` —— R1/R2 板块行情

**Files:**
- Create: `utils/board_quotes.py`
- Test: `tests/test_board_quotes.py`

**Interfaces:**
- Consumes:
  - Task 1: `EmResult`、`em_push2_get`、`CACHE`、`now_iso`（`from .push2_client import ...`）
  - Task 2: `zt_industry_counts`（`from .review_market import zt_industry_counts`）、`review_common.json_ok/json_err/json_err_code/json_unavailable`、`tools.RateLimiter`
- Produces（Task 4 依赖）:
  - `get_board_quote(board_code: str = "", board_name: str = "") -> str`
  - `get_boards(scope: str = "行业", sort: str = "main_net", limit: int = 20, with_open: bool = True) -> str`
  - 模块级 `ulist_ok: bool | None`（None=未探测；False=ulist 不支持板块 secid 已退化 stock/get）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_board_quotes.py`：

```python
"""板块行情单测：全部 mock 上游（em_push2_get / zt_industry_counts），无网络。"""

import json

import pytest

from utils import board_quotes as bq
from utils.push2_client import EmResult


@pytest.fixture(autouse=True)
def _clean():
    bq.CACHE.clear()
    bq.ulist_ok = None
    yield
    bq.CACHE.clear()
    bq.ulist_ok = None


def _clist_payload(rows):
    """clist 响应体：rows = [{f12,f14,f2,f3,f17,f18,f62,f104,f105,f128,f140,f141}]"""
    return {"data": {"total": len(rows), "diff": rows}}


_ROW_YUANJIAN = {
    "f12": "BK0459", "f14": "元件", "f2": 51.83, "f3": 4.90,
    "f17": "-", "f18": 49.4854, "f62": 81.79e8,
    "f104": 12, "f105": 3, "f128": "东山精密", "f140": "002384", "f141": 10.0,
}
_ROW_YOUXI = {
    "f12": "BK1044", "f14": "游戏Ⅱ", "f2": 20.1, "f3": 1.2,
    "f17": "-", "f18": 19.9, "f62": -5e8,
    "f104": 5, "f105": 8, "f128": "某股", "f140": "002384", "f141": 5.0,
}


def _mock_upstream(monkeypatch, *, industry=None, concept=None, ulist=None, stock_get=None):
    calls = {"paths": []}

    def fake(path, params):
        calls["paths"].append(path)
        if path == "/api/qt/clist/get":
            fs = params.get("fs", "")
            if "t:2" in fs:
                return industry or EmResult(ok=False, reason="upstream_error")
            if "t:3" in fs:
                return concept or EmResult(ok=False, reason="upstream_error")
        if path == "/api/qt/ulist.np/get":
            return ulist or EmResult(ok=False, reason="upstream_error")
        if path == "/api/qt/stock/get":
            return stock_get or EmResult(ok=False, reason="upstream_error")
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(bq, "em_push2_get", fake)
    return calls


def test_board_quote_by_name_with_ulist(monkeypatch):
    _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload([_ROW_YUANJIAN]), as_of="2026-11-12T09:30:00+08:00"),
        concept=EmResult(ok=True, data=_clist_payload([]), as_of="2026-11-12T09:30:00+08:00"),
        ulist=EmResult(
            ok=True,
            data={"diff": [{"f12": "BK0459", "f46": 49.4056, "f60": 49.4854}]},
            as_of="2026-11-12T09:30:00+08:00",
        ),
    )
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": {"元件": 5})
    out = json.loads(bq.get_board_quote(board_name="元件"))
    assert out["success"]
    d = out["data"]
    assert d["board_code"] == "BK0459" and d["board_name"] == "元件"
    assert d["scope"] == "行业"
    assert d["open"] == 49.4056 and d["prev_close"] == 49.4854
    assert abs(d["open_pct"] - (-0.161)) < 0.01  # A1 口径：(f46-f60)/f60*100
    assert d["main_net_yi"] == 81.79 and d["up"] == 12 and d["down"] == 3
    assert d["leader_name"] == "东山精密" and d["limit_up"] == 5
    assert d["as_of"] and d["source"] == "eastmoney_push2_clist"


def test_board_quote_name_verbatim_passthrough(monkeypatch):
    """A3：board_name 原文透传（罗马数字 Ⅱ 原样，不做规范化）。"""
    _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload([_ROW_YOUXI]), as_of="t"),
        concept=EmResult(ok=True, data=_clist_payload([]), as_of="t"),
        ulist=EmResult(ok=True, data={"diff": [{"f12": "BK1044", "f46": None, "f60": 19.9}]}, as_of="t"),
    )
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": None)
    out = json.loads(bq.get_board_quote(board_name="游戏Ⅱ"))
    d = out["data"]
    assert d["board_name"] == "游戏Ⅱ" and d["open_pct"] is None
    assert d["status"] == "no_open" and "limit_up" not in d


def test_board_quote_not_found(monkeypatch):
    _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload([_ROW_YUANJIAN]), as_of="t"),
        concept=EmResult(ok=True, data=_clist_payload([]), as_of="t"),
    )
    out = json.loads(bq.get_board_quote(board_name="不存在的板块"))
    assert out["success"] is False and out["error_code"] == "BOARD_NOT_FOUND"


def test_board_quote_unavailable(monkeypatch):
    """A2：上游限流 → success:true + data:null + status:unavailable，不抛错。"""
    _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=False, reason="rate_limited"),
        concept=EmResult(ok=False, reason="rate_limited"),
    )
    out = json.loads(bq.get_board_quote(board_name="元件"))
    assert out["success"] is True and out["data"] is None
    assert out["status"] == "unavailable" and out["reason"] == "rate_limited"


def test_boards_sort_and_with_open_false(monkeypatch):
    calls = _mock_upstream(
        monkeypatch,
        industry=EmResult(
            ok=True,
            data=_clist_payload([_ROW_YOUXI, _ROW_YUANJIAN]),
            as_of="t",
        ),
    )
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": None)
    out = json.loads(bq.get_boards(scope="行业", sort="main_net", limit=10, with_open=False))
    assert out["success"]
    d = out["data"]
    assert d["total"] == 2 and len(d["boards"]) == 2
    assert d["boards"][0]["board_code"] == "BK0459"  # 主力净流入降序
    assert all(p == "/api/qt/clist/get" for p in calls["paths"])  # with_open=false 不打 ulist


def test_boards_open_from_clist_f17(monkeypatch):
    """clist 自带 f17/f18 时免 ulist 请求（开盘价零成本路径）。"""
    row = dict(_ROW_YUANJIAN, f17=50.0)
    calls = _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload([row]), as_of="t"),
    )
    out = json.loads(bq.get_boards(scope="行业", with_open=True))
    assert all(p == "/api/qt/clist/get" for p in calls["paths"])
    b = out["data"]["boards"][0]
    assert b["open"] == 50.0 and abs(b["open_pct"] - (50.0 - 49.4854) / 49.4854 * 100) < 0.01


def test_boards_open_fallback_stock_get(monkeypatch):
    """ulist 返回 ok 但 0 行 → 判定不支持板块 secid，退化逐板块 stock/get。"""
    calls = _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload([_ROW_YUANJIAN]), as_of="t"),
        ulist=EmResult(ok=True, data={"diff": []}, as_of="t"),
        stock_get=EmResult(
            ok=True, data={"f46": 49.4056, "f60": 49.4854}, as_of="t"
        ),
    )
    out = json.loads(bq.get_boards(scope="行业"))
    assert "/api/qt/stock/get" in calls["paths"] and bq.ulist_ok is False
    b = out["data"]["boards"][0]
    assert b["open"] == 49.4056


def test_boards_digest_when_no_args(monkeypatch):
    _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload([_ROW_YUANJIAN]), as_of="t"),
        concept=EmResult(ok=True, data=_clist_payload([_ROW_YOUXI]), as_of="t"),
    )
    out = json.loads(bq.get_board_quote())
    d = out["data"]
    assert d["total"] == 2 and {b["scope"] for b in d["boards"]} == {"行业", "概念"}


def test_limit_up_prefix_match(monkeypatch):
    """池内行业名 4 字截断（自动化设）与完整板块名（自动化设备）前缀匹配。"""
    _mock_upstream(
        monkeypatch,
        industry=EmResult(
            ok=True,
            data=_clist_payload(
                [dict(_ROW_YUANJIAN, f12="BK1036", f14="自动化设备")]
            ),
            as_of="t",
        ),
        concept=EmResult(ok=True, data=_clist_payload([]), as_of="t"),
        ulist=EmResult(ok=True, data={"diff": []}, as_of="t"),
        stock_get=EmResult(ok=True, data={"f46": 1.0, "f60": 1.0}, as_of="t"),
    )
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": {"自动化设": 3})
    out = json.loads(bq.get_board_quote(board_name="自动化设备"))
    assert out["data"]["limit_up"] == 3
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_board_quotes.py -v`
Expected: 全部 FAIL（`ModuleNotFoundError: No module named 'utils.board_quotes'`）

- [ ] **Step 3: 实现 `utils/board_quotes.py`**

```python
#!/usr/bin/env python3
"""板块行情：R1 单板块开盘涨跌幅 + R2 板块清单排行。

上游 = 东财 push2（经 utils.push2_client → akproxy）：
- clist/get m:90 板块列表（f17/f18 若板块支持则免额外开盘请求）；
- ulist.np/get 批量开盘价 f46/f60，不支持板块 secid 时自动退化逐板块 stock/get
  （stock/get 的 fields 必须全集，精简会"假失败"返回空 data——需求文档坑1）；
- 开盘涨跌幅 = (f46-f60)/f60*100；f46 缺失（未开盘/停牌）→ open_pct=null + status=no_open。

board_name 一律东财原文透传（含截断名/罗马数字），禁止规范化。
limit_up（板块内涨停家数）来自涨停池所属行业分桶（review_market.zt_industry_counts，
4 字截断名前缀匹配），算不出时省略字段。
"""

import logging

from .push2_client import CACHE, EmResult, em_push2_get, now_iso
from .review_common import (
    json_err,
    json_err_code,
    json_ok,
    json_unavailable,
    parse_day,
)
from .review_market import zt_industry_counts
from .tools import RateLimiter

logger = logging.getLogger(__name__)

BOARD_LIST_TTL = 60.0  # 板块清单缓存（秒）
BOARD_OPEN_TTL = 60.0  # 开盘价缓存（秒）
_ULIST_CHUNK = 100     # GET URL 长度安全分块

ulist_ok: bool | None = None  # None=未探测；False=ulist 不支持板块 secid，已退化 stock/get

_CLIST_FIELDS = "f12,f14,f2,f3,f17,f18,f62,f104,f105,f128,f140,f141"
_STOCK_GET_FIELDS = "f43,f44,f45,f46,f47,f48,f57,f58,f59,f60,f62,f104,f105,f107,f168,f169"
_SCOPE_FS = {"行业": "m:90+t:2", "概念": "m:90+t:3"}
_SOURCE = "eastmoney_push2_clist"


def _f(v) -> float | None:
    """fltt=2 字段安全转 float：'-' / None / '' → None。"""
    try:
        if v in (None, "-", ""):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _int_or_none(v) -> int | None:
    n = _f(v)
    return int(n) if n is not None else None


def _normalize_board_row(scope: str, raw: dict) -> dict:
    """clist 行 → 规范化行。_ 前缀键为内部缓存字段，输出前剥离。"""
    main = _f(raw.get("f62"))
    return {
        "_scope": scope,
        "board_code": str(raw.get("f12", "")),
        "board_name": str(raw.get("f14", "")),  # 原文透传
        "current_pct": _f(raw.get("f3")),
        "main_net_yi": round(main / 1e8, 2) if main is not None else None,
        "up": _int_or_none(raw.get("f104")),
        "down": _int_or_none(raw.get("f105")),
        "leader_name": str(raw.get("f128") or "") or None,
        "leader_code": str(raw.get("f140") or "") or None,
        "leader_pct": _f(raw.get("f141")),
        "_open": _f(raw.get("f17")),
        "_prev_close": _f(raw.get("f18")),
    }


def fetch_board_list(scope: str) -> EmResult:
    """板块清单快照（含主力资金/涨跌家数/领涨股；缓存 60s，stale 降级）。

    data = {"scope", "boards": [规范化行], "by_code", "by_name", "total"}
    """
    params = {
        "pn": 1, "pz": 600, "po": 1, "np": 1, "fltt": 2, "invt": 2,
        "fid": "f3", "fs": _SCOPE_FS[scope], "fields": _CLIST_FIELDS,
    }

    def _fetch() -> EmResult:
        res = em_push2_get("/api/qt/clist/get", params)
        if not res.ok:
            return res
        diff = (res.data or {}).get("diff") or []
        boards = [
            _normalize_board_row(scope, r)
            for r in diff
            if str(r.get("f12", "")).startswith("BK")
        ]
        snapshot = {
            "scope": scope,
            "boards": boards,
            "by_code": {b["board_code"]: b for b in boards},
            "by_name": {b["board_name"]: b for b in boards},
            "total": len(boards),
        }
        return EmResult(ok=True, data=snapshot, as_of=res.as_of)

    return CACHE.get(f"board_list:{scope}", BOARD_LIST_TTL, _fetch)


def _fetch_open_ulist(codes: list[str]) -> EmResult:
    """ulist 批量开盘价（分块多请求合并）。data = {code: {open, prev_close}}。"""
    merged, as_of = {}, ""
    for i in range(0, len(codes), _ULIST_CHUNK):
        chunk = codes[i : i + _ULIST_CHUNK]
        res = em_push2_get(
            "/api/qt/ulist.np/get",
            {
                "fltt": 2, "invt": 2,
                "secids": ",".join(f"90.{c}" for c in chunk),
                "fields": "f12,f46,f60",
            },
        )
        if not res.ok:
            return res
        as_of = as_of or res.as_of
        for r in (res.data or {}).get("diff") or []:
            code = str(r.get("f12", ""))
            open_, prev = _f(r.get("f46")), _f(r.get("f60"))
            if code and (open_ is not None or prev is not None):
                merged[code] = {"open": open_, "prev_close": prev}
    return EmResult(ok=True, data=merged, as_of=as_of)


def _fetch_open_stock_get(codes: list[str]) -> EmResult:
    """逐板块 stock/get 兜底（fields 全集，见模块 docstring）。"""
    merged, as_of = {}, ""
    for c in codes:
        res = em_push2_get(
            "/api/qt/stock/get",
            {"fltt": 2, "invt": 2, "fields": _STOCK_GET_FIELDS, "secid": f"90.{c}"},
        )
        if not res.ok:
            return res
        as_of = as_of or res.as_of
        d = res.data or {}
        merged[c] = {"open": _f(d.get("f46")), "prev_close": _f(d.get("f60"))}
    return EmResult(ok=True, data=merged, as_of=as_of)


def fetch_board_open(codes: list[str]) -> EmResult:
    """板块开盘价/昨收（缓存 60s；data = {code: {open, prev_close}}）。

    优先 ulist 批量；ulist ok 但一行都没有 → 判定不支持板块 secid，进程内退化
    stock/get（真·上游失败 rate_limited/upstream_error 原样返回，不切通道）。
    注：盘前 f46 可能整批为 '-'，此时退化后结果同样为空，行为收敛无副作用。
    """
    codes = sorted({c for c in codes if c})
    if not codes:
        return EmResult(ok=True, data={}, as_of=now_iso())

    def _fetch() -> EmResult:
        global ulist_ok
        if ulist_ok is not False:
            res = _fetch_open_ulist(codes)
            if res.ok:
                if res.data:
                    ulist_ok = True
                    return res
                ulist_ok = False  # ok 但 0 行 → 通道不支持
            else:
                return res
        return _fetch_open_stock_get(codes)

    return CACHE.get("board_open:" + ",".join(codes), BOARD_OPEN_TTL, _fetch)


def _limit_up_for(board_name: str, counts: dict | None) -> int | None:
    """池内行业名为 4 字截断原文：精确命中或板块名以截断名开头。"""
    if not counts:
        return None
    if board_name in counts:
        return counts[board_name]
    for hybk, n in counts.items():
        if board_name.startswith(hybk):
            return n
    return None


def _emit_row(row: dict, counts: dict | None) -> dict:
    out = {k: v for k, v in row.items() if not k.startswith("_")}
    limit_up = _limit_up_for(row["board_name"], counts)
    if limit_up is not None:
        out["limit_up"] = limit_up
    return out


def _open_pct(open_: float | None, prev: float | None) -> float | None:
    if open_ is None or not prev:
        return None
    return round((open_ - prev) / prev * 100, 3)


def _lookup(snaps: list[EmResult], code: str, name: str):
    """行业/概念快照里找板块行，返回 (row, scope)；未命中 (None, None)。"""
    if code and not code.startswith("BK"):
        code = f"BK{code}"
    for s in snaps:
        if not s.ok or not s.data:
            continue
        row = s.data["by_code"].get(code) if code else s.data["by_name"].get(name)
        if row is not None:
            return row, s.data["scope"]
    return None, None


@RateLimiter(max_calls=20, time_window=60)
def get_board_quote(board_code: str = "", board_name: str = "") -> str:
    """查询单个板块行情：开盘涨跌幅/主力净流入/涨跌家数/领涨股（R1）

    Args:
        board_code: 东财板块代码，如 BK0459（与 board_name 二选一）
        board_name: 东财板块名原文，如 元件 / 游戏Ⅱ；都不给则返回全部板块精简表
    """
    code, name = (board_code or "").strip(), (board_name or "").strip()
    try:
        snaps = [fetch_board_list(s) for s in ("行业", "概念")]
        if not any(s.ok for s in snaps):
            reason = next((s.reason for s in snaps if s.reason), "upstream_error")
            return json_unavailable(reason)

        if not code and not name:
            return _boards_digest(snaps)

        row, scope = _lookup(snaps, code, name)
        if row is None:
            return json_err_code("BOARD_NOT_FOUND", f"板块不存在: {code or name}")
        primary = next(s for s in snaps if s.ok and s.data["scope"] == scope)

        counts = zt_industry_counts()
        notes = []
        open_res = fetch_board_open([row["board_code"]])
        if open_res.ok:
            openq = (open_res.data or {}).get(row["board_code"]) or {}
        else:
            openq, notes = {}, [f"开盘数据不可用({open_res.reason})"]
        open_ = openq.get("open", row.get("_open"))
        prev = openq.get("prev_close", row.get("_prev_close"))

        out = _emit_row(row, counts)
        out.update(
            {
                "open": open_,
                "prev_close": prev,
                "open_pct": _open_pct(open_, prev),
                "as_of": primary.as_of,
                "stale": primary.stale or open_res.stale,
                "cached": primary.cached,
                "source": _SOURCE,
            }
        )
        if notes:
            out["notes"] = notes
        if open_ is None and not notes:
            out["status"] = "no_open"  # 正常但当日无开盘（未开盘/停牌）
        return json_ok(out)
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_board_quote: %s", e)
        return json_err(f"查询板块行情失败: {e}")


def _boards_digest(snaps: list[EmResult]) -> str:
    """board_code/board_name 都不给：全部行业+概念精简表（按当前涨跌幅降序）。"""
    rows, primary = [], None
    for s in snaps:
        if not s.ok or not s.data:
            continue
        if primary is None:
            primary = s
        for b in s.data["boards"]:
            rows.append(
                {
                    "scope": b["_scope"],
                    "board_code": b["board_code"],
                    "board_name": b["board_name"],
                    "current_pct": b["current_pct"],
                    "main_net_yi": b["main_net_yi"],
                }
            )
    rows.sort(
        key=lambda r: (r["current_pct"] is not None, r["current_pct"] or 0),
        reverse=True,
    )
    return json_ok(
        {
            "boards": rows,
            "total": len(rows),
            "as_of": primary.as_of if primary else now_iso(),
            "stale": any(s.stale for s in snaps if s.ok),
            "cached": all(s.cached for s in snaps if s.ok),
            "source": _SOURCE,
        }
    )


@RateLimiter(max_calls=20, time_window=60)
def get_boards(
    scope: str = "行业",
    sort: str = "main_net",
    limit: int = 20,
    with_open: bool = True,
) -> str:
    """查询板块清单排行（R2）：主力净流入/涨跌幅/开盘涨跌幅排序

    Args:
        scope: 行业（默认）/ 概念
        sort: main_net（默认）/ chg / open_pct
        limit: 返回条数 ≤50，默认 20
        with_open: 是否附带开盘涨跌幅（默认 true；false 省上游请求）
    """
    try:
        if scope not in _SCOPE_FS:
            return json_err(f"scope 仅支持 行业/概念，当前: {scope}")
        if sort not in ("main_net", "chg", "open_pct"):
            return json_err(f"sort 仅支持 main_net/chg/open_pct，当前: {sort}")
        limit = max(1, min(int(limit), 50))

        snap = fetch_board_list(scope)
        if not snap.ok:
            return json_unavailable(snap.reason or "upstream_error")
        boards = list(snap.data["boards"])
        notes = []

        if with_open:
            opens = {}
            if all(b["_open"] is not None for b in boards):
                pass  # clist f17/f18 已齐，免 ulist
            else:
                open_res = fetch_board_open([b["board_code"] for b in boards])
                if open_res.ok:
                    opens = open_res.data or {}
                else:
                    notes.append(f"开盘涨跌幅不可用({open_res.reason})，open_pct 为空")
            for b in boards:
                o = opens.get(b["board_code"]) or {}
                b["open"] = o.get("open", b["_open"])
                b["prev_close"] = o.get("prev_close", b["_prev_close"])
                b["open_pct"] = _open_pct(b["open"], b["prev_close"])
        else:
            for b in boards:
                b["open"] = b["_open"]
                b["prev_close"] = b["_prev_close"]
                b["open_pct"] = _open_pct(b["_open"], b["_prev_close"])

        sort_key = {
            "main_net": lambda b: (b["main_net_yi"] is not None, b["main_net_yi"] or 0),
            "chg": lambda b: (b["current_pct"] is not None, b["current_pct"] or 0),
            "open_pct": lambda b: (b["open_pct"] is not None, b["open_pct"] or 0),
        }[sort]
        boards.sort(key=sort_key, reverse=True)

        counts = zt_industry_counts()
        emit = [_emit_row(b, counts) for b in boards[:limit]]
        return json_ok(
            {
                "scope": scope,
                "sort": sort,
                "total": snap.data["total"],
                "boards": emit,
                "notes": notes,
                "as_of": snap.as_of,
                "stale": snap.stale,
                "cached": snap.cached,
                "source": _SOURCE,
            }
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_boards: %s", e)
        return json_err(f"查询板块清单失败: {e}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_board_quotes.py -v`
Expected: 9 passed

- [ ] **Step 5: 格式化并提交**

```bash
uv run black utils/board_quotes.py tests/test_board_quotes.py
uv run pytest tests/test_board_quotes.py -q
git add utils/board_quotes.py tests/test_board_quotes.py
git commit -m "feat: board quote/list tools core with open-price cascade"
```

---

### Task 4: 工具注册 + 导出 + 服务说明

**Files:**
- Create: `mcp_tools/board_quotes.py`
- Modify: `utils/__init__.py`、`mcp_tools/__init__.py`、`mcp_app.py`（SERVER_INSTRUCTIONS）
- Test: `tests/test_tool_registration.py`

**Interfaces:**
- Consumes: Task 3 的 `get_board_quote` / `get_boards`
- Produces: MCP 工具 `get_board_quote_tool(board_code, board_name)`、`get_boards_tool(scope, sort, limit, with_open)`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_tool_registration.py`：

```python
"""工具注册冒烟：新板块工具已挂到 FastMCP 实例，且 utils 导出可用。"""

import asyncio


def test_new_board_tools_registered():
    from mcp_app import mcp
    import mcp_tools  # noqa: F401  导入即注册

    tools = asyncio.run(mcp.get_tools())
    names = set(tools)
    assert "get_board_quote_tool" in names
    assert "get_boards_tool" in names
    assert "get_zt_pool_tool" in names


def test_utils_exports():
    from utils import get_board_quote, get_boards, get_zt_pool  # noqa: F401
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_tool_registration.py -v`
Expected: FAIL（`ImportError: cannot import name 'get_board_quote'`；注册断言也失败）

- [ ] **Step 3: 实现**

创建 `mcp_tools/board_quotes.py`：

```python
"""板块行情类工具：单板块开盘涨跌幅 / 板块清单排行。"""

from mcp_app import mcp
from utils import get_board_quote, get_boards


@mcp.tool()
def get_board_quote_tool(board_code: str = "", board_name: str = "") -> str:
    """查询单个板块行情：开盘涨跌幅/主力净流入/涨跌家数/领涨股（竞价核心）

    Args:
        board_code: 东财板块代码，如 BK0459（与 board_name 二选一）
        board_name: 东财板块名原文，如 元件 / 游戏Ⅱ；都不给则返回全部板块精简表
    """
    return get_board_quote(board_code, board_name)


@mcp.tool()
def get_boards_tool(
    scope: str = "行业",
    sort: str = "main_net",
    limit: int = 20,
    with_open: bool = True,
) -> str:
    """查询板块清单排行：主力净流入/涨跌幅/开盘涨跌幅排序

    Args:
        scope: 行业（默认）/ 概念
        sort: main_net（默认）/ chg / open_pct
        limit: 返回条数 ≤50，默认 20
        with_open: 附带开盘涨跌幅（默认 true）
    """
    return get_boards(scope, sort, limit, with_open)
```

`utils/__init__.py`：import 区加一行、`__all__` 加两项：

```python
from .board_quotes import get_board_quote, get_boards
```

`__all__` 列表内追加 `"get_board_quote", "get_boards"`。

`mcp_tools/__init__.py` 的导入元组按字母序加入 `board_quotes`：

```python
from . import (  # noqa: F401
    board_quotes,
    derivatives,
    funds,
    global_markets,
    lhb,
    meta,
    prompts,
    review,
    stock,
)
```

`mcp_app.py` 的 `SERVER_INSTRUCTIONS`：把【复盘】小节的这行

```
- get_zt_pool_tool(pool, date)：涨停生态五池（涨停/炸板/跌停/强势/昨涨停），支持历史日期；涨停/炸板池含首次封板时间/最后封板时间（HHMMSS），"最早上板"按首次封板时间排序
```

替换为

```
- get_zt_pool_tool(pool, date, full)：涨停生态五池（涨停/炸板/跌停/强势/昨涨停），支持历史日期；涨停/炸板池含首次封板时间/最后封板时间（HHMMSS），"最早上板"按首次封板时间排序；full=true 返回全量池（链强度/炸板率/晋级率须全量，默认截断60条）
```

并在【复盘】小节末尾（`get_market_breadth_tool` 行之后）插入：

```
【板块】
- get_board_quote_tool(board_code|board_name)：单板块开盘涨跌幅/主力净流入/涨跌家数/领涨股/涨停家数；board_name 必须东财原文（如 游戏Ⅱ）；查不到=BOARD_NOT_FOUND（配置错），限流=status:unavailable（缺数据≠条件不达标，标待人工判），未开盘=no_open
- get_boards_tool(scope, sort, limit, with_open)：行业/概念板块排行（main_net/chg/open_pct 排序）
```

- [ ] **Step 4: 跑测试确认通过（全量回归）**

Run: `uv run pytest -v`
Expected: 全部 passed（live 标记默认跳过）

- [ ] **Step 5: 格式化并提交**

```bash
uv run black mcp_tools/board_quotes.py utils/__init__.py mcp_tools/__init__.py mcp_app.py tests/test_tool_registration.py
git add mcp_tools/board_quotes.py utils/__init__.py mcp_tools/__init__.py mcp_app.py tests/test_tool_registration.py
git commit -m "feat: register board quote/list MCP tools with server instructions"
```

---

### Task 5: 真实接口探测 + live 冒烟 + 验收

**Files:**
- Create: `scripts/probe_em_board_fields.py`（一次性探测脚本，不入 CI）
- Create: `tests/test_live_board_smoke.py`（`@pytest.mark.live`）
- Modify: `docs/superpowers/specs/2026-11-12-em-rate-limit-and-boards-design.md`（回填验收实测结果）

**Interfaces:**
- Consumes: Task 3/4 的 `get_board_quote` / `get_boards` / `get_zt_pool`
- Produces: 设计文档 §5 三个"实现期验证点"的实测结论 + A1~A7 验收记录

- [ ] **Step 1: 写探测脚本（验证点 ①②③）**

创建 `scripts/probe_em_board_fields.py`：

```python
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
```

- [ ] **Step 2: 本地跑探测（需 AKPROXY_TOKEN；空响应=触发限流，等 30s 重试）**

Run: `uv run python scripts/probe_em_board_fields.py`
Expected: 三段原始 JSON 输出。判定规则：
- clist 首行含 f17 且值非 `"-"` → 开盘价主通道=clist（board_quotes 已自动优先，无需改码）
- clist 缺 f17 但 ulist 的 diff 有行 → 主通道=ulist（board_quotes 默认路径 ✓）
- 两者都不行 → stock/get 兜底（board_quotes 自动退化 ✓）
把结论记到设计文档 §5"实现期实测验证"下方（三行内）。

- [ ] **Step 3: 写 live 冒烟测试**

创建 `tests/test_live_board_smoke.py`：

```python
"""板块工具真实接口冒烟（-m live 显式运行，需 AKPROXY_TOKEN；交易日盘中跑最有意义）。"""

import json

import pytest

from utils import get_board_quote, get_board_quote as _gbq, get_boards, get_zt_pool


@pytest.mark.live
def test_board_quote_live():
    out = json.loads(get_board_quote(board_name="元件"))
    assert out["success"]
    if not out["data"]:  # 限流时 data=null + status=unavailable，属预期形态
        pytest.skip(f"上游不可用: {out.get('status')}/{out.get('reason')}")
    d = out["data"]
    assert d["board_code"] == "BK0459" and d["board_name"] == "元件"
    assert "open_pct" in d and "as_of" in d and "source" in d


@pytest.mark.live
def test_boards_live_verbatim_names():
    """A3：概念/行业清单返回原文（存在特殊字符名即透传成功）。"""
    out = json.loads(get_boards(scope="行业", sort="main_net", limit=50))
    assert out["success"]
    if not out["data"]:
        pytest.skip(f"上游不可用: {out.get('status')}/{out.get('reason')}")
    assert out["data"]["total"] > 50
    names = [b["board_name"] for b in out["data"]["boards"]]
    assert all(n for n in names)  # 无空名


@pytest.mark.live
def test_zt_pool_full_live():
    """A4：full=true 时 items 长度 == count（不截断）。"""
    out = json.loads(get_zt_pool("涨停", full=True))
    assert out["success"]
    if not out["data"]:
        pytest.skip(f"上游不可用: {out.get('error')}")
    assert len(out["data"]["items"]) == out["data"]["count"]


@pytest.mark.live
def test_rapid_calls_never_throw():
    """A2：连续高频 20 次不抛错（限流时返回 unavailable 信封也算通过）。"""
    for _ in range(20):
        payload = json.loads(get_board_quote(board_code="BK0459"))
        assert "success" in payload
```

- [ ] **Step 4: 跑 live 冒烟**

Run: `uv run pytest tests/test_live_board_smoke.py -m live -v`
Expected: 4 passed（非交易日时 `test_zt_pool_full_live` 会因回退到最近交易日仍应通过；若上游限流导致 unavailable 信封，board_quote 类用例设计为容忍，重跑一次确认）

- [ ] **Step 5: 验收结论回填设计文档并提交**

把 A1~A7 实测结果（每条一行 ✓/✗+关键数字，如 count=85）追加到设计文档 §5 末尾"验收实测"小节。

```bash
git add scripts/probe_em_board_fields.py tests/test_live_board_smoke.py docs/superpowers/specs/2026-11-12-em-rate-limit-and-boards-design.md
git commit -m "test: live smoke and field probes for board tools, record acceptance"
```

- [ ] **Step 6: 构建镜像并部署（按项目既有流程）**

```bash
docker build -t docker.sihanfu.cn:15999/mcp-stock-server:latest .
docker push docker.sihanfu.cn:15999/mcp-stock-server:latest
```

远程主机（192.168.3.10）上按既有方式重启（项目根有 docker-compose.yml）：
`docker compose pull && docker compose up -d`。

- [ ] **Step 7: 部署后验证（新工具在服务端可见）**

Run（本地）:

```bash
uv run python -c "
import asyncio
from fastmcp import Client

async def main():
    async with Client('http://192.168.3.10:18881/mcp') as c:
        tools = await c.list_tools()
        names = [t.name for t in tools]
        assert 'get_board_quote_tool' in names and 'get_boards_tool' in names, names
        r = await c.call_tool('get_board_quote_tool', {'board_name': '元件'})
        print(r.content[0].text[:300])

asyncio.run(main())
"
```

Expected: 打印 `元件` 板块行情 JSON 片段（含 `open_pct`/`as_of`/`source`）。若返回 `status: unavailable` 说明上游限流中，等 30 秒重跑一次确认能恢复。

---

## Self-Review 记录

- **Spec 覆盖**：R1→Task 3/4；R2→Task 3/4；R3→Task 2；R4 服务端契约→Task 1/2/3（三态信封 + as_of/stale/cached）；R5→Task 1（Push2Cache single-flight/失败不缓存/缓存可见性）+ Task 2/3 TTL；非目标未引入成分股枚举/实盘接口。✓
- **占位符扫描**：无 TBD/TODO；所有代码步骤含完整代码。✓
- **类型一致性**：`EmResult` 字段、`_pool_with_fallback` 六元组返回、`zt_industry_counts(date="")`、`fetch_board_open` 的 data 形态在 Task 2/3 间一致；`get_zt_pool` 三参签名与 `mcp_tools/review.py` 透传一致。✓
