# -*- coding: utf-8 -*-
"""原生模式下的 Redis 替身 —— 让 api 不需要 Redis 服务器也能跑。

为什么要这个
------------
Redis 官方从来没有 Windows 版。Docker 模式下这不是问题（容器里跑 Linux），
但原生模式没有容器，就没地方跑 Redis 服务端。

为什么不改业务代码
------------------
api 里有 12 处独立创建 Redis 客户端，分布在 routers 和 services 下，
但它们**全部**走 `redis.from_url()` 或 `redis.Redis.from_url()`。
所以只要在解释器启动时把这两个入口换掉，业务代码一行都不用动 ——
Docker 模式下这个文件不会被加载，走的还是真 Redis。

Python 启动时会自动 import 名为 sitecustomize 的模块（只要它在 sys.path 上），
启动器把本目录加进 PYTHONPATH，于是这段在任何 import redis 之前就生效了。

只在 HUNTER_NATIVE_MODE=1 时接管，避免误伤 Docker / 生产环境。
"""
import os
import sys

if os.getenv("HUNTER_NATIVE_MODE") == "1":
    try:
        import fakeredis
        import redis as _redis

        # 所有客户端共用同一个内存实例 —— 否则各 router 各存各的，
        # A 写进去的 key B 读不到，行为跟真 Redis 不一致。
        _SHARED = fakeredis.FakeServer()

        class _NativeRedis(fakeredis.FakeRedis):
            """接住业务代码传的各种连接参数，落到同一个共享内存库。"""

            def __init__(self, *args, **kwargs):
                # 这些是网络层参数，内存实现用不上，静默丢弃
                for k in ("host", "port", "db", "socket_timeout",
                          "socket_connect_timeout", "password", "username",
                          "ssl", "connection_pool", "retry_on_timeout",
                          "health_check_interval", "max_connections"):
                    kwargs.pop(k, None)
                kwargs.setdefault("server", _SHARED)
                super().__init__(*args, **kwargs)

            @classmethod
            def from_url(cls, url, **kwargs):          # noqa: ARG003 - url 忽略
                return cls(**kwargs)

        def _from_url(url, **kwargs):                  # noqa: ARG001 - url 忽略
            return _NativeRedis(**kwargs)

        _redis.Redis = _NativeRedis
        _redis.StrictRedis = _NativeRedis
        _redis.from_url = _from_url

        print("[native] Redis -> 进程内内存实现 (fakeredis)", file=sys.stderr)

    except ImportError as exc:
        print(f"[native] 警告: fakeredis 未安装，Redis 调用会失败: {exc}",
              file=sys.stderr)
