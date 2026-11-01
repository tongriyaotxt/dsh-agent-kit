"""dsh_agent_kit — 零依赖 DSH 接入层。

    from dsh_agent_kit import DshClient

    c = DshClient(cwd="/proj")
    for ev in c.stream("解释这个仓库"):
        if ev["type"] == "text":
            print(ev["text"], end="", flush=True)

    t = c.ask("列出三个候选方案")
    print(t.text, t.usage, t.session_id)
"""

from .client import (
    DEFAULT_PROFILE,
    KIND_EXIT,
    KIND_SPAWN,
    KIND_TIMEOUT,
    DshClient,
    DshError,
    Turn,
    build_argv,
    build_env,
    resolve_launcher,
)

__version__ = "1.0.0"
__all__ = [
    "DshClient",
    "DshError",
    "Turn",
    "DEFAULT_PROFILE",
    "KIND_TIMEOUT",
    "KIND_EXIT",
    "KIND_SPAWN",
    "build_argv",
    "build_env",
    "resolve_launcher",
    "__version__",
]
