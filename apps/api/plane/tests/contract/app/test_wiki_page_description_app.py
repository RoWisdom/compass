# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The wiki's binary description endpoint — the collaboration server's data source.

``apps/live`` fetches a page's Yjs binary from
``${basePath}/pages/<id>/description/`` and stores it back with a PATCH. The
project pages have had that endpoint all along (``PagesDescriptionViewSet``);
the wiki had only the JSON ``WikiPageDetailSerializer``, which deliberately
omits ``description_binary``. Without this endpoint the wiki editor connects
and then fails on every fetch.

Two invariants are pinned here, both easy to lose:

1. **Sanitisation.** ``WikiPageUpdateSerializer`` inherits
   ``PageBinaryUpdateSerializer.validate_description_html``, so HTML arriving
   through this endpoint is sanitised by the same code path as everywhere else.
   This route renders through ``@plane/editor`` now, but the JSON detail route
   still serves ``description_html`` to clients, and the wiki list route is a
   second sink.

2. **The mirror is project-scoped, so pages without a project are skipped.**
   ``_write_page_mirror`` resolves a directory name from ``Project.name`` and
   ``MARKDOWN_STORAGE_PATH`` points at the projects folder itself — a page with
   no ProjectPage link has nowhere to be written. Skipping must leave the
   database write intact; a page whose markdown cannot be mirrored is still a
   page that was saved.
