# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""System checks for the Markdown mirror roots.

``Page.external_id`` / ``PageCollection.external_id`` hold a **vault-relative**
path whose first segment is the literal directory name ``3-Wiki`` (see
``import_wiki_markdown``). ``page/collection._wiki_page_own_path`` resolves such
a path as ``get_wiki_markdown_root(workspace).parent / external_id``, while a
mirror write lands at ``get_wiki_markdown_root(workspace) / <集合> / <笔记>.md``.

Those two name the same file only when the workspace's **effective** wiki root is
itself a directory called ``3-Wiki``. Point it anywhere else and every imported
row resolves to a path that is *not* where writes land — silently, because every
mirror write is best-effort and swallows ``OSError``.

The check fires only for the combination that actually loses the mapping: a
workspace whose effective wiki root is misnamed **and** which already owns rows
that depend on the ``3-Wiki`` prefix. The env-var misnaming is a Warning, so the
global config is reported even before any workspace has a field of its own.
"""

# Standard Library
import os
from pathlib import Path

# Django imports
from django.core.checks import Error, Warning, register

# The name ``external_id`` hard-codes as its first segment, and the environment
# variable that can misname the root globally. Both mirror
# ``plane.utils.markdown_storage``.
REQUIRED_WIKI_ROOT_NAME = "3-Wiki"
WIKI_ROOT_ENV = "WIKI_MARKDOWN_STORAGE_PATH"
EXTERNAL_ID_PREFIX = "3-Wiki/"


@register()
def wiki_markdown_root_names(app_configs, **kwargs):
    """Verify every wiki mirror root can resolve the paths of the rows it owns."""
    # Local imports: this module is imported from ``AppConfig.ready()``, so it
    # must not touch the ORM at import time. ``django.db.utils`` is imported here
    # for the same reason as the models, and to sit beside the ``except`` below.
    from django.db.utils import DatabaseError

    from plane.db.models import Page, PageCollection, Workspace
    from plane.utils.markdown_storage import get_wiki_markdown_root

    findings = []

    try:
        # The env-var branch sits *inside* the guard: ``Path.expanduser()`` can
        # raise ``RuntimeError`` (``Could not determine home directory``) when
        # ``HOME`` is unset, and this check runs on *every* management command.
        # It is also evaluated before the ORM queries so that its ``plane.W001``
        # finding survives the ``except`` below when the database is unreachable.
        env_root = os.environ.get(WIKI_ROOT_ENV)
        if env_root and Path(env_root).expanduser().name != REQUIRED_WIKI_ROOT_NAME:
            findings.append(
                Warning(
                    f"{WIKI_ROOT_ENV} 指向 {env_root!r}，目录名不是 {REQUIRED_WIKI_ROOT_NAME!r}。",
                    hint=(
                        f"wiki 页会写进该目录，而 external_id 相对的是它父目录下的 "
                        f"{REQUIRED_WIKI_ROOT_NAME}/。请把它指到名为 {REQUIRED_WIKI_ROOT_NAME} 的目录。"
                    ),
                    id="plane.W001",
                )
            )

        dependent_ids = set(
            Page.objects.filter(external_id__startswith=EXTERNAL_ID_PREFIX).values_list(
                "workspace_id", flat=True
            )
        ) | set(
            PageCollection.objects.filter(external_id__startswith=EXTERNAL_ID_PREFIX).values_list(
                "workspace_id", flat=True
            )
        )
        for workspace in Workspace.objects.filter(id__in=dependent_ids):
            root = get_wiki_markdown_root(workspace)
            if root.name == REQUIRED_WIKI_ROOT_NAME:
                continue
            findings.append(
                Error(
                    f"工作区 {workspace.slug!r} 的 wiki 镜像根是 {str(root)!r}，目录名不是 "
                    f"{REQUIRED_WIKI_ROOT_NAME!r}，但它已有 external_id 以 {EXTERNAL_ID_PREFIX!r} "
                    f"开头的页面或集合。",
                    hint=(
                        "这些行的 external_id 相对的是该根的父目录，而写入落在根本身，两者对不上。"
                        f"请把 wiki 根设成名为 {REQUIRED_WIKI_ROOT_NAME} 的目录。"
                    ),
                    id="plane.E001",
                )
            )
    except (DatabaseError, RuntimeError):
        # The database is unreachable or a table is missing on a fresh database
        # mid-migrate — any ``DatabaseError``, not just the two narrower
        # subclasses this once caught, since a connection dropped mid-command
        # surfaces as ``InterfaceError``/``InternalError``/``DataError`` — or the
        # home directory could not be resolved (``RuntimeError`` from
        # ``expanduser``). A system check must never be the reason a management
        # command cannot run — 226's middleware has dropped out for minutes at a
        # time, and this check runs on *every* management command. ``findings``
        # is returned as-is, so a ``plane.W001`` appended above is still reported.
        return findings

    return findings
