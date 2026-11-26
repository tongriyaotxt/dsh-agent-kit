#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""脚手架 —— 一条命令生成一个 app 的四件套，并且**故意交付成"红"的**。

设计取舍：生成完立刻跑起来是绿的，会让人以为做完了。
所以 stub 的 `run()` 返回 `{"todo": True}`，与 `cases.json` 的期望不符，
`verify.py` 一跑就红 —— 这才是诚实的状态：逻辑还没写。

目录结构（docs/08 的应用侧契约 + docs/09 的四件套）：

    ~/apps/<name>/
      app.json          能力声明（也是 D4 的指纹锚点）
      AGENT_RULES.md    给 agent 的规则手册（五节模板）
      logic.py          纯函数 run(input, ctx) —— 先写它
      cases.json        断言：input → expect
      verify.py         自证入口（含变异检查）
      说明.md           给用户看的唯一一份文件
      state/app.json    状态（带 updated，界面靠它轮询）
      data/ out/ outbox/

用法：
    python -m dsh_agent_kit.scaffold 发票统计 --desc "按月统计发票总额"
    python -m dsh_agent_kit.scaffold 发票统计 --root /tmp/apps --force
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from string import Template


def slugify(name: str) -> str:
    """目录名 → 可用的 slug。中文名退化为哈希（不含 `app-` 前缀，前缀由 profile 加）。"""
    ascii_part = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    if ascii_part:
        return ascii_part
    return hashlib.md5(name.encode("utf-8")).hexdigest()[:8]


# ---------------------------------------------------------------- 模板
# 一律用 string.Template（$变量），因为模板里有大量 JSON / Python 花括号。
APP_JSON = Template("""{
  "version": 1,
  "name": $name,
  "slug": $slug,
  "desc": $desc,
  "profile": $profile,
  "surface": "window",
  "sandbox": "workspace-write",
  "network": false,
  "created": $created
}
""")

LOGIC_PY = Template('''#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""$literal_name —— 纯逻辑，不碰界面、不碰网络。

契约：run(input, ctx) -> dict
    input  来自 cases.json 的 input（也来自界面调用）
    ctx    受限上下文，只提供 data/ out/ state/ 的读写
           （见 dsh_agent_kit.verify.LocalCtx）

先让 cases.json 全绿，再谈界面。
"""


def run(input, ctx):
    # TODO: 这里写真正的逻辑。现在故意返回 todo，好让 verify 变红。
    return {"todo": True}
''')

CASES_JSON = Template("""{
  "app": $name,
  "updated": $created,
  "cases": [
    {
      "id": "c1",
      "note": "最小样例：先照抄一条真实输入输出",
      "input": { "rows": [{"amount": 100}, {"amount": 23.5}] },
      "expect": { "count": 2, "total": 123.5 }
    },
    {
      "id": "c2",
      "note": "边界：空输入应该是 0，不是报错",
      "input": { "rows": [] },
      "expect": { "count": 0, "total": 0 }
    }
  ]
}
""")

VERIFY_PY = Template('''#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""这个 app 的自证入口。改完 logic.py 就跑它。

    python verify.py            # 只看用例
    python verify.py --mutate   # 用例全绿后，验证断言不是摆设
"""

import os
import sys

# kit 的 Python 包位置（脚手架生成时写入；换机器改这一行）
sys.path.insert(0, $kit_path)

from dsh_agent_kit.verify import main  # noqa: E402

if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    extra = [a for a in sys.argv[1:] if a.startswith("-")]
    sys.exit(main(["--app", here] + (extra or ["--mutate"])))
''')

AGENT_RULES = Template("""# 给 agent 的操作手册 —— $literal_name

你在这个项目里的唯一职责：$literal_desc。不要做别的。

## 一、工作流

1. 读 `cases.json` —— 那是验收标准，不是建议
2. 改 `logic.py` 的 `run(input, ctx)`
3. 跑 `python verify.py --mutate` —— 必须「用例全绿 + 变异全抓」
4. 绿了才算做对；红了先证伪期望，再改代码

## 二、上下文在哪

| 文件 | 内容 |
|---|---|
| `cases.json` | 断言：每条 input → expect |
| `logic.py` | 纯函数 `run(input, ctx)`，不许碰界面 |
| `data/` | 输入；`out/` 产出；`outbox/` 待人工确认的出站动作 |
| `app.json` | 能力声明（档位 / 网络 / 表面）；**不要手改** |

## 三、输出格式

`run()` 的返回值必须能 JSON 序列化，且与 `cases.json` 的 `expect` 完全一致（键序无关）。

## 四、硬规则（违反会被拒）

1. **先逻辑后界面**：`cases.json` 不全绿之前，不要生成任何界面文件
2. **不许改 `cases.json` 的 `expect` 来迁就代码** —— 除非先说明「原期望写错了」并给出理由
3. 只读写 `data/` `out/` `state/`，其他路径一律不许
4. 不许联网（`app.json` 的 `network` 是 false）

## 五、禁止事项

- ❌ 不要改 `app.json` 的 `sandbox` / `network` 字段
- ❌ 不要为了「看起来跑通」而删掉失败的用例
- ❌ 不要输出完整方案，只出可裁决的最小改动

## 六、反例（这样写会被打回）

```python
def run(input, ctx):
    return {"todo": True}      # ← 脚手架的原样，未实现
```
""")

