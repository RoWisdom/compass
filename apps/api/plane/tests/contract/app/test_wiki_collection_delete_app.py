# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""删除集合（罗盘 Round I，设计 §4.2）。语义从 Round H 的「内容整体上浮到常规、
一个都不删」改为**连里面的页面与文件夹一起删**：

  · 集合里**每一行**（页面、文件夹、嵌套页面）都软删；
  · 集合行自己也软删（与 Round H 同）；
  · 磁盘上只删**我们自己写的**镜像，目录只在空掉时 `rmdir`；
  · 「常规」重名守卫、`?transfer_to=` / `?delete_pages=` 的 400 守卫**逐字不变**。

⚠️ 与 `test_wiki_folder_delete_app.py` 是同一套语义的两个入口，断言必须同步。
"""

import pytest
from rest_framework import status

from plane.db.models import Page, PageCollection, Project, ProjectPage, User, Workspace, WorkspaceMember

GENERAL = "常规"


def _collection(workspace, user, name, **kwargs):
    return PageCollection.objects.create(workspace=workspace, name=name, owned_by=user, **kwargs)


def _url(workspace, collection):
    return f"/api/workspaces/{workspace.slug}/page-collections/{collection.id}/"


@pytest.fixture
def no_celery(monkeypatch):
    """把软删**集合行**时那次 Celery 级联钉成空操作。

    `SoftDeleteModel.delete()` 会 `.delay()` 一个任务（`db/mixins.py:78`），而测试设置
    （`plane/settings/test.py`）**没有** `CELERY_TASK_ALWAYS_EAGER` —— 真跑就会把一条
    消息发到 226 的 RabbitMQ 上，被**生产** worker 消费。

    页面那侧**不需要**它：`_cascade_delete_pages` 走的是一条 `QuerySet.update()`，
    根本不经过 `SoftDeleteModel.delete()`。
    """
    monkeypatch.setattr("plane.db.mixins.soft_delete_related_objects.delay", lambda *args, **kwargs: None)


@pytest.mark.contract
class TestTheGeneralNameIsReserved:
    @pytest.mark.django_db
    def test_create_rejects_the_general_name(self, session_client, workspace):
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/page-collections/", {"name": GENERAL}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "name" in response.data, "错误体要与序列化器校验错误同形（前端已有兜底 toast）"
        assert not PageCollection.objects.filter(workspace=workspace, name=GENERAL).exists()

    @pytest.mark.django_db
    def test_rename_into_the_general_name_is_rejected(self, session_client, workspace, create_user):
        """改名撞车也要挡 —— 不挡的话那个集合的目录会与常规的目录**是同一个文件夹**。"""
        collection = _collection(workspace, create_user, "别名叫法")

        response = session_client.patch(_url(workspace, collection), {"name": GENERAL}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "name" in response.data
        collection.refresh_from_db()
        assert collection.name == "别名叫法", "被拒的改名不得落库"

    @pytest.mark.django_db
    def test_a_name_that_merely_contains_it_is_fine(self, session_client, workspace, create_user):
        """判据是**相等**不是包含 —— 「常规 2」是合法集合名。"""
        response = session_client.patch(
            _url(workspace, _collection(workspace, create_user, "X")), {"name": f"{GENERAL} 2"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK


def _wiki_page(workspace, user, name, **kwargs):
    return Page.objects.create(
        workspace=workspace,
        name=name,
        owned_by=user,
        access=Page.PUBLIC_ACCESS,
        is_global=True,
        description_html="<p></p>",
        description_json={},
        **kwargs,
    )


def _folder(workspace, user, name, **kwargs):
    kwargs.setdefault("node_type", Page.NODE_TYPE_FOLDER)
    return _wiki_page(workspace, user, name, **kwargs)


@pytest.fixture
def collection_tree(workspace, create_user):
    """两个集合 + 局外页面：

        集合 A
        ├── page（页面）
        └── F（文件夹）
            └── child（页面）
        集合 B
        └── other（页面）
        outside（页面，无集合）
    """
    a = _collection(workspace, create_user, "A")
    b = _collection(workspace, create_user, "B")
    page = _wiki_page(workspace, create_user, "page", collection=a)
    folder = _folder(workspace, create_user, "F", collection=a)
    child = _wiki_page(workspace, create_user, "child", parent=folder, collection=a)
    other = _wiki_page(workspace, create_user, "other", collection=b)
    outside = _wiki_page(workspace, create_user, "outside")
    return {"a": a, "b": b, "page": page, "folder": folder, "child": child, "other": other, "outside": outside}


def _deleted(row):
    row.refresh_from_db()
    return row.deleted_at is not None


@pytest.mark.contract
class TestDeletingACollectionDeletesEverythingInsideIt:
    @pytest.mark.django_db
    def test_an_empty_collection_can_be_deleted(self, session_client, workspace, create_user, no_celery):
        collection = _collection(workspace, create_user, "空集合")

        response = session_client.delete(_url(workspace, collection))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not PageCollection.objects.filter(id=collection.id).exists(), "软删行不再从默认 manager 里露出来"
        collection.refresh_from_db()
        assert collection.deleted_at is not None

    @pytest.mark.django_db
    def test_deleting_the_same_collection_twice_is_a_404(self, session_client, workspace, create_user, no_celery):
        """第二次删同一个集合 ⇒ 404（软删行已被默认 manager 过滤掉）。"""
        collection = _collection(workspace, create_user, "空集合")
        assert session_client.delete(_url(workspace, collection)).status_code == status.HTTP_204_NO_CONTENT

        assert session_client.delete(_url(workspace, collection)).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_every_row_inside_gets_a_deleted_at(self, session_client, workspace, collection_tree, no_celery):
        """核心承诺：页面、文件夹、嵌套页面**每一行**都软删。"""
        tree = collection_tree

        response = session_client.delete(_url(workspace, tree["a"]))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        for key in ("page", "folder", "child"):
            assert _deleted(tree[key]), f"{key} 应随集合一起软删"
        tree["a"].refresh_from_db()
        assert tree["a"].deleted_at is not None, "集合行自己也软删"

    @pytest.mark.django_db
    def test_the_deleted_rows_leave_the_wiki_tree_endpoint(
        self, session_client, workspace, collection_tree, no_celery
    ):
        session_client.delete(_url(workspace, collection_tree["a"]))

        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/?scope=all")
        assert response.status_code == status.HTTP_200_OK
        ids = {row["id"] for row in response.data}
        for key in ("page", "folder", "child"):
            assert str(collection_tree[key].id) not in ids, f"{key} 不该再出现在树里"

    @pytest.mark.django_db
    def test_the_other_collection_and_pages_outside_are_untouched(
        self, session_client, workspace, collection_tree, no_celery
    ):
        session_client.delete(_url(workspace, collection_tree["a"]))

        assert not _deleted(collection_tree["b"]), "B 不该被删"
        collection_tree["other"].refresh_from_db()
        assert collection_tree["other"].deleted_at is None, "B 里的页面不动"
        assert collection_tree["other"].collection_id == collection_tree["b"].id
        collection_tree["outside"].refresh_from_db()
        assert collection_tree["outside"].deleted_at is None, "本来就在常规的页面不受影响"

    @pytest.mark.django_db
    def test_a_page_that_also_belongs_to_a_project_is_deleted(
        self, session_client, workspace, create_user, no_celery
    ):
        """设计 §2 I-3 的锁（与删文件夹那条同一个形状）。"""
        collection = _collection(workspace, create_user, "A")
        page = _wiki_page(workspace, create_user, "双身份", collection=collection)
        project = Project.objects.create(name="项目", identifier="PRJ", workspace=workspace)
        ProjectPage.objects.create(workspace=workspace, project=project, page=page, created_by=create_user)

        session_client.delete(_url(workspace, collection))

        assert _deleted(page)

    @pytest.mark.django_db
    def test_the_deleted_collection_leaves_the_list(self, session_client, workspace, collection_tree, no_celery):
        session_client.delete(_url(workspace, collection_tree["a"]))

        response = session_client.get(f"/api/workspaces/{workspace.slug}/page-collections/")

        ids = {str(row["id"]) for row in response.data["collections"]}
        assert str(collection_tree["a"].id) not in ids
        assert str(collection_tree["b"].id) in ids

    @pytest.mark.django_db
    def test_the_write_refreshes_updated_at_and_does_not_stamp_updated_by(
        self, session_client, workspace, collection_tree, no_celery
    ):
        """与收录/移出/换集合同一条纪律：刷新时间戳，但不把执行者盖到「最后编辑人」上。"""
        from datetime import timedelta

        from django.utils import timezone

        stale = timezone.now() - timedelta(days=30)
        Page.objects.filter(id=collection_tree["page"].id).update(updated_at=stale, updated_by=None)

        session_client.delete(_url(workspace, collection_tree["a"]))

        collection_tree["page"].refresh_from_db()
        assert collection_tree["page"].updated_at > stale
        assert collection_tree["page"].updated_by_id is None


@pytest.mark.contract
class TestTheEndpointRefusesTheUnimplementedContract:
    @pytest.mark.django_db
    def test_transfer_to_is_rejected(self, session_client, workspace, collection_tree, no_celery):
        response = session_client.delete(
            f"{_url(workspace, collection_tree['a'])}?transfer_to={collection_tree['b'].id}"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in response.data
        collection_tree["a"].refresh_from_db()
        assert collection_tree["a"].deleted_at is None, "被拒的请求不得删掉任何东西"
        collection_tree["page"].refresh_from_db()
        assert collection_tree["page"].deleted_at is None

    @pytest.mark.django_db
    def test_delete_pages_is_rejected(self, session_client, workspace, collection_tree, no_celery):
        """`?delete_pages=false` 也 400 —— 判据是**出现**，因为出现就说明调用方
        以为那份文档化的契约存在（设计 §4.1）。"""
        response = session_client.delete(f"{_url(workspace, collection_tree['a'])}?delete_pages=false")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in response.data
        collection_tree["a"].refresh_from_db()
        assert collection_tree["a"].deleted_at is None


@pytest.mark.contract
class TestPermissions:
    @pytest.mark.django_db
    def test_a_collection_in_another_workspace_is_404(self, session_client, workspace, create_user, no_celery):
        other = Workspace.objects.create(name="别的", slug="other-del-ws", owner=create_user)
        foreign = _collection(other, create_user, "外人")

        response = session_client.delete(f"/api/workspaces/{workspace.slug}/page-collections/{foreign.id}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        foreign.refresh_from_db()
        assert foreign.deleted_at is None

    @pytest.mark.django_db
    def test_a_guest_is_forbidden(self, workspace, create_user, no_celery):
        """写端点不含 GUEST（与 `create` / `partial_update` 的权限列表逐字一致）。"""
        from rest_framework.test import APIClient

        from plane.app.permissions import ROLE

        guest = User.objects.create(email="guest-del@plane.so", username="guest-del")
        guest.set_password("test-password")
        guest.save()
        WorkspaceMember.objects.create(workspace=workspace, member=guest, role=ROLE.GUEST.value)
        client = APIClient()
        client.force_authenticate(user=guest)
        collection = _collection(workspace, create_user, "谁都能看的")

        response = client.delete(_url(workspace, collection))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        collection.refresh_from_db()
        assert collection.deleted_at is None

    @pytest.mark.django_db
    def test_a_non_member_is_forbidden(self, workspace, no_celery):
        """非工作区成员 → 403（装饰器不区分资源是否存在）。"""
        from rest_framework.test import APIClient

        outsider = User.objects.create(email="outsider-del@plane.so", username="outsider-del")
        outsider.set_password("test-password")
        outsider.save()
        client = APIClient()
        client.force_authenticate(user=outsider)
        collection = _collection(workspace, outsider, "非成员建的")

        response = client.delete(_url(workspace, collection))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        collection.refresh_from_db()
        assert collection.deleted_at is None


@pytest.mark.contract
class TestDeletingACollectionDeletesTheMirrors:
    """镜像按设计 §4.3 收尾 —— 与删文件夹那一节同一条纪律、同一份实现。"""

    @pytest.fixture
    def mirrored(self, workspace, create_user, isolate_markdown_mirror):
        """集合 A / 3-Wiki/A/ 下已经落好的镜像：

            ├── page            page.md
            └── F（文件夹）      F/child.md
        """
        from plane.app.views.page.collection import _mirror_wiki_page

        collection = _collection(workspace, create_user, "A")
        page = _wiki_page(workspace, create_user, "page", collection=collection)
        folder = _folder(workspace, create_user, "F", collection=collection)
        child = _wiki_page(workspace, create_user, "child", parent=folder, collection=collection)
        for row in (page, child):
            _mirror_wiki_page(Page.objects.get(id=row.id), f"<p>{row.name} 的正文</p>")

        root = isolate_markdown_mirror.parent / "3-Wiki"
        assert (root / "A" / "page.md").is_file(), "前置：page 的镜像在 A/ 下"
        assert (root / "A" / "F" / "child.md").is_file(), "前置：child 的镜像在 A/F/ 下"
        return {"collection": collection, "page": page, "folder": folder, "child": child, "root": root}

    @pytest.mark.django_db
    def test_our_own_files_are_deleted(self, session_client, workspace, mirrored, no_celery):
        response = session_client.delete(_url(workspace, mirrored["collection"]))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not (mirrored["root"] / "A" / "page.md").exists()
        assert not (mirrored["root"] / "A" / "F" / "child.md").exists()

    @pytest.mark.django_db
    def test_the_emptied_collection_directory_is_removed(self, session_client, workspace, mirrored, no_celery):
        session_client.delete(_url(workspace, mirrored["collection"]))

        assert not (mirrored["root"] / "A").exists(), "集合目录空了就该走"
        assert (mirrored["root"]).is_dir(), "wiki 根永不动"

    @pytest.mark.django_db
    def test_a_file_that_is_not_ours_keeps_the_directory_alive(
        self, session_client, workspace, mirrored, no_celery
    ):
        """孤儿文件（用户手写、或页面行已不在）挡着 ⇒ **不删目录**、文件一字不动。"""
        orphan = mirrored["root"] / "A" / "用户手写的.md"
        orphan_text = "---\ntags:\n  - 手写\n---\n\n这是我自己的笔记。\n"
        orphan.write_text(orphan_text, encoding="utf-8")

        session_client.delete(_url(workspace, mirrored["collection"]))

        assert (mirrored["root"] / "A").is_dir(), "非空目录必须原样留下"
        assert orphan.read_text(encoding="utf-8") == orphan_text, "孤儿文件一字未动"
        assert not (mirrored["root"] / "A" / "page.md").exists(), "我们自己的还是收掉了"

    @pytest.mark.django_db
    def test_a_read_only_vault_does_not_fail_the_delete(
        self, session_client, workspace, mirrored, no_celery, monkeypatch
    ):
        """best-effort：磁盘问题**永远不得**让一次删除失败（失败方向是「页面已删、文件残留」）。"""
        import os

        def _boom(*args, **kwargs):
            raise OSError("read-only vault")

        monkeypatch.setattr(os, "unlink", _boom)

        response = session_client.delete(_url(workspace, mirrored["collection"]))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert _deleted(mirrored["page"]), "不只是 204：行确实被软删了"
        assert (mirrored["root"] / "A" / "page.md").is_file(), "删不掉就留着"

    @pytest.mark.django_db
    def test_a_collection_with_no_mirror_directory_is_fine(
        self, session_client, workspace, create_user, isolate_markdown_mirror, no_celery
    ):
        """226 上多个集合从来没落过盘 ⇒ 「删这种集合」是常规路径，必须干净。"""
        collection = _collection(workspace, create_user, "从没落过盘的")
        _wiki_page(workspace, create_user, "页", collection=collection)

        response = session_client.delete(_url(workspace, collection))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not (isolate_markdown_mirror.parent / "3-Wiki").exists(), "不得凭空造目录"

    @pytest.mark.django_db
    def test_a_non_utf8_file_at_a_top_level_pages_mirror_path_does_not_fail_the_delete(
        self, session_client, workspace, create_user, isolate_markdown_mirror, no_celery
    ):
        """一个非 UTF-8 的文件压在顶层页面的镜像路径上，删除**不得**因此 500。

        `_frontmatter_id`（`markdown_storage.py:75-86`）只兜 `OSError`，而一个非 UTF-8
        的文件抛的是 `UnicodeDecodeError`（`ValueError`，不是 `OSError`）。
        本轮的解析发生在**两个**地方（`_wiki_mirror_target` 里的 `_resolve_page_path`，
        以及 `delete_page_file` 里的 `_frontmatter_id`），两处都得兜住。
        """
        collection = _collection(workspace, create_user, "A")
        _wiki_page(workspace, create_user, "page", collection=collection)

        root = isolate_markdown_mirror.parent / "3-Wiki"
        mirror = root / "A" / "page.md"
        mirror.parent.mkdir(parents=True, exist_ok=True)
        mirror.write_bytes(b"\xff\xfe\x00\x01 not utf-8")

        response = session_client.delete(_url(workspace, collection))

        assert response.status_code == status.HTTP_204_NO_CONTENT
