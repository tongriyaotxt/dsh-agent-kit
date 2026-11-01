"""DSH headless 客户端：把 `dsh --profile <p> --json -` 的 JSONL 事件流包成 Python API。

设计约束（每条都对应一个实测过的坑，见 docs/06-troubleshooting.md）：

1. 任务走 **stdin** —— 拼进命令行会被 shell 吃掉中文/引号/反斜杠/换行；
2. Windows 上 ``dsh`` 是 ``dsh.cmd``，直接 Popen 会 FileNotFoundError —— 解析真实路径 + ``cmd /c``；
3. stderr 必须分流 —— 否则诊断污染 JSONL；
4. 超时自己算 —— 不依赖 dsh 内部超时，到点 kill；
5. 子进程不能继承 ``DSH_SESSION_ID``/``DSH_PROFILE``/``DSH_PROFILE_DIR`` —— 否则嵌套调用会串进父会话。
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Sequence

DEFAULT_PROFILE = "headless"

#: 会话级环境变量：子进程一律不继承
SCRUB_ENV = ("DSH_SESSION_ID", "DSH_PROFILE", "DSH_PROFILE_DIR")

KIND_SPAWN = "spawn"
KIND_TIMEOUT = "timeout"
KIND_EXIT = "exit"

_UNSET = object()


class DshError(RuntimeError):
    """dsh 启动失败 / 超时 / 非 0 退出。"""

    def __init__(
        self,
        message: str,
        kind: str = KIND_EXIT,
        exit_code: Optional[int] = None,
        stderr: str = "",
        events: Optional[List[dict]] = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.exit_code = exit_code
        self.stderr = stderr
        self.events = events or []


# --------------------------------------------------------------------------- #
# 命令与环境的拼装（纯函数，便于单测）
# --------------------------------------------------------------------------- #
def resolve_launcher(dsh: str = "dsh") -> List[str]:
    """把 ``dsh`` 解析成可直接交给 Popen 的前缀命令。

    Windows 上 npm 生成的是 ``dsh.cmd``：``shutil.which`` 能找到它，但直接
    Popen 一个 ``.cmd`` 会 FileNotFoundError，必须 ``cmd /c`` 包装。
    """
    exe = shutil.which(dsh) or dsh
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", exe]
    return [exe]


def build_env(
    base: Optional[Dict[str, str]] = None,
    extra: Optional[Dict[str, str]] = None,
    drop_session_env: bool = True,
) -> Dict[str, str]:
    """构造子进程环境：UTF-8 输出 + 摘除会话级 DSH 变量。"""
    env = dict(os.environ if base is None else base)
    if drop_session_env:
        for key in SCRUB_ENV:
            env.pop(key, None)
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra or {})
    return env


def build_argv(
    profile: str = DEFAULT_PROFILE,
    launcher: Optional[Sequence[str]] = None,
    patches: Optional[Sequence[str]] = None,
    session_id: Optional[str] = None,
    task: Optional[str] = None,
    extra_launcher: Optional[Sequence[str]] = None,
    extra_app: Optional[Sequence[str]] = None,
) -> List[str]:
    """拼 argv。

    顺序规则来自 ``dsh [--profile] <name> [launcher-options] [app-args...]``：

        dsh --patch <p> --profile <name> [launcher...] --json [--session-id <id>] (task | -)

    启动器 flag 写在 app 参数之后会得到 ``unknown option '--patch'``。
    """
    argv: List[str] = list(launcher if launcher is not None else resolve_launcher())
    for p in patches or ():
        argv += ["--patch", str(p)]
    argv += ["--profile", profile]
    argv += list(extra_launcher or ())
    argv.append("--json")
    if session_id:
        argv += ["--session-id", session_id]
    argv += list(extra_app or ())
    argv.append("-" if task is None else task)
    return argv


# --------------------------------------------------------------------------- #
# 结果对象
# --------------------------------------------------------------------------- #
@dataclass
class Turn:
    """一轮运行的结果。既支持属性访问，也支持 ``r["text"]`` 式访问。"""

    text: str = ""
    session_id: Optional[str] = None
    usage: Optional[dict] = None
    events: List[dict] = field(default_factory=list)
    exit_code: Optional[int] = None
    stderr: str = ""
    duration: float = 0.0

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError as exc:  # pragma: no cover - 明确报错更好
            raise KeyError(key) from exc

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "session_id": self.session_id,
            "usage": self.usage,
            "exit_code": self.exit_code,
            "stderr": self.stderr,
            "duration": self.duration,
            "events": self.events,
        }

    def events_of(self, type_: str) -> List[dict]:
        return [e for e in self.events if e.get("type") == type_]


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #
class DshClient:
    """一次任务一个进程；会话靠 ``--session-id`` 续接。

    参数
    ----
    dsh            : 可执行名或绝对路径
    profile        : profile 名（默认 headless）
    cwd            : 子进程工作目录
    patches        : ``--patch`` 列表（可重复，按顺序叠加）
    env            : 追加的环境变量
    timeout        : 默认超时秒数
    strict         : True 时超时/非 0 退出抛 DshError；False 时产出 error 事件
    track_session  : True 时自动记住并复用 session_id
    extra_launcher : 其它启动器 flag（放在 app 参数之前）
    extra_app      : 其它 app 参数
    """

    def __init__(
        self,
        dsh: str = "dsh",
        profile: str = DEFAULT_PROFILE,
        cwd: Optional[str] = None,
        patches: Optional[Sequence[str]] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: float = 600.0,
        strict: bool = True,
        track_session: bool = True,
        drop_session_env: bool = True,
        extra_launcher: Optional[Sequence[str]] = None,
        extra_app: Optional[Sequence[str]] = None,
        launcher: Optional[Sequence[str]] = None,
    ) -> None:
        #: ``launcher`` 直接给出完整前缀（如 ``[sys.executable, "fake_dsh.py"]``），
        #: 用于包装器、测试替身、或 ``node cli.js`` 这类非 dsh 可执行。
        self.launcher = list(launcher) if launcher is not None else resolve_launcher(dsh)
        self.dsh = dsh
        self.profile = profile
        self.cwd = cwd or os.getcwd()
        self.patches = list(patches or ())
        self.timeout = float(timeout)
        self.strict = strict
        self.track_session = track_session
        self.extra_launcher = list(extra_launcher or ())
        self.extra_app = list(extra_app or ())
        self._env_extra = dict(env or {})
        self._drop_session_env = drop_session_env
        self._session_id: Optional[str] = None

    @property
    def env(self) -> Dict[str, str]:
        """子进程环境：在**启动那一刻**从当前 ``os.environ`` 重建。

        不在构造时快照，是为了让调用方能在两次任务之间改变环境
        （例如切换代理、临时打开一个故障注入开关）。
        """
        return build_env(extra=self._env_extra, drop_session_env=self._drop_session_env)

    # ---------------- 会话 ---------------- #
    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    def reset(self) -> None:
        """忘掉当前会话，下一轮开新会话。"""
        self._session_id = None

    # ---------------- argv ---------------- #
    def argv(
        self,
        session_id: Optional[str] = None,
        extra_app: Optional[Sequence[str]] = None,
    ) -> List[str]:
        return build_argv(
            profile=self.profile,
            launcher=self.launcher,
            patches=self.patches,
            session_id=session_id,
            task=None,  # 永远走 stdin
            extra_launcher=self.extra_launcher,
            extra_app=list(self.extra_app) + list(extra_app or ()),
        )

    # ---------------- 核心 ---------------- #
    def stream(
        self,
        task: str,
        session_id: Any = _UNSET,
        timeout: Optional[float] = None,
        extra_app: Optional[Sequence[str]] = None,
    ) -> Iterator[dict]:
        """逐条产出事件字典。

        ``session_id`` 省略时用记住的会话；显式传 ``None`` 表示强制开新会话。
        """
        sid = self._resolve_session(session_id)
        limit = self.timeout if timeout is None else float(timeout)
        argv = self.argv(session_id=sid, extra_app=extra_app)

        try:
            proc = subprocess.Popen(
                argv,
                cwd=self.cwd,
                env=self.env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except OSError as exc:
            raise DshError(
                "无法启动 dsh (%s): %s" % (" ".join(argv[:2]), exc), kind=KIND_SPAWN
            ) from exc

        collected: List[dict] = []
        stderr_buf: List[str] = []
        reader = threading.Thread(
            target=lambda: stderr_buf.append(
                proc.stderr.read().decode("utf-8", "replace")
            ),
            daemon=True,
        )
        reader.start()

        # 任务走 stdin：写完立刻 close（=EOF），否则 dsh 会一直等输入
        try:
            assert proc.stdin is not None
            proc.stdin.write(task.encode("utf-8"))
            proc.stdin.close()
        except (OSError, ValueError) as exc:
            self._kill(proc)
            raise DshError("写入任务失败: %s" % exc, kind=KIND_SPAWN) from exc

        deadline = time.monotonic() + limit
        # 读 stdout 必须放在独立线程里：readline() 阻塞时主循环拿不到控制权，
        # 超时就会失效（子进程挂住 = 你的客户端一起挂住）。
        lines: "queue.Queue[tuple]" = queue.Queue()
        pump = threading.Thread(target=self._pump, args=(proc.stdout, lines), daemon=True)
        pump.start()

        timed_out = False
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    self._kill(proc)
                    break
                try:
                    kind, payload = lines.get(timeout=remaining)
                except queue.Empty:
                    timed_out = True
                    self._kill(proc)
                    break
                if kind == "eof":
                    break
                line = payload.decode("utf-8", "replace").strip()
                if not line:
                    continue
                ev = self._parse(line)
                collected.append(ev)
                if ev.get("type") == "session" and ev.get("sessionId"):
                    if self.track_session:
                        self._session_id = ev["sessionId"]
                yield ev
        finally:
            try:
                proc.wait(timeout=10)
            except Exception:  # noqa: BLE001 - 兜底：不能让收尾把异常盖掉
                self._kill(proc)
            reader.join(timeout=2)
            pump.join(timeout=2)

        err = "".join(stderr_buf).strip()

        if timed_out:
            if self.strict:
                raise DshError(
                    "超时 %.0fs" % limit,
                    kind=KIND_TIMEOUT,
                    stderr=err,
                    events=collected,
                )
            yield {"type": "error", "message": "timeout %.0fs" % limit,
                   "kind": KIND_TIMEOUT}
            return

        if proc.returncode not in (0, None):
            if self.strict:
                raise DshError(
                    "dsh 退出码 %s: %s" % (proc.returncode, err[-800:]),
                    kind=KIND_EXIT,
                    exit_code=proc.returncode,
                    stderr=err,
                    events=collected,
                )
            yield {
                "type": "error",
                "message": "exit %s: %s" % (proc.returncode, err[-800:]),
                "kind": KIND_EXIT,
                "exit_code": proc.returncode,
            }

    def ask(
        self,
        task: str,
        session_id: Any = _UNSET,
        timeout: Optional[float] = None,
        extra_app: Optional[Sequence[str]] = None,
    ) -> Turn:
        """跑完一轮，返回 :class:`Turn`。"""
        started = time.monotonic()
        events: List[dict] = []
        text_parts: List[str] = []
        final_text: Optional[str] = None
        sid: Optional[str] = None
        usage: Optional[dict] = None
        error: Optional[dict] = None

        try:
            for ev in self.stream(task, session_id=session_id, timeout=timeout,
                                  extra_app=extra_app):
                events.append(ev)
                kind = ev.get("type")
                if kind == "session":
                    sid = ev.get("sessionId") or sid
                elif kind == "text":
                    text_parts.append(ev.get("text") or "")
                elif kind == "final":
                    final_text = ev.get("text") or ""
                elif kind == "status":
                    if ev.get("usage"):
                        usage = ev["usage"]
                elif kind == "error":
                    error = ev
        except DshError as exc:
            exc.events = exc.events or events
            raise

        if sid is None:
            sid = self._session_id

        return Turn(
            text=final_text if final_text is not None else "".join(text_parts),
            session_id=sid,
            usage=usage,
            events=events,
            exit_code=None if error else 0,
            stderr=error.get("message", "") if error else "",
            duration=time.monotonic() - started,
        )

    # ---------------- 内部 ---------------- #
    def _resolve_session(self, session_id: Any) -> Optional[str]:
        if session_id is _UNSET:
            return self._session_id if self.track_session else None
        return session_id  # 可能是 None（强制新会话）

    @staticmethod
    def _parse(line: str) -> dict:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return {"type": "_raw", "text": line}
        if not isinstance(obj, dict):
            return {"type": "_raw", "text": line}
        return obj

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            if os.name == "nt":
                # Windows 上 kill() 只杀直接子进程，进程树要交给 taskkill
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    capture_output=True,
                    check=False,
                )
            else:
                proc.kill()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _pump(stream: Any, out: "queue.Queue[tuple]") -> None:
        """把 stdout 行推进队列；EOF 推一个哨兵。跑在独立线程里，让超时可控。"""
        try:
            for raw in iter(stream.readline, b""):
                out.put(("line", raw))
        except Exception:  # noqa: BLE001 - 进程被杀时 read 可能抛错
            pass
        finally:
            out.put(("eof", None))

    @staticmethod
    def _drain(reader: threading.Thread, buf: List[str]) -> str:
        reader.join(timeout=2)
        return "".join(buf).strip()
