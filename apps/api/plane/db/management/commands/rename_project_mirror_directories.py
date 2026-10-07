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

        projects = list(
            Project.objects.filter(deleted_at__isnull=True)
            .select_related("workspace")
            .order_by("created_at")
        )

        # The old rule was ``_sanitize_name(name) or id`` — and sanitising runs
        # *after* the ``(name, workspace)`` uniqueness the database enforces, so two
        # legal projects can share one old directory name and their mirrors are then
        # interleaved inside that single directory. Moving it into either project's
        # id would put the *other* project's pages inside an AI member's sandbox
        # island — the very thing this rename exists to prevent. So a shared old name
        # is refused, never arbitrated. (Keyed by resolved root, so two workspaces
        # that resolve to different roots never share a key — and two that fall back
        # to the same env root correctly do.)
        #
        # ⚠️ That rule belongs to history and to history only. It is here to *find*
        # the existing directories; it is **not** the source of truth any more — the
        # live rule is ``_project_directory_name`` (the id). Do not copy the line
        # below into anything new.
        shared = {}
        live = []
        for project in projects:
            root = get_markdown_root(project.workspace)
            old_name = _sanitize_name(project.name or "") or str(project.id)
            new_name = str(project.id)
            if old_name == new_name:
                # The name sanitized to empty, so the old rule already fell back to
                # the id — nothing to move.
                self.stdout.write(f"[skip] {project.name}: already named by id ({new_name})")
                continue
            live.append((project, root, old_name, new_name))
            shared[(str(root), old_name)] = shared.get((str(root), old_name), 0) + 1

        moved = 0
        refused = 0
        for project, root, old_name, new_name in live:
            old = root / old_name
            new = root / new_name

            if not old.exists():
                # No mirror directory on disk: nothing to move (and re-running after
                # an apply lands here, which is what makes the command idempotent).
                self.stdout.write(f"[skip] {project.name}: no directory to move ({old_name})")
            elif shared[(str(root), old_name)] > 1:
                # Never arbitrate this by ``created_at``: whichever project won, the
                # other project's pages would end up inside its island. Leave the
                # shared tree alone and let a human split it.
                self.stdout.write(
                    self.style.ERROR(
                        f"[shared] {project.name}: {old_name} is shared by "
                        f"{shared[(str(root), old_name)]} projects — refusing to move it "
                        f"into one project's island; split it by hand"
                    )
                )
                refused += 1
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
                refused += 1
            else:
                if apply:
                    old.rename(new)
                    self.stdout.write(f"[moved] {old_name} -> {new_name}")
                else:
                    self.stdout.write(f"[dry-run] {old_name} -> {new_name}")
                moved += 1

        verb = "Moved" if apply else "Would move"
        tally = f"{verb} {moved} project directory(ies)"
        if refused:
            tally += f", refused {refused}"
        self.stdout.write(self.style.SUCCESS(tally))
