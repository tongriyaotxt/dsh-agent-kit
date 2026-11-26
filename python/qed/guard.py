#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Profile 守卫 —— 检测「AI 改自己的 profile 来提权」（docs/09-issues.md D4）。

问题：
    profile 目录（`~/.dsh/profiles/<name>/`）决定这个 app 拿到什么能力：
    装哪些插件、能不能上网、沙箱档位。而 agent 有 `fs` 和 `bash`，
    它可以把 `configs/lockdown.patch.yml` 那几条 disabled 删掉，下一轮就多出工具。

做法（两个动作，一个诚实的边界）：

    1. **指纹**：对「能力声明文件」算 sha256（package.json / cordis.yml /
       cordis.patch.yml / compatibility.json / pnpm-workspace.yaml）。
    2. **只读**：把 profile 目录里的文件设为只读，抬高改动成本。
    3. **比对**：spawn 前后各算一次，变了就报 `tampered`，宿主据此拒绝下一轮。

**边界（务必读）**：
    工具进程与守卫同属一个 OS 用户，`attrib +R` 挡不住它 —— 它自己能 `chmod +w`。
    所以这层是**检测**，不是**阻止**。真阻止要 L2（受限令牌 / AppContainer / 容器）。
    另外默认只哈希声明文件，防不了「改 node_modules 里的插件实现」这种依赖投毒；
    需要时用 `full=True`（慢，含 node_modules）。

用法：
    from qed.guard import ProfileGuard

    with ProfileGuard(profile_dir) as guard:
        run_dsh_once()                  # 这一轮里 agent 可能试图改 profile
    if guard.tampered:
        raise RuntimeError("profile 被改动：%r" % guard.changes)
"""

from __future__ import annotations

import hashlib
import os
import stat
from typing import Dict, List, Optional

#: 决定「这个 profile 能干什么」的文件。其余一律不算（备份、日志、node_modules）。
DECLARATION_FILES = (
    "package.json",
    "cordis.yml",
    "cordis.patch.yml",
    "compatibility.json",
    "pnpm-workspace.yaml",
)

#: **真正设只读的那几个**。
#:
#: 实测（2026-11-26）：把 `cordis.yml` 也设成只读，dsh 启动直接崩 ——
#: `EPERM: open '.../profiles/headless/cordis.yml'`，它在启动时会重写这棵树。
#: 所以只读清单只留「用户层」：bundle 声明与覆盖层。`cordis.yml` 只做指纹检测。
READONLY_FILES = ("package.json", "cordis.patch.yml")

#: 完全跳过遍历的目录
SKIP_DIRS = {"node_modules", ".git", ".plugin-manager", "__pycache__"}

#: 跳过前缀/后缀（工具留下的备份与临时文件）
SKIP_PREFIX = (".rollback-",)
SKIP_SUFFIX = (".pyc", ".log", ".tmp")

#: 目录名前缀也跳过 —— 否则 `.rollback-<ts>/cordis.patch.yml` 会被当成能力声明，
#: 一有回滚就误报「profile 被改」（测试 test_fingerprint_ignores_noise 抓到）
SKIP_DIR_PREFIX = (".rollback-",)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_files(profile_dir: str, full: bool):
    for root, dirs, files in os.walk(profile_dir):
        dirs[:] = sorted(
            d for d in dirs
            if d not in SKIP_DIRS and not d.startswith(SKIP_DIR_PREFIX)
        )
        for name in sorted(files):
            if not full and name not in DECLARATION_FILES:
                continue
            if name.startswith(SKIP_PREFIX) or name.endswith(SKIP_SUFFIX):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, profile_dir).replace(os.sep, "/")
            yield rel, path


def fingerprint(profile_dir: str, full: bool = False) -> Dict:
    """对能力声明文件算指纹。

    返回 ``{"digest": <总摘要>, "files": {rel: sha256}}``。
    目录不存在时返回空指纹（不抛异常：新 profile 还没建是正常状态）。
    """
    files: Dict[str, str] = {}
    profile_dir = os.path.abspath(os.path.expanduser(profile_dir))
    if os.path.isdir(profile_dir):
        for rel, path in _iter_files(profile_dir, full):
            try:
                files[rel] = _sha256_file(path)
            except OSError:
                files[rel] = "<unreadable>"
    digest = hashlib.sha256()
    for rel in sorted(files):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(files[rel].encode("utf-8"))
        digest.update(b"\n")
    return {"digest": digest.hexdigest(), "files": files}


def diff(before: Dict, after: Dict) -> List[str]:
    """列出变化：``+ 新增`` / ``- 删除`` / ``~ 修改``。"""
    a = before.get("files", {})
    b = after.get("files", {})
    changes: List[str] = []
    for rel in sorted(set(a) | set(b)):
        if rel not in a:
            changes.append("+ " + rel)
        elif rel not in b:
            changes.append("- " + rel)
        elif a[rel] != b[rel]:
            changes.append("~ " + rel)
    return changes


def set_readonly(profile_dir: str, readonly: bool = True, full: bool = False,
                 names=None) -> List[str]:
    """把文件设为只读 / 恢复可写。返回操作失败的文件列表。

    默认只动 ``READONLY_FILES``（`package.json` / `cordis.patch.yml`）——
    实测 `cordis.yml` 被锁会让 dsh 启动即 EPERM。传 ``full=True`` 或
    ``names=(...)`` 可以覆盖这个行为。
    """
    failed: List[str] = []
    profile_dir = os.path.abspath(os.path.expanduser(profile_dir))
    if not os.path.isdir(profile_dir):
        return failed
    allowed = None if full else set(names or READONLY_FILES)
    for rel, path in _iter_files(profile_dir, full):
        if allowed is not None and rel.rsplit("/", 1)[-1] not in allowed:
            continue
        try:
            mode = os.stat(path).st_mode
            if readonly:
                os.chmod(path, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
            else:
                os.chmod(path, mode | stat.S_IWUSR)
        except OSError:
            failed.append(rel)
    return failed


class ProfileGuard:
    """上下文管理器：进入时指纹 + 只读，退出时比对。

    ``tampered`` 为 True 表示这一轮里 profile 被改过（或者被解除了只读）。
    ``changes`` 给出具体是哪些文件。
    """

    def __init__(self, profile_dir: str, full: bool = False):
        self.profile_dir = os.path.abspath(os.path.expanduser(profile_dir))
        self.full = full
        self.before: Optional[Dict] = None
        self.after: Optional[Dict] = None
        self.changes: List[str] = []
        self.tampered = False
        self.lock_failures: List[str] = []
        self._was_dir = os.path.isdir(self.profile_dir)

    def __enter__(self) -> "ProfileGuard":
        self.before = fingerprint(self.profile_dir, self.full)
        if self._was_dir:
            self.lock_failures = set_readonly(self.profile_dir, True, self.full)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        # 先比对（此时只读仍生效），再解锁 —— 顺序反了会掩盖「解锁后才改」的窗口
        self.after = fingerprint(self.profile_dir, self.full)
        self.changes = diff(self.before, self.after)
        self.tampered = bool(self.changes)
        if self._was_dir:
            set_readonly(self.profile_dir, False, self.full)
        return False        # 不吞异常

    def summary(self) -> str:
        if not self.tampered:
            return "profile 未改动（%d 个声明文件）" % len((self.before or {}).get("files", {}))
        return "profile 被改动：%s" % "；".join(self.changes)
