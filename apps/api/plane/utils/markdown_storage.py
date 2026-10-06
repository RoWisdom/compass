# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Environment variables pointing at the root directories where Markdown mirrors
# of Plane content are written. They are the **middle** priority: a workspace's
# own setting wins, and these win over the built-in defaults.
MARKDOWN_STORAGE_PATH_ENV = "MARKDOWN_STORAGE_PATH"
DEFAULT_PROJECT_MARKDOWN_PATH = "~/projects"

# Characters that are illegal in a file name on common filesystems, plus control
# characters. Replaced with "-" so the file name stays human-readable.
_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
# Cap the stem length to keep the file name within the 255-byte limit even for
# multi-byte (e.g. CJK) names.
_MAX_FILENAME_CHARS = 80


def get_markdown_root(workspace=None) -> Path:
    """Return the root directory for mirrored project-page Markdown files.

    Resolution is ``workspace.project_markdown_path`` > ``MARKDOWN_STORAGE_PATH``
    > the built-in default. ``workspace`` is duck-typed (``getattr``, not an
    import) because this module has no ORM dependency and is also called from
    management commands and unit tests where no request context exists.
    """
    field = getattr(workspace, "project_markdown_path", None)
    root = field or os.environ.get(MARKDOWN_STORAGE_PATH_ENV) or DEFAULT_PROJECT_MARKDOWN_PATH
    return Path(root).expanduser()


WIKI_MARKDOWN_STORAGE_PATH_ENV = "WIKI_MARKDOWN_STORAGE_PATH"
DEFAULT_WIKI_MARKDOWN_PATH = "~/wiki"


def get_wiki_markdown_root(workspace=None) -> Path:
    """Return the root directory for wiki page mirrors.

    A *separate* tree from ``get_markdown_root()`` — the two are configured
    independently, so this does **not** derive from the projects root. (It used
    to fall back to ``get_markdown_root().parent / "3-Wiki"``; that coupling made
    "I changed the projects dir and the wiki dir moved too" a real surprise.)

    Same precedence as ``get_markdown_root``: workspace field > env > default.
    """
    field = getattr(workspace, "wiki_markdown_path", None)
    root = field or os.environ.get(WIKI_MARKDOWN_STORAGE_PATH_ENV) or DEFAULT_WIKI_MARKDOWN_PATH
    return Path(root).expanduser()


