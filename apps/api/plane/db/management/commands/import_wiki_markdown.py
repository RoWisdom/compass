# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# One-way import: each folder under the wiki Markdown root becomes a Plane page
# collection, each .md inside it becomes a page. Plane then owns the content —
# edits there mirror back to the file (see the wiki page routes); edits made in
# the editor to the file are not read back.
#
# Three properties this command must keep:
#   * create-only — a page whose external_id already exists is skipped and left
#     exactly as it is, so re-running can never clobber an edit made in Plane.
#     The one exception is a directory page's `node_type`, which this command
#     re-stamps: it is not editable in Plane (every write path leaves it
#     read-only), so re-stamping cannot discard a user's change.
#   * read-only on the vault — importing writes no files at all;
#   * deterministic order — folders and files are created in *reverse*
#     alphabetical order so the model's `-created_at` default ordering shows
#     them alphabetically.

# Python imports
from pathlib import Path

# Django imports
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

# Module imports
from plane.db.models import Page, PageCollection, User, Workspace
from plane.utils.markdown_storage import get_wiki_markdown_root, split_frontmatter
from plane.utils.markdown_to_html import markdown_to_html

#: Stamped on every row this command creates. Also the get-or-create key along
#: with external_id, so rows from other sources are never touched.
EXTERNAL_SOURCE = "obsidian-vault"


class Command(BaseCommand):
    help = "Import folders of Markdown notes as Plane page collections (one-way, create-only)"

    def add_arguments(self, parser):
        parser.add_argument("--workspace-slug", required=True, help="Workspace slug, e.g. 926")
        parser.add_argument("--owner", required=True, help="Email of the user who owns the imported rows")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be created without touching the database.",
        )

    def handle(self, *args, **options):
        workspace = Workspace.objects.filter(slug=options["workspace_slug"]).first()
        if workspace is None:
            raise CommandError(f"Workspace not found: {options['workspace_slug']}")

        owner = User.objects.filter(email=options["owner"]).first()
        if owner is None:
            raise CommandError(f"User not found: {options['owner']}")

        root = get_wiki_markdown_root(workspace)
        if not root.is_dir():
            raise CommandError(f"Wiki Markdown root is not a directory: {root}")

        dry_run = options["dry_run"]
        folders = sorted(
            (entry for entry in root.iterdir() if entry.is_dir() and not entry.name.startswith(".")),
            key=lambda entry: entry.name,
            reverse=True,
        )

        created_collections = reused_collections = created_pages = skipped_pages = 0

        for folder in folders:
            notes = self._collect_notes(folder)

            if dry_run:
                self.stdout.write(f"[dry-run] collection {folder.name} <- {len(notes)} page(s)")
                continue

            collection, was_created = PageCollection.objects.get_or_create(
                workspace=workspace,
                external_source=EXTERNAL_SOURCE,
                external_id=f"3-Wiki/{folder.name}",
                defaults={"name": folder.name, "owned_by": owner},
            )
            if was_created:
                created_collections += 1
            else:
                reused_collections += 1

            for path in notes:
                outcome = self._import_note(workspace, owner, collection, folder, path)
                if outcome:
                    created_pages += 1
                else:
                    skipped_pages += 1

        if dry_run:
            self.stdout.write(self.style.SUCCESS("Dry run — nothing was written."))
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"collections: {created_collections} created, {reused_collections} reused; "
                f"pages: {created_pages} created, {skipped_pages} skipped"
            )
        )

    @staticmethod
    def _collect_notes(folder: Path) -> list:
        """Every .md under ``folder``, relative-path-sorted newest-first.

        Reverse order is what makes the sidebar come out alphabetical: the model
        orders by ``-created_at``, so the page created *last* is shown first.
        Dot-directories (``.obsidian``, ``.trash``) are skipped.
        """
        notes = [
            path
            for path in folder.rglob("*.md")
            if not any(part.startswith(".") for part in path.relative_to(folder).parts)
        ]
        return sorted(notes, key=lambda path: path.relative_to(folder).as_posix(), reverse=True)

    def _import_note(self, workspace, owner, collection, folder: Path, path: Path) -> bool:
        """Import one note. Returns ``True`` when a page was created, ``False``
        when it already existed and was left alone."""
        relative = path.relative_to(folder)
        external_id = f"3-Wiki/{folder.name}/{relative.as_posix()}"

        # A note in a sub-directory hangs off a page named after that directory —
        # the same convention the mirror uses (a page's children live in a folder
        # named after it), so a round trip lands in the same shape.
        #
        # This runs **before** the already-imported early return below, on purpose:
        # a re-run visits notes that already exist, and those are exactly the runs
        # where an existing directory page has to be re-stamped. Doing it after the
        # early return would make the re-stamp unreachable for every page the first
        # import created — i.e. for every page that needs it.
        parent_id = None
        for depth in range(1, len(relative.parts)):
            directory_parts = relative.parts[:depth]
            parent, created = Page.objects.get_or_create(
                workspace=workspace,
                external_source=EXTERNAL_SOURCE,
                external_id=f"3-Wiki/{folder.name}/{'/'.join(directory_parts)}",
                defaults={
                    "name": directory_parts[-1],
                    "parent_id": parent_id,
                    "owned_by": owner,
                    "is_global": True,
                    "collection": collection,
                    # 目录页是**文件夹**，不是页面（罗盘 Round D）。一条目录不再有自己的
                    # 正文 —— 它只是层级里的一个节点。
                    "node_type": Page.NODE_TYPE_FOLDER,
                },
            )
            if not created and parent.node_type != Page.NODE_TYPE_FOLDER:
                # **补盖**：`defaults` 只在**新建**时生效，所以本轮之前导过的那批目录页
                # 会留在 `"doc"` —— 侧栏里是页面图标、被算进集合计数、"最近编辑"里占位。
                # 补盖让"重跑一次导入"成为这个缺陷的修法，而不是要用户去手改数据库。
                #
                # **只写一个字段**（`update_fields`）：本命令的硬不变量是"重跑不抹掉
                # 用户在 Plane 里改过的正文"，而 `node_type` 在 Plane 里**没有任何写
                # 入口**（树/详情序列化器都把它放在 `read_only_fields`、更新序列化器
                # 根本不声明它）—— 它不是"用户的改动"，所以补它不越线。
                # `updated_at` 必须一起给：`auto_now` 只在字段进了 `update_fields`
                # 时才会落库。
                parent.node_type = Page.NODE_TYPE_FOLDER
                parent.save(update_fields=["node_type", "updated_at"])
            parent_id = parent.id

        if Page.objects.filter(
            workspace=workspace, external_source=EXTERNAL_SOURCE, external_id=external_id
        ).exists():
            return False

        _, body = split_frontmatter(path.read_text(encoding="utf-8"))

        with transaction.atomic():
            Page.objects.create(
                workspace=workspace,
                name=path.stem,
                description_html=markdown_to_html(body) or "<p></p>",
                owned_by=owner,
                is_global=True,
                access=Page.PUBLIC_ACCESS,
                collection=collection,
                parent_id=parent_id,
                external_source=EXTERNAL_SOURCE,
                external_id=external_id,
            )
        return True
