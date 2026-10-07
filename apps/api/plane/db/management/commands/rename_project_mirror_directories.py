# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# One-off migration: a project's Markdown mirror directory used to be named after
# the project's *sanitized name*; it is now named after the project's **id**
# (``_project_directory_name``). This command moves the existing directories so
# the mirrors keep being found after the rename.
#
# The new name is also the AI member's sandbox island (设计 §2), so it must be
# unique per project — a sanitized name is not: sanitising runs **after** the
# ``(name, workspace)`` uniqueness the database enforces, so two legal projects
# could fold into one directory. That is why this is a rename and not a nicety.

# Django imports
from django.core.management.base import BaseCommand

# Module imports
from plane.db.models import Project
from plane.utils.markdown_storage import _sanitize_name, get_markdown_root


class Command(BaseCommand):
    help = "Rename project Markdown mirror directories from the project name to its id"

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually rename the directories. Without it the command only prints what it would do.",
        )

    def handle(self, *args, **options):
        apply = options["apply"]

        projects = (
            Project.objects.filter(deleted_at__isnull=True)
            .select_related("workspace")
            .order_by("created_at")
        )

        moved = 0
        for project in projects.iterator():
            root = get_markdown_root(project.workspace)

            # ⚠️ This rule belongs to history and to history only — it is the *old*
            # directory name, kept here so the existing directories can still be
            # found and moved. It is **not** the source of truth any more: the live
            # rule is ``_project_directory_name`` (the id). Do not copy this line
            # into anything new.
            old_name = _sanitize_name(project.name or "") or str(project.id)
            new_name = str(project.id)
            old = root / old_name
            new = root / new_name

            if old_name == new_name:
                # The name sanitized to empty, so the old rule already fell back to
                # the id — nothing to move.
                self.stdout.write(f"[skip] {project.name}: already named by id ({new_name})")
            elif not old.exists():
                # No mirror directory on disk: nothing to move (and re-running after
                # an apply lands here, which is what makes the command idempotent).
                self.stdout.write(f"[skip] {project.name}: no directory to move ({old_name})")
            elif new.exists():
                # Refuse loudly. Never merge the two trees — that would interleave
                # two projects' mirrors in one directory, the very collision the
                # rename exists to end.
                self.stdout.write(
                    self.style.ERROR(
                        f"[conflict] {project.name}: {new_name} already exists — "
                        f"refusing to merge {old_name} into it"
                    )
                )
            else:
                if apply:
                    old.rename(new)
                    self.stdout.write(f"[moved] {old_name} -> {new_name}")
                else:
                    self.stdout.write(f"[dry-run] {old_name} -> {new_name}")
                moved += 1

        verb = "Moved" if apply else "Would move"
        self.stdout.write(self.style.SUCCESS(f"{verb} {moved} project directory(ies)"))
