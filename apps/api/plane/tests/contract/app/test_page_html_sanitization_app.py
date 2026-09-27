# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""``description_html`` must be sanitized on **every** write path.

The wiki detail route — ``apps/web/app/(all)/[workspaceSlug]/(projects)/wiki/
[pageId]/page.tsx`` — renders ``description_html`` with
``dangerouslySetInnerHTML``. It is the only raw-HTML sink in the frontend
(``grep -rn dangerouslySetInnerHTML apps/web packages/editor packages/ui``),
and the branch introduced it. Everything else renders through
``@plane/editor``, which is inert to unknown attributes and event handlers.

Three write paths reach that field, and all three must run the value through
``plane.utils.content_validator.validate_html_content``:

1. ``PageBinaryUpdateSerializer.validate_description_html`` — the project-page
   body PATCH, inherited by the wiki PATCH (``WikiPageUpdateSerializer``).
2. ``PageDetailSerializer.validate_description_html`` — the serializer
   ``PageViewSet.partial_update`` uses. **Was missing; this branch added it.**
3. ``PageSerializer.create`` — the project-page POST. ``description_html``
   travels via ``self.context``, *not* as a serializer field, so a
   ``validate_description_html`` hook never runs for this path; the sanitizer
   has to be called explicitly inside ``create()``. **Checked here because a
   ``validate_*`` method would be silently dead code.**

A single unsanitized path stores XSS that the wiki route then executes for
every workspace member who opens the page — ADMIN included. Any one of these
tests going red means the sink is live again.
"""

import pytest
from rest_framework import status

from plane.db.models import (
    Page,
    Project,
    ProjectMember,
    ProjectPage,
)

# An ``onerror`` handler: nh3 keeps ``<img>`` (and its ``src``) but drops the
# event handler attribute, so "onerror is gone" and "the payload's benign part
# survived" can both be asserted.
XSS_PAYLOAD = "<p>正文</p><img src=x onerror=alert(1)>"

# Positive control. If the sanitizer were replaced by something that deletes
# everything, the two XSS assertions above would still pass — this payload is
# what stops that.
SAFE_PAYLOAD = "<p>段落</p><strong>加粗</strong><ul><li>条目一</li></ul>"


@pytest.fixture
def project(db, workspace, create_user):
    """The caller's own project (role=20 → ADMIN, so POST/PATCH are allowed)."""
    project = Project.objects.create(
        name="Sanitize Project",
        identifier="SP",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20)
    return project


@pytest.fixture
def project_page(db, workspace, project, create_user):
    """A page linked to ``project`` — the link is what the permission check and
    ``PageViewSet.partial_update``'s lookup both resolve through."""
    page = Page.objects.create(
        workspace=workspace,
        name="待消毒页面",
        owned_by=create_user,
        access=Page.PUBLIC_ACCESS,
    )
    ProjectPage.objects.create(
        workspace=workspace,
        project=project,
        page=page,
        created_by_id=create_user.id,
        updated_by_id=create_user.id,
    )
    return page


@pytest.fixture
def wiki_page(db, workspace, create_user):
    """A page already included in the wiki — used for the shared-path check."""
    return Page.objects.create(
        workspace=workspace,
        name="Wiki 页面",
        owned_by=create_user,
        access=Page.PUBLIC_ACCESS,
        is_global=True,
    )


@pytest.mark.contract
class TestPageHtmlSanitization:
    @pytest.mark.django_db
    def test_create_sanitizes_description_html(self, session_client, workspace, project):
        """Path 3 — ``PageSerializer.create``.

        ``description_html`` is read straight out of ``request.data`` by the view
        and handed to ``Page.objects.create`` via the serializer context. The
        page must still be created (the sanitizer strips, it does not reject),
        and what lands in the column must be clean.
        """
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/",
            {"name": "XSS create", "description_html": XSS_PAYLOAD},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        page = Page.objects.get(id=response.data["id"])
        assert "onerror" not in page.description_html
        # The benign half of the payload must survive — an implementation that
        # writes nothing (or writes an empty string) would pass the line above.
        assert "正文" in page.description_html

    @pytest.mark.django_db
    def test_create_rejects_when_sanitization_fails(self, session_client, workspace, project, monkeypatch):
        """Path 3, failure branch — a sanitizer that cannot sanitize must **not**
        fall through to storing the raw value.

        ``validate_html_content`` signals failure as
        ``(False, message, None)`` — the sanitized value and the verdict arrive
        together, so a "use it only if it isn't None" guard silently keeps the
        *unsanitized* input. That is strictly worse than rejecting: it is the
        exact stored-XSS outcome path 3 exists to prevent.

        The three tests above cannot reach this branch over HTTP: it takes
        either >10MB of HTML (``DATA_UPLOAD_MAX_MEMORY_SIZE`` is 5MB, so Django
        400s before the view runs) or ``nh3`` itself raising. The validator is
        patched to force it, which is why this is a unit-level contract rather
        than an end-to-end one.
        """
        monkeypatch.setattr(
            "plane.app.serializers.page.validate_html_content",
            lambda _value: (False, "Failed to sanitize HTML", None),
        )

        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/",
            {"name": "消毒失败", "description_html": XSS_PAYLOAD},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # Rejecting means nothing was written — not "the page exists but is empty".
        assert not Page.objects.filter(name="消毒失败").exists()

    @pytest.mark.django_db
    def test_partial_update_sanitizes_description_html(self, session_client, workspace, project, project_page):
        """Path 2 — ``PageDetailSerializer`` / ``PageViewSet.partial_update``.

        This is the serializer the review flagged: a bare
        ``serializers.CharField()`` with no ``validate_description_html``.
        """
        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{project_page.id}/",
            {"description_html": XSS_PAYLOAD},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        project_page.refresh_from_db()
        assert "onerror" not in project_page.description_html
        assert "正文" in project_page.description_html

    @pytest.mark.django_db
    def test_normal_content_survives_sanitization(self, session_client, workspace, project):
        """Positive control — the sanitizer must not be a delete-everything.

        Without this, both XSS tests above would go green on a
        ``validate_html_content`` that returned ``""``.
        """
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/",
            {"name": "正常内容", "description_html": SAFE_PAYLOAD},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        page = Page.objects.get(id=response.data["id"])
        assert "<p>段落</p>" in page.description_html
        assert "<strong>加粗</strong>" in page.description_html
        assert "<ul>" in page.description_html
        assert "<li>条目一</li>" in page.description_html

    @pytest.mark.django_db
    def test_wiki_own_write_path_still_sanitizes(self, session_client, workspace, wiki_page):
        """Regression guard on path 1, which this branch did **not** change.

        ``WikiPageUpdateSerializer`` inherits
        ``PageBinaryUpdateSerializer.validate_description_html``. The wiki's own
        PATCH is the safe path the false comment on the sink generalised from;
        pin it so a future edit to the shared serializer cannot quietly strip
        the guard while the two new tests above keep passing.
        """
        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/",
            {"description_html": XSS_PAYLOAD},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        wiki_page.refresh_from_db()
        assert "onerror" not in wiki_page.description_html
        assert "正文" in wiki_page.description_html
