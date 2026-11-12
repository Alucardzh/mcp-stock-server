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
    # 首个 host 空响应（限流形态）、第二个 host 成功 → 第二次请求前 _pace 等满间隔
    _mock_get(monkeypatch, [{"data": None}, {"data": {"a": 1}}], c)
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
