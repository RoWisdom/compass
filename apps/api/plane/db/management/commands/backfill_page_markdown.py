# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# One-off backfill: mirror the content of every existing page as a local Markdown
# file, using the same naming / frontmatter / hierarchy logic as the live
# dual-write. A page linked to several projects is mirrored once per project.

# Django imports
from django.core.management.base import BaseCommand

# Module imports
from plane.db.models import FileAsset, Page, ProjectPage, User
from plane.utils.html_to_markdown import html_to_markdown
from plane.utils.markdown_storage import write_page_markdown


class Command(BaseCommand):
    help = "Backfill local Markdown mirrors for existing page content"

    def add_arguments(self, parser):
        parser.add_argument(
            "--workspace-slug",
            default=None,
            help="Restrict the backfill to a single workspace (e.g. 926).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be written without touching the filesystem.",
        )

    def handle(self, *args, **options):
        workspace_slug = options["workspace_slug"]
        dry_run = options["dry_run"]

        # Map each page to the projects it is (still) linked to.
        links = ProjectPage.objects.filter(deleted_at__isnull=True).select_related("project")
        if workspace_slug:
            links = links.filter(project__workspace__slug=workspace_slug)

        project_index = {}
        page_ids = []
        for link in links.iterator():
            project_index.setdefault(str(link.page_id), []).append((str(link.project_id), link.project.name))
            page_ids.append(link.page_id)

        pages = Page.objects.filter(id__in=page_ids, archived_at__isnull=True).order_by(
            "workspace__slug", "created_at"
        )

        # id -> (name, parent_id) for ancestor resolution without N+1 queries.
        page_index = {str(p.id): (p.name or "", p.parent_id) for p in pages}

        # Resolve @-mention display names lazily, caching per user id.
        user_cache = {}

        def resolve_user(user_id):
            if user_id not in user_cache:
                user = User.objects.filter(pk=user_id).first()
                name = None
                if user:
                    name = user.display_name or (user.first_name + " " + user.last_name).strip() or user.email
                user_cache[user_id] = name or None
            return user_cache[user_id]

        # Resolve image-component src (asset id) to an asset URL, caching per id.
        asset_cache = {}

        def resolve_asset_url(asset_id):
            if asset_id not in asset_cache:
                asset = FileAsset.objects.filter(pk=asset_id).first()
                asset_cache[asset_id] = asset.asset_url if asset else None
            return asset_cache[asset_id]

        written = 0
        for page in pages.iterator():
            markdown = html_to_markdown(
                page.description_html,
                resolve_user=resolve_user,
                resolve_asset_url=resolve_asset_url,
            )

            # Walk the parent chain upward (root-first) to mirror the hierarchy.
            ancestors = []
            parent_id = page.parent_id
            depth = 0
            while parent_id and depth < 20:
                entry = page_index.get(str(parent_id))
                if entry is None:
                    break
                ancestors.append((entry[0], str(parent_id)))
                parent_id = entry[1]
                depth += 1
            ancestors.reverse()

            for project_id, project_name in project_index.get(str(page.id), []):
                if dry_run:
                    prefix = "/".join(a[0] for a in ancestors)
                    self.stdout.write(
                        f"[dry-run] {project_name}/{prefix + '/' if prefix else ''}"
                        f"{page.name or str(page.id)} -> {len(markdown)} chars"
                    )
                else:
                    write_page_markdown(
                        project_name=project_name,
                        project_id=project_id,
                        ancestors=ancestors,
                        page_id=str(page.id),
                        name=page.name,
                        markdown=markdown,
                    )
            written += 1

        verb = "Would process" if dry_run else "Processed"
        self.stdout.write(self.style.SUCCESS(f"{verb} {written} page(s)"))