def _sanitize_name(name: str) -> str:
    """Turn a name into a safe, human-readable file name stem."""
    name = _INVALID_FILENAME_CHARS.sub("-", name or "")
    name = re.sub(r"\.md$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:_MAX_FILENAME_CHARS]


def _file_stem(name: Optional[str], entry_id: str) -> str:
    """Return a file-name stem, falling back to the entry's id when the name
    sanitizes to an empty string."""
    return _sanitize_name(name or "") or entry_id


def _frontmatter_id(path: Path) -> Optional[str]:
    """Read the ``id`` field out of a mirrored Markdown file's frontmatter.

    Returns ``None`` when the file cannot be read or has no ``id`` line.
    """
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[:10]:
            if line.startswith("id: "):
                return line[4:].strip()
    except OSError:
        pass
    return None


def _project_directory_name(project_name: Optional[str], project_id: str) -> str:
    """Return the folder name for a project, preferring its human name."""
    return _sanitize_name(project_name or "") or str(project_id)


def project_directory(workspace, project) -> Path:
    """Return the directory a project's mirrored content lives in.

    This is also the "island" an AI member is confined to (design §2): the DSH
    sandbox's write root, and the tree that is snapshotted before and after a run
    to compute the run's artifacts. Public so ``plane.utils.agent_run`` does not
    have to reach for the private ``_project_directory_name``.
    """
    return get_markdown_root(workspace) / _project_directory_name(project.name, project.id)


def _yaml_str(value: str) -> str:
    """Quote a string so it is safe to embed as a YAML scalar."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


# --- Frontmatter merge ------------------------------------------------------
# Plane owns three keys and nothing else. Everything already in the file — tags,
# source, created, whatever the user's clipper wrote — is preserved verbatim.
# This matters because the target vault is full of clipped notes that already
# carry frontmatter: the old code prepended a second `---` block unconditionally
# (a freshly built frontmatter string concatenated in front of the body), which
# demoted the user's keys and the body after them into prose.

#: The only keys this module writes. Anything else in an existing frontmatter
#: block is the user's and is carried over untouched.
MANAGED_FRONTMATTER_KEYS = ("id", "title", "updated_at")

_FRONTMATTER_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+):")


def split_frontmatter(text: Optional[str]) -> tuple:
    """Split a Markdown file into ``(frontmatter lines, body)``.

    Returns ``([], text)`` when there is no frontmatter or the opening ``---``
    is never closed — an unterminated block is treated as body rather than
    swallowed, so a malformed file can never lose content.
    """
    if text is None:
        return [], ""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return [], text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return lines[1:index], "\n".join(lines[index + 1 :]).lstrip("\n")
    return [], text


def _drop_managed_keys(lines: list) -> list:
    """Return ``lines`` without the managed keys and their continuation lines.

    A "continuation" is an indented line following a key — a YAML block scalar
    (``title: |``) or a nested list. Dropping the key but keeping its body would
    leave orphaned YAML, so the continuation goes with it. Indented lines are
    never *matched* as keys, so a nested list under a key we keep survives.
    """
    kept = []
    skipping = False
    for line in lines:
        key_match = _FRONTMATTER_KEY_RE.match(line)
        if key_match:
            skipping = key_match.group(1) in MANAGED_FRONTMATTER_KEYS
            if not skipping:
                kept.append(line)
            continue
        if line.strip() and line[:1] in (" ", "\t"):
            if not skipping:
                kept.append(line)
            continue
        skipping = False
        kept.append(line)
    return kept


def merge_frontmatter(existing: Optional[str], *, title: Optional[str], entry_id: str, body: str) -> str:
    """Return full file content: our frontmatter keys merged over the existing block.

    Plane's keys come first in a fixed order; the user's remaining keys follow in
    their original order. A file with no (or malformed) frontmatter gets a fresh
    block, which is exactly the old behaviour.
    """
    preserved, _ = split_frontmatter(existing)
    if existing and existing.split("\n")[0].strip() == "---" and not preserved:
        # Opening `---` with no closing one: the block cannot be parsed, so its
        # lines are dropped rather than carried over — keeping them would leave
        # two frontmatter blocks in the file. Warn rather than fail: the page
        # body must still land, and the file was already malformed.
        logger.warning("Discarding unparseable frontmatter: no closing '---' found")
    managed = ["---", f"id: {entry_id}"]
    if title:
        managed.append(f"title: {_yaml_str(title)}")
    managed.append(f"updated_at: {datetime.now(timezone.utc).isoformat()}")
    return "\n".join(managed + _drop_managed_keys(preserved) + ["---", "", body or ""])


# --- Page mirrors -----------------------------------------------------------
# Pages mirror under {root}/{project}/{ancestor...}/{name}.md so that a page's
# sub-pages nest in a folder named after their parent page. ``ancestors`` is the
# parent chain, root-first, as a list of (name, id) tuples.


def _resolve_page_path(
    directory: Path,
    ancestors: list,
    name: Optional[str],
    page_id: str,
    own_path: Optional[Path] = None,
) -> Path:
    """Resolve a page's file under ``directory``, nesting sub-pages in a folder
    named after their parent. A name collision with a *different* page appends
    ``-{id[:8]}``. Shared by the project and wiki trees — the only difference
    between them is which root the directory started from.

    ``own_path`` is where this page's file is *supposed* to live — the vault path
    it was imported from (``Page.external_id``), made absolute by the caller.
    It only matters for a file that exists with **no** ``id:`` line: such a file
    counts as ours only when it is exactly ``own_path``; otherwise it is someone
    else's clipped/hand-written note and we must not write over it.

    The default is deliberately the **conservative** one: a caller that does not
    know where the page came from (``own_path=None``) never has a file that
    "is exactly" it, so a no-``id:`` file on the target name is let alone and
    the write lands on the ``-{id[:8]}`` sibling. That is the ruled semantics —
    the failure direction is "a second file", never "a destroyed note". Every
    project-tree caller takes the default; only the wiki side passes ``own_path``.
    """
    for ancestor_name, ancestor_id in ancestors or []:
        directory = directory / _file_stem(ancestor_name, ancestor_id)
    stem = _file_stem(name, page_id)
    path = directory / f"{stem}.md"
    if path.exists():
        existing_id = _frontmatter_id(path)
        if existing_id is not None and existing_id != page_id:
            # 已有 id 行，且不是本页 —— 另一个页面的镜像，让开。
            path = directory / f"{stem}-{page_id[:8]}.md"
        elif existing_id is None and path != own_path:
            # 没有 id 行：只有当它**就是本页自己的来源文件**时才算我们的（见 docstring）。
            # 否则它是别人的剪藏/手写笔记，让开 —— 覆盖它等于删用户的笔记。
            path = directory / f"{stem}-{page_id[:8]}.md"
    return path


# --- Shared page-file operations --------------------------------------------
# Both trees write and move their mirrors the same way; only the path each of
# them starts from differs. These two helpers hold the one implementation, so
# the project and wiki sides cannot drift apart. Errors are *not* caught here —
# each caller keeps its own try/except and its own log message. ``_move_page_file``
# does warn on its own for the two refusals below, since those are outcomes the
# callers cannot see (they look like a plain no-op).


def _write_page_file(path: Path, page_id: str, name: Optional[str], markdown: str) -> None:
    """Write ``markdown`` to ``path`` atomically, merging any frontmatter already
    in the file. Writes to a sibling ``.tmp`` and ``replace``s it over the target
    so a partial write can never truncate the mirror."""
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = None
    if path.exists():
        existing = path.read_text(encoding="utf-8")

    content = merge_frontmatter(existing, title=name, entry_id=page_id, body=markdown or "")

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def _move_page_file(
    old_path: Path,
    new_path: Path,
    old_name: Optional[str],
    new_name: Optional[str],
    page_id: str,
) -> bool:
    """Move a page's .md (and its sub-page folder) after a rename/reparent.

    Two refusals guard the move, both logged and both in the direction of
    *not* moving — never of overwriting or deleting. A file whose ``id:``
    belongs to a different page is not ours to move, and a file already sitting
    at the target with another page's ``id:`` must not be clobbered. A file with
    **no** ``id:`` is treated as ours: on the wiki tree the caller's path
    resolution has already decided (via ``own_path``) whether a no-``id:`` file
    is this page's own; on the project tree it is the pre-existing behaviour.

    Returns ``True`` when this page's own ``.md`` is at ``new_path`` **because this
    call put it there**, and ``False`` for every other outcome: nothing to move, a
    refusal, or ``old_path == new_path``. The sub-page folder is not part of the
    answer. The wiki move uses this to decide whether the recorded path may follow
    the file — see ``collection._repoint_page_external_id``.
    """
    moved = False
    if old_path == new_path:
        return False
    if old_path.exists():
        old_id = _frontmatter_id(old_path)
        target_taken = new_path.exists() and _frontmatter_id(new_path) not in (None, page_id)
        if old_id not in (None, page_id):
            # 来源不是本页的文件 —— 永远不搬、不改名（可能是别页的镜像，或用户的手写笔记）。
            logger.warning(
                "Refusing to move markdown mirror for page %s: %s carries id %s", page_id, old_path, old_id
            )
        elif target_taken:
            # 目标位置已有不是本页的文件 —— 不搬，否则一整篇别人的笔记被静默覆盖。
            logger.warning(
                "Refusing to move markdown mirror for page %s: target %s belongs to another page",
                page_id,
                new_path,
            )
        else:
            # 目录**只在真的搬文件时**建。搬到这儿之前的每一步（没有源文件、来源不是本页、
            # 目标被占）都是「什么都不动」，凭空建出目标目录会在 vault 里留下一片空文件夹
            # —— 对那些镜像从未落盘的页面尤其明显（页面行存在、正文一次都没写过，本函数
            # 照样会被调到，此时一个源文件都没有）。
            new_path.parent.mkdir(parents=True, exist_ok=True)
            old_path.replace(new_path)
            moved = True
    # Move the sub-page folder (named after this page) when its location or
    # stem changed, so descendants follow the rename/reparent.
    old_stem = _file_stem(old_name, page_id)
    new_stem = _file_stem(new_name, page_id)
    old_dir = old_path.parent / old_stem
    new_dir = new_path.parent / new_stem
    if old_dir != new_dir and old_dir.is_dir() and not new_dir.exists():
        new_dir.parent.mkdir(parents=True, exist_ok=True)
        old_dir.replace(new_dir)

    return moved


def delete_page_file(path: Path, page_id: str) -> bool:
    """删掉 ``path`` —— **当且仅当它是我们写的**（罗盘 Round I，设计 §4.3 规则一）。

    「我们写的」= frontmatter 的 ``id:`` 正是 ``page_id``。其余一律留下：

      · **没有 `id:` 行** ⇒ 它可能是这一页被导入时的**原稿**（用户自己手写的笔记）；
      · **`id:` 是别人** ⇒ 别人的剪藏、别的页面的镜像。

    这条判定不是这里发明的：它就是 ``_resolve_page_path`` / ``_move_page_file``
    已经在用的同一个 ``_frontmatter_id``，只是方向从「拒绝写 / 拒绝搬」变成「拒绝删」。
    本仓写死的失败方向因此逐字守住 —— **never "a destroyed note"**。

    ⚠️ ``UnicodeDecodeError`` 不是 ``OSError``（它是 ``ValueError``），而 ``_frontmatter_id``
    只兜 ``OSError`` ⇒ 一个非 UTF-8 的文件会一路冒上去把整次删除变成 500。
    ``collection.py`` 搬移那侧踩过同一个坑（``test_..._non_utf8_file_...`` 就是为它写的）。

    返回 ``True`` **只在真的删掉了一个文件**时 —— 调用方靠它决定要不要往上收目录。
    """
    # 一个 falsy 的 ``page_id`` 必须**立刻**拒绝。``_frontmatter_id`` 对一个没有 ``id:``
    # 行的文件返回 ``None``，所以真把 ``None`` 传进来会**恰好匹配**用户手写的那一类文件
    # —— 正好是本函数唯一绝不允许删的东西，失败方向整个反过来。调用方今天传的是
    # ``str(page.id)``（UUID），不可达；但这条不变量值得自我防卫，不能只靠调用方守规矩。
    if not page_id:
        return False

    try:
        if not path.is_file():
            return False
        if _frontmatter_id(path) != page_id:
            return False
        path.unlink()
        return True
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Failed to delete markdown mirror of page %s: %s", page_id, exc)
        return False


def prune_empty_directories(paths, *, stop_at: Path) -> None:
    """把删完文件之后**空掉**的目录收掉 —— 自底向上，非空即停（设计 §4.3 规则二/三）。

    ``paths`` 是**刚被删掉的文件**路径，``stop_at`` 是这次删除所属那棵树的根
    （wiki 树给 ``get_wiki_markdown_root``，项目树给 ``get_markdown_root``）——
    爬到它就停，**永不动它**。

    为什么「非空就 ``break`` 整条链」既正确又省事：一个目录非空 ⇒ **它的祖先必然
    也非空**（祖先包含它），所以没有必要继续往上试。

    那个论证只覆盖**同一次向上爬**。跨路径还有一层补偿：同一条目录会被后面另一条
    路径**再试一次** —— 它此刻可能已经被那条路径腾空了。所以 ``seen`` 只在
    **``rmdir`` 成功之后**才记（失败就记 ⇒ 永久留下一个空目录，见下面的注释）。

    为什么遍历时先看 ``stop_at not in directory.parents``：路径万一不在 ``stop_at``
    这棵树里（软链、被绕过的数据），没有这一条就会顺着父目录一路 ``rmdir`` 到 ``/``。

    **永不 `rmtree`**（规则三，没有例外）：文件夹目录里可能躺着**不是镜像**的文件
    —— 手写笔记、附件、Obsidian 自己的东西。``rmtree`` 会把它们一次吃掉且不可逆；
    ``rmdir`` 只可能失败，而失败方向是「目录留下」，正是我们要的那一边。
    """
    seen = set()
    for path in paths:
        directory = Path(path).parent
        while directory != stop_at and directory not in seen:
            if stop_at not in directory.parents:
                break
            try:
                os.rmdir(directory)
            except OSError as exc:
                # 三种正常结局：目录本来就不存在、非空（还留着不是我们写的文件）、只读盘。
                #
                # ⚠️ **失败时绝不能把 directory 记进 `seen`。** 它现在非空，但同一批里
                # 排在**后面**的另一条文件路径可能正好在它下面；那条删完之后它就空了。
                # 先记 `seen` 等于「试过一次、失败、永不重试」⇒ 那个已经空掉的目录被永久
                # 留下。实测：`paths=[A/x.md, A/B/y.md]` 里 A 会残留，而反过来传
                # `[A/B/y.md, A/x.md]` 两个都删干净 —— 一个**顺序相关**的 bug，而喂进来的
                # 顺序是 queryset 给的（无序）。
                logger.info("Not removing the directory %s: %s", directory, exc)
                break
            # 只有**真的删掉**了才记进来（它不可能再被删第二次）。
            seen.add(directory)
            directory = directory.parent


def move_mirror_file(
    old_path: Path,
    new_path: Path,
    old_name: Optional[str],
    new_name: Optional[str],
    page_id: str,
) -> bool:
    """Move an already-resolved page mirror from ``old_path`` to ``new_path``.

    ``move_page_markdown`` and ``move_wiki_page_markdown`` each resolve their two
    paths inside their own root and call ``_move_page_file`` directly, keeping
    their own log message. This entry point exists for the move that has **no
    single root to resolve inside**: a page crossing between the wiki tree and the
    project tree (collection → project, or the reverse), where only the caller
    knows both the old and the new state and so resolves both paths itself.

    Three entry points, one implementation: the ``OSError`` guard and the log
    message are the whole of this function, so ``_move_page_file``'s
    sub-page-folder handling is still written exactly once. Best-effort like every
    other mirror call: ``OSError`` is logged, never raised.

    Returns what ``_move_page_file`` returns — see its docstring. An ``OSError``
    counts as "not moved": the exception is logged and ``False`` is returned, so a
    caller that follows the return value never follows a move that did not happen.
    """
    try:
        return _move_page_file(old_path, new_path, old_name, new_name, page_id)
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Failed to move markdown mirror %s: %s", page_id, exc)
        return False


def page_markdown_path(
    *,
    project_name: Optional[str],
    project_id: str,
    ancestors: list,
    name: Optional[str],
    page_id: str,
    root: Path,
) -> Path:
    """Resolve the absolute path of a project page's Markdown file."""
    directory = root / _project_directory_name(project_name, project_id)
    return _resolve_page_path(directory, ancestors, name, page_id)


def write_page_markdown(
    *,
    project_name: Optional[str],
    project_id: str,
    ancestors: list,
    page_id: str,
    name: Optional[str],
    markdown: str,
    root: Path,
) -> None:
    """Write a page's content to a local Markdown file (best-effort).

    The frontmatter already in the file is read first and merged, so a note that
    arrived with its own keys (Obsidian Web Clipper output, hand-written
    frontmatter) keeps them.
    """
    try:
        path = page_markdown_path(
            project_name=project_name,
            project_id=project_id,
            ancestors=ancestors,
            name=name,
            page_id=page_id,
            root=root,
        )
        _write_page_file(path, page_id, name, markdown)
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Failed to write page markdown mirror %s: %s", page_id, exc)


def move_page_markdown(
    *,
    project_name: Optional[str],
    project_id: str,
    old_ancestors: list,
    new_ancestors: list,
    page_id: str,
    old_name: Optional[str],
    new_name: Optional[str],
    root: Path,
) -> None:
    """Move a page's .md (and its sub-page folder) after a rename/reparent."""
    try:
        old_path = page_markdown_path(
            project_name=project_name,
            project_id=project_id,
            ancestors=old_ancestors,
            name=old_name,
            page_id=page_id,
            root=root,
        )
        new_path = page_markdown_path(
            project_name=project_name,
            project_id=project_id,
            ancestors=new_ancestors,
            name=new_name,
            page_id=page_id,
            root=root,
        )
        _move_page_file(old_path, new_path, old_name, new_name, page_id)
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Failed to move page markdown mirror %s: %s", page_id, exc)


def delete_page_markdown(
    *,
    project_name: Optional[str],
    project_id: str,
    ancestors: list,
    page_id: str,
    name: Optional[str],
    root: Path,
) -> None:
    """Delete a page's local Markdown file (best-effort)."""
    try:
        page_markdown_path(
            project_name=project_name,
            project_id=project_id,
            ancestors=ancestors,
            name=name,
            page_id=page_id,
            root=root,
        ).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Failed to delete page markdown mirror %s: %s", page_id, exc)


# --- Wiki page mirrors ------------------------------------------------------
# Wiki pages mirror under {wiki root}/{collection}/{ancestor...}/{name}.md, so a
# collection's folder is the sibling of a project's folder in the vault. Only the
# root differs: the nesting rule, the collision fallback, the sanitising and the
# write/move file operations are the same functions the project tree uses — one
# implementation, two roots.


#: 「常规」分区（无集合、无项目的页面）在 wiki 树里的目录名。
#: 它是一个**目录名**，不是一个集合 —— 预置四分区一律不建 `PageCollection` 行
#: （`utils/wiki_collections.py` 从页面自身字段推导归属），所以常规既没有名字也没有 id，
#: 只能靠这个常量落成一个与集合目录同级的文件夹。
GENERAL_DIRECTORY = "常规"


def wiki_general_directory(*, root: Path) -> Path:
    """「常规」分区在 wiki 树里的目录 —— 与集合目录同级。

    存在的理由：`wiki_collection_directory(collection_name=None, collection_id="")`
    会退化成 `root / str(collection_id)`（本文件 `wiki_collection_directory`），
    那是给「无名字的集合」用的兜底，不是常规该有的形状。常规既没有名字也没有 id，
    所以它需要自己的一行。
    """
    return root / GENERAL_DIRECTORY


def wiki_collection_directory(*, collection_name: Optional[str], collection_id: str, root: Path) -> Path:
    """The folder a collection's mirrors live in, under the wiki vault root.

    ``root`` is resolved by the caller and passed in — this module does no
    resolution here.

    One definition, two callers: ``wiki_page_markdown_path`` resolves a page's file
    inside it, and a collection rename moves it. Having the two disagree about the
    name is not hypothetical — writing through ``_sanitize_name`` while moving by
    the raw name put ``3-Wiki/C  D/`` and ``3-Wiki/C D/`` on disk at once, which is
    exactly the split-folder bug the rename exists to prevent.
    """
    return root / (_sanitize_name(collection_name or "") or str(collection_id))


def wiki_page_markdown_path(
    *,
    collection_name: Optional[str],
    collection_id: str,
    ancestors: list,
    name: Optional[str],
    page_id: str,
    root: Path,
    own_path: Optional[Path] = None,
) -> Path:
    """Resolve the absolute path of a wiki page's Markdown file.

    ``own_path`` is the page's own vault source file (from ``Page.external_id``)
    and is passed straight to ``_resolve_page_path`` — see its docstring for why
    a file with no ``id:`` needs it. Only the wiki side passes it; the project tree
    keeps the default (``None``), which by the rule above means a no-``id:`` file on
    the target name is **left alone** and the write lands on the
    ``-{id[:8]}`` sibling instead — a change from the pre-W2 behaviour, which
    overwrote it.
    """
    directory = wiki_collection_directory(
        collection_name=collection_name, collection_id=collection_id, root=root
    )
    return _resolve_page_path(directory, ancestors, name, page_id, own_path=own_path)


def wiki_general_page_markdown_path(
    *,
    ancestors: list,
    name: Optional[str],
    page_id: str,
    root: Path,
    own_path: Optional[Path] = None,
) -> Path:
    """Resolve the absolute path of a 「常规」 wiki page's Markdown file.

    **不能**用 `wiki_page_markdown_path(collection_name="", collection_id="", ...)`
    顶替：那会经 `wiki_collection_directory` 解析成 `root / ("" or "")`，而 pathlib
    把 `root / ""` 折叠成 `root` 本身 —— 落点是 wiki 根，不是 `常规/`。
    真正要做的是**换掉 `wiki_collection_directory` 那一步**，之后与集合版逐字相同。

    `own_path` 的语义与 `wiki_page_markdown_path` 逐字相同（无 `id:` 的文件只有
    正好是它时才被认作本页自己的来源文件）。
    """
    return _resolve_page_path(
        wiki_general_directory(root=root), ancestors, name, page_id, own_path=own_path
    )


def write_wiki_page_markdown(
    *,
    collection_name: Optional[str],
    collection_id: str,
    ancestors: list,
    page_id: str,
    name: Optional[str],
    markdown: str,
    root: Path,
    own_path: Optional[Path] = None,
) -> None:
    """Write a wiki page's content to its local Markdown file (best-effort).

    Same semantics as ``write_page_markdown``: the frontmatter already in the
    file is read first and merged, so an imported clip keeps the keys its
    clipper wrote (``tags``, ``source``, handmade frontmatter) instead of having
    them demoted to prose under a second ``---`` block.

    ``own_path`` (see ``wiki_page_markdown_path``) keeps a name collision with a
    hand-written note from overwriting it: the write lands on the ``-{id[:8]}``
    sibling instead.
    """
    try:
        path = wiki_page_markdown_path(
            collection_name=collection_name,
            collection_id=collection_id,
            ancestors=ancestors,
            name=name,
            page_id=page_id,
            root=root,
            own_path=own_path,
        )
        _write_page_file(path, page_id, name, markdown)
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Failed to write wiki page markdown mirror %s: %s", page_id, exc)


def write_wiki_general_page_markdown(
    *,
    ancestors: list,
    page_id: str,
    name: Optional[str],
    markdown: str,
    root: Path,
    own_path: Optional[Path] = None,
) -> None:
    """Write a 「常规」 wiki page's content to its local Markdown file (best-effort).

    与 `write_wiki_page_markdown` 逐字相同，只少两个集合参数、并把
    `wiki_page_markdown_path` 换成 `wiki_general_page_markdown_path`。
    语义（合并 frontmatter、`own_path` 的归属守卫、只吞 `OSError` / `UnicodeDecodeError`）
    一条不动 —— 只读 vault 不得让一次页面保存失败。
    """
    try:
        path = wiki_general_page_markdown_path(
            ancestors=ancestors,
            name=name,
            page_id=page_id,
            root=root,
            own_path=own_path,
        )
        _write_page_file(path, page_id, name, markdown)
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Failed to write wiki page markdown mirror %s: %s", page_id, exc)


def move_wiki_page_markdown(
    *,
    collection_name: Optional[str],
    collection_id: str,
    old_ancestors: list,
    new_ancestors: list,
    page_id: str,
    old_name: Optional[str],
    new_name: Optional[str],
    root: Path,
    own_path: Optional[Path] = None,
) -> None:
    """Move a wiki page's .md (and its sub-page folder) after a rename/reparent.

    Same semantics as ``move_page_markdown``: the file is ``replace``d into its
    new path, and the sub-page folder (named after this page) follows when the
    page's name or location changed so descendants stay nested under it.

    ``own_path`` (see ``wiki_page_markdown_path``) is threaded into both path
    resolutions so a file with no ``id:`` is only treated as this page's own
    when it actually is.

    No production caller today: a wiki page's mirror is moved through
    ``_move_wiki_page_mirror`` (``app/views/page/collection.py``), which resolves
    both of its paths itself and calls ``move_mirror_file``. This entry point
    cannot express what that caller must — it takes **one** collection for both
    the old and the new state, so it could never move a page that changes
    collection. Kept because it is the wiki-shaped sibling of
    ``move_page_markdown`` and because the implementation plan's Interfaces list
    names it; delete it only together with that plan entry.
    """
    try:
        old_path = wiki_page_markdown_path(
            collection_name=collection_name,
            collection_id=collection_id,
            ancestors=old_ancestors,
            name=old_name,
            page_id=page_id,
            root=root,
            own_path=own_path,
        )
        new_path = wiki_page_markdown_path(
            collection_name=collection_name,
            collection_id=collection_id,
            ancestors=new_ancestors,
            name=new_name,
            page_id=page_id,
            root=root,
            own_path=own_path,
        )
        _move_page_file(old_path, new_path, old_name, new_name, page_id)
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Failed to move wiki page markdown mirror %s: %s", page_id, exc)
