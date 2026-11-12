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
EMPTY_MAX_ROUNDS = 2  # 最多两轮，不无限重试


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
        self._entries: dict = {}  # key -> _CacheEntry
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
                return EmResult(ok=True, data=entry.data, as_of=entry.as_of, stale=True)
            return res

    def clear(self):
        with self._guard:
            self._entries.clear()
            self._key_locks.clear()


CACHE = Push2Cache()  # 进程级共享缓存


def em_push2_cached(path: str, params: dict, cache_key: str, ttl: float) -> EmResult:
    """带缓存的 push2 GET（缓存/stale 语义见 Push2Cache.get）。"""
    return CACHE.get(cache_key, ttl, lambda: em_push2_get(path, params))
