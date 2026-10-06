# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""纯函数支撑 —— 一次 AI 成员运行里所有不需要 DB、不需要网络的判断。

刻意不 import 任何 ORM 模型：``project`` / ``workspace`` 都是鸭子类型的
（只用 ``.name`` / ``.id`` / ``.project_markdown_path``），这样单元测试可以拿
轻量替身喂进来。见设计 §3「产物怎么算出来」与 §6 一的四条实测结论。
"""

# Python imports
import hashlib
import os
from pathlib import Path


def dsh_home() -> Path:
    """DSH 的 home —— 会话、技能、存储都挂在这下面。默认 ``~/.dsh``。"""
    return Path(os.environ.get("DSH_HOME", "~/.dsh")).expanduser()


def skills_root() -> Path:
    """技能目录。一个技能 = 一个 ``<root>/<name>/SKILL.md``（设计 §8）。"""
    return dsh_home() / "skills"


def skill_body(name: str) -> str:
    """Read a skill's Markdown body, or ``""`` when there is no such skill.

    The skill's **body** is what gets concatenated after the member's handbook
    (design §8: 「挂载 = 拼接」). Frontmatter is kept verbatim — DSH's own skill
    loader is the one that parses it, and we are only forwarding text.
    """
    path = skills_root() / name / "SKILL.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def permission_mode_for_tier(tier: str) -> str:
    """档位 → ``DSH_PERMISSION_MODE``。

    Only 甲 (read-only) gets the read-only sandbox; 乙 and 丙 both write, but
    only inside the island. Verified hard constraint — see design §6.1.
    """
    return "read-only" if tier == "readonly" else "workspace-write"


def _file_digest(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_tree(root: Path) -> dict:
    """Return ``{path relative to root: sha1}`` for every readable file under root.

    A missing root is not an error — it renders as ``{}`` so that a run whose
    island does not exist yet still diffs cleanly against the empty tree.
    Unreadable files are skipped rather than failing the run: an artifact list
    that is missing one file beats a crashed task.
    """
    root = Path(root)
    snapshot = {}
    if not root.is_dir():
        return snapshot
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            snapshot[str(path.relative_to(root))] = _file_digest(path)
        except OSError:
            continue
    return snapshot


def diff_snapshot(before: dict, after: dict) -> list:
    """Return the sorted relative paths that are new or whose content changed.

    This is how ``AgentRun.artifacts`` is computed — never from the model's own
    report (design §3).
    """
    changed = [name for name, digest in after.items() if before.get(name) != digest]
    return sorted(changed)


def sessions_root() -> Path:
    """``~/.dsh/sessions`` — one directory per cwd slug (design §6.1)."""
    return dsh_home() / "sessions"


def session_dirs() -> set:
    """Every ``session-*`` directory currently under ``sessions_root()``, as strings.

    The slug algorithm (absolute path with ``/`` → ``-``) is **not** relied on
    here: we take a set before the run and a set after, and the difference is the
    run. That survives any change to how DSH spells the slug.
    """
    root = sessions_root()
    if not root.is_dir():
        return set()
    return {str(path) for path in root.rglob("session-*") if path.is_dir()}


def new_session_ref(before: set, after: set) -> str:
    """Return the directory name of the single new session dir, else ``""``.

    Exactly one new session means the run created it. Zero means the run did not
    get as far as a session; more than one means something else was writing to
    the same home at the same time. In both of those cases we prefer an empty
    ref over a wrong one.
    """
    new = sorted(after - before)
    if len(new) != 1:
        return ""
    return Path(new[0]).name