"""

from uuid import uuid4

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import Page, Project, ProjectMember, ProjectPage, User, WorkspaceMember

XSS_PAYLOAD = "<p>正文</p><img src=x onerror=alert(1)>"


@pytest.fixture
def wiki_page(workspace, create_user):
    """A page included in the wiki, with no project link."""
    return Page.objects.create(
        workspace=workspace,
        name="Wiki 正文页",
        owned_by=create_user,
        access=Page.PUBLIC_ACCESS,
        is_global=True,
    )


@pytest.fixture
def project(workspace, create_user):
    project = Project.objects.create(
        name="镜像项目",
        identifier="MIR",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20)
    return project


@pytest.fixture
def linked_wiki_page(workspace, project, create_user):
    """A wiki page that DOES belong to a project — the mirrorable shape.

    Today every wiki page in the real database looks like this, which is why
    the mirror path is the one that actually runs.
    """
    page = Page.objects.create(
        workspace=workspace,
        name="有项目的 Wiki 页",
        owned_by=create_user,
        access=Page.PUBLIC_ACCESS,
        is_global=True,
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
def guest(db, workspace):
    """本工作区里的一个只读成员（GUEST, role=5）。

    形状照 ``test_wiki_pages_app.py:74-95`` 的同名 fixture 抄 ——
    ``plane/tests/conftest.py`` 里**没有** guest fixture，本仓库的契约测试模块
    各自定义自己需要的 fixture。``User.username`` 是 unique=True 且 ``create_user``
    已占用了 ""，所以必须给唯一值。
    """
    unique_id = uuid4().hex[:8]
    guest_user = User.objects.create(
        email=f"guest-{unique_id}@plane.so",
        username=f"guest_{unique_id}",
        first_name="Guest",
        last_name="User",
    )
    guest_user.set_password("test-password")
    guest_user.save()
    WorkspaceMember.objects.create(workspace=workspace, member=guest_user, role=5)
    return guest_user


@pytest.fixture
def guest_client(guest):
    """以 GUEST 身份认证的客户端。

    **不要复用 ``session_client``** —— 它已经 ``force_authenticate(create_user)``
    过了。同一个 ``APIClient`` 实例上再认证一次虽然技术上会换掉身份，但那是靠
    「后一次覆盖前一次」的副作用成立，读起来像在测 create_user 被拒。
    既有 fixture 的 docstring 明确警告过这一点，照它做。
    """
    client = APIClient()
    client.force_authenticate(user=guest)
    return client


@pytest.mark.contract
class TestWikiPageDescriptionEndpoint:
    @pytest.mark.django_db
    def test_retrieve_returns_octet_stream_of_binary(self, session_client, workspace, wiki_page):
        wiki_page.description_binary = b"yjs-bytes"
        wiki_page.save(update_fields=["description_binary"])

        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/description/")

        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "application/octet-stream"
        assert b"".join(response.streaming_content) == b"yjs-bytes"

    @pytest.mark.django_db
    def test_retrieve_of_empty_binary_yields_empty_body(self, session_client, workspace, wiki_page):
        """A page whose description was never written must not 500.

        This is the state every page is in before its first collaborative edit,
        and the live server turns an empty body into a first-time conversion.
        """
        wiki_page.description_binary = None
        wiki_page.save(update_fields=["description_binary"])

        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/description/")

        assert response.status_code == status.HTTP_200_OK
        assert b"".join(response.streaming_content) == b""

    @pytest.mark.django_db
    def test_patch_writes_description_html(self, session_client, workspace, wiki_page):
        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/description/",
            {"description_html": "<p>新正文</p>", "description_json": {"type": "doc"}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        wiki_page.refresh_from_db()
        assert "新正文" in wiki_page.description_html

    @pytest.mark.django_db
    def test_patch_sanitizes_description_html(self, session_client, workspace, wiki_page):
        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/description/",
            {"description_html": XSS_PAYLOAD},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        wiki_page.refresh_from_db()
        assert "onerror" not in wiki_page.description_html
        # Positive control: a sanitizer that deleted everything would pass the
        # line above.
        assert "正文" in wiki_page.description_html

    @pytest.mark.django_db
    def test_patch_404s_for_a_page_not_in_the_wiki(self, session_client, workspace, create_user):
        """Scope comes from _wiki_page_queryset — a project page is not a wiki page."""
        outsider = Page.objects.create(
            workspace=workspace,
            name="没收录",
            owned_by=create_user,
            access=Page.PUBLIC_ACCESS,
            is_global=False,
        )

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{outsider.id}/description/",
            {"description_html": "<p>x</p>"},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_patch_400s_when_the_page_is_locked(self, session_client, workspace, wiki_page):
        wiki_page.is_locked = True
        wiki_page.save(update_fields=["is_locked"])

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/description/",
            {"description_html": "<p>x</p>"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # ``error_code`` carries the *numeric* code (``ERROR_CODES["PAGE_LOCKED"]``
        # == 4701) and the name rides in ``error_message`` — that is the payload
        # shape of the project-page sibling ``PagesDescriptionViewSet``, which
        # this endpoint mirrors field for field. Asserted as the literal wire
        # value rather than through the constant so the contract is pinned
        # against a renumbering as well.
        assert response.data["error_code"] == 4701
        assert response.data["error_message"] == "PAGE_LOCKED"

    @pytest.mark.django_db
    def test_patch_403s_for_a_guest(self, guest_client, workspace, wiki_page):
        """GUEST is read-only，与 wiki 的其它写端点一致。

        断言的是 **403**（不是 404），这一点已由既有套件核实：
        ``test_wiki_pages_app.py:1092`` 的 ``test_guest_cannot_write_page_bodies``
        对同一个 PATCH 端点断言的正是 403。``wiki_page`` 是公开页，GUEST 能看见它、
        所以过得了 ``_wiki_page_queryset`` 的可见性过滤，403 来自
        ``@allow_permission([ADMIN, MEMBER])`` —— 这一区分正是这条测试的价值。
        """
        response = guest_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/description/",
            {"description_html": "<p>guest 写的</p>"},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        wiki_page.refresh_from_db()
        assert "guest 写的" not in wiki_page.description_html

    @pytest.mark.django_db
    def test_patch_mirrors_a_page_that_belongs_to_a_project(
        self, session_client, workspace, linked_wiki_page, isolate_markdown_mirror
    ):
        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{linked_wiki_page.id}/description/",
            {"description_html": "<p>镜像我</p>"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        mirrored = list(isolate_markdown_mirror.rglob("*.md"))
        assert len(mirrored) == 1
        assert "镜像我" in mirrored[0].read_text(encoding="utf-8")

    @pytest.mark.django_db
    def test_patch_skips_the_mirror_and_still_saves_when_there_is_no_project(
        self, session_client, workspace, wiki_page, isolate_markdown_mirror
    ):
        """No ProjectPage link -> no directory to mirror into -> skip, do not guess.

        The write to the database must still happen: a page whose markdown
        cannot be mirrored is still a page the user edited.
        """
        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/description/",
            {"description_html": "<p>没有项目也要存</p>"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        wiki_page.refresh_from_db()
        assert "没有项目也要存" in wiki_page.description_html
        assert list(isolate_markdown_mirror.rglob("*.md")) == []

    @pytest.mark.django_db
    def test_patch_does_not_create_a_page_version(self, session_client, workspace, linked_wiki_page):
        """Version history is deliberately not accumulated (design §4).

        Pinned because the project-page endpoint next door DOES call
        track_page_version.delay, so a reviewer copying that block over would
        silently start collecting versions nobody asked for.
        """
        from plane.db.models import PageVersion

        before = PageVersion.objects.filter(page=linked_wiki_page).count()

        session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{linked_wiki_page.id}/description/",
            {"description_html": "<p>不进版本历史</p>"},
            format="json",
        )

        assert PageVersion.objects.filter(page=linked_wiki_page).count() == before