README_MD = Template("""# $literal_name

$literal_desc

## 怎么用

```bash
python verify.py --mutate     # 看它到底对不对（这是唯一可信的状态）
```

## 它是什么

- 一个**文件夹**：`logic.py` 是逻辑，`cases.json` 是验收标准，`data/` `out/` 是数据。
- 权限：workspace-write（只能读写本目录）；网络：禁用。
- 改逻辑 → 跑 `verify.py` → 绿了才算改对。

## 状态

刚生成，逻辑还没写（`verify.py` 一跑就是红的）。这是**故意**的。
""")


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


#: 新建 profile 的 bundle 声明：和 headless 一样，够跑无人值守。
PROFILE_PACKAGE = Template("""{
  "name": "dsh-profile-$slug",
  "private": true,
  "dependencies": {},
  "dsh": {
    "profile": {
      "bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-headless"],
      "patchReload": "startup"
    }
  }
}
""")

#: 新建 profile 的覆盖层：开凭证（否则 MISSING_CREDENTIAL）+ 关外联。
PROFILE_PATCH = """# 由 dsh_agent_kit.scaffold 生成
# 1) headless bundle 默认把凭证能力关掉，这里开回来（docs/03 第七节）
- id: credentials
  disabled: false
- id: authorization
  disabled: false
# 2) 默认收紧外联（docs/10 的 D3）
- id: tool-web
  disabled: true
- id: web-search-deepseek
  disabled: true
- id: web-fetch-http
  disabled: true
"""


def create_profile(profile: str, profiles_root: str = None, force: bool = False) -> str:
    """建一个可运行的 dsh profile。

    没有它，`dsh --profile app-xxx` 会直接 `profile not found` —— 新生成的 app
    等于跑不起来。这一步是 v0.1 的必要环。
    """
    root = os.path.abspath(os.path.expanduser(
        profiles_root or os.path.join(os.path.expanduser("~"), ".dsh", "profiles")))
    path = os.path.join(root, profile)
    if os.path.exists(path) and not force:
        return path                      # 已存在就尊重原样，不覆盖用户的配置
    _write(os.path.join(path, "package.json"),
           PROFILE_PACKAGE.substitute(slug=profile))
    _write(os.path.join(path, "cordis.yml"), "[]\n")
    _write(os.path.join(path, "cordis.patch.yml"), PROFILE_PATCH)
    return path


def _iso_now() -> str:
    import datetime
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def create_app(name: str, desc: str = "", root: str = None, force: bool = False,
               profiles_root: str = None) -> str:
    root = os.path.abspath(os.path.expanduser(
        root or os.path.join(os.path.expanduser("~"), "apps")))
    app_dir = os.path.join(root, name)
    if os.path.exists(app_dir) and not force:
        raise FileExistsError("app 已存在：%s（用 --force 覆盖模板文件）" % app_dir)

    kit_python = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    slug = slugify(name)
    desc = desc or "（还没写清楚这个 app 要做什么）"
    created = _iso_now()

    subs = {
        "name": json.dumps(name, ensure_ascii=False),
        "slug": json.dumps(slug),
        "desc": json.dumps(desc, ensure_ascii=False),
        "profile": json.dumps("app-" + slug),
        "created": json.dumps(created),
        "literal_name": name,
        "literal_desc": desc,
        "kit_path": repr(kit_python),
    }

    _write(os.path.join(app_dir, "app.json"), APP_JSON.substitute(subs))
    _write(os.path.join(app_dir, "logic.py"), LOGIC_PY.substitute(subs))
    _write(os.path.join(app_dir, "cases.json"), CASES_JSON.substitute(subs))
    _write(os.path.join(app_dir, "verify.py"), VERIFY_PY.substitute(subs))
    _write(os.path.join(app_dir, "AGENT_RULES.md"), AGENT_RULES.substitute(subs))
    _write(os.path.join(app_dir, "说明.md"), README_MD.substitute(subs))
    _write(os.path.join(app_dir, "state", "app.json"),
           json.dumps({"updated": created, "status": "scaffolded"},
                      ensure_ascii=False, indent=2))
    # 没有 profile 就跑不起来：顺手建一个。
    # 注意 force 不往下传：app 目录是「生成物」可以覆盖，profile 是「用户资产」
    # （用户可能已经调过 bundles / patch），永远不自动覆盖。
    create_profile("app-" + slug, profiles_root)

    for sub in ("data", "out", "outbox"):
        os.makedirs(os.path.join(app_dir, sub), exist_ok=True)
        _write(os.path.join(app_dir, sub, ".gitkeep"), "")
    return app_dir


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="生成一个 app 的四件套")
    parser.add_argument("name", help="app 名字（可以是中文）")
    parser.add_argument("--desc", default="", help="一句话说明它做什么")
    parser.add_argument("--root", default=None, help="父目录，默认 ~/apps")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--profiles-root", default=None,
                        help="dsh profile 父目录，默认 ~/.dsh/profiles")
    parser.add_argument("--no-profile", action="store_true", help="不创建 dsh profile")
    args = parser.parse_args(argv)

    try:
        app_dir = create_app(args.name, args.desc, args.root, args.force,
                             None if args.no_profile else args.profiles_root)
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print("已生成: %s" % app_dir)
    print("下一步：")
    print("  1) 把 cases.json 的两条样例换成你真实的输入输出")
    print("  2) 改 logic.py 的 run()，让 python verify.py --mutate 变绿")
    print("  3) 现在跑一次 verify，它应该是红的（逻辑还没写）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
