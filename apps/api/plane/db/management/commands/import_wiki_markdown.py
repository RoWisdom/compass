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
#     exactly as it is, so re-running can never clobber an edit made in Plane;
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

        root = get_wiki_markdown_root()
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

        if Page.objects.filter(
            workspace=workspace, external_source=EXTERNAL_SOURCE, external_id=external_id
        ).exists():
            return False

        # A note in a sub-directory hangs off a page named after that directory —
        # the same convention the mirror uses (a page's children live in a folder
        # named after it), so a round trip lands in the same shape.
        parent_id = None
        for depth in range(1, len(relative.parts)):
            directory_parts = relative.parts[:depth]
            parent, _ = Page.objects.get_or_create(
                workspace=workspace,
                external_source=EXTERNAL_SOURCE,
                external_id=f"3-Wiki/{folder.name}/{'/'.join(directory_parts)}",
                defaults={
                    "name": directory_parts[-1],
                    "owned_by": owner,
                    "is_global": True,
                    "collection": collection,
                },
            )
            parent_id = parent.id

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
