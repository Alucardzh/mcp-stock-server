"""install_patch 双调用点回归：后装的 validate.py 不得丢掉 tools.py 的域名扩展。

tools.py 先装（hook_domains 含 datacenter-web），validate.py 后装——补丁模块每次
install_patch 都重建 Session 类并整体替换 requests.Session（后装者生效），因此
后装列表必须包含先装列表的全部扩展域名，否则 datacenter-web 运行时未被接管。
"""
import inspect

import requests

from utils.tools import akshare_proxy_patch  # noqa: F401  # 触发两条 install_patch 链


def _effective_hook_domains():
    """读取当前生效的 PatchedSession.request 闭包捕获的 hook_domains。"""
    request_fn = getattr(requests.Session, "request", None)
    if request_fn is None:
        return None
    try:
        nonlocals = inspect.getclosurevars(request_fn).nonlocals
    except (TypeError, ValueError):
        return None
    return nonlocals.get("hook_domains")


def test_effective_hook_domains_include_datacenter_web():
    import utils  # noqa: F401  # 确保两条 install_patch 链都已执行
    import utils.validate

    eff = getattr(akshare_proxy_patch, "_HOOK_DOMAINS", None)
    if eff is None:
        eff = _effective_hook_domains()
    if eff is None:  # 补丁模块无公开属性且闭包不可读时，检查后装调用点源码
        assert "datacenter-web.eastmoney.com" in inspect.getsource(utils.validate)
    else:
        assert any("datacenter-web" in str(d) for d in eff), (
            f"生效 hook_domains 缺少 datacenter-web: {eff}"
        )
