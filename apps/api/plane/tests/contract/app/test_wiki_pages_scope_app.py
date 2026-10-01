# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""``GET wiki-pages/?scope=all`` —— 侧栏那棵树的数据源。

侧栏要一次拿到**所有**分区的页面才建得出跨分区的导航树，而既有的列表分支
一次只取一个分区。这里锁住三件事：

1. `scope=all` 返回全部已收录、且对调用者可见的页面，每行带 `collection_key`；
2. `collection_key` 是**服务端**算的（`resolve_collection_key`），不是前端复刻的；
3. **不带 `scope` 时行为逐字不变** —— `wiki-list-root.tsx` 那条按集合取数的路径
   不能被这次改动碰到，这是硬要求。
"""

from uuid import uuid4

import pytest
from rest_framework import status

from plane.db.models import Page, PageCollection, User


@pytest.fixture
def other_user(db):
    unique_id = uuid4().hex[:8]
    user = User.objects.create(
        email=f"other-{unique_id}@plane.so",
        username=f"other_{unique_id}",
        first_name="Other",
        last_name="User",
    )
    user.set_password("test-password")
    user.save()
    return user


@pytest.fixture
def pages(workspace, create_user, other_user):
    """四个页面，一个分区一个 —— 覆盖 `resolve_collection_key` 的全部四条分支。"""
    collection = PageCollection.objects.create(workspace=workspace, name="我的集合", owned_by=create_user)
    return {
        "general": Page.objects.create(
            name="公开页", workspace=workspace, owned_by=create_user, is_global=True,
            description_html="<p></p>", description_json={},
        ),
        "private": Page.objects.create(
            name="私有页", workspace=workspace, owned_by=create_user, is_global=True,
            access=Page.PRIVATE_ACCESS, description_html="<p></p>", description_json={},
        ),
        "archived": Page.objects.create(
            name="归档页", workspace=workspace, owned_by=create_user, is_global=True,
            archived_at="2026-01-01", description_html="<p></p>", description_json={},
        ),
        "collection": Page.objects.create(
            name="集合页", workspace=workspace, owned_by=create_user, is_global=True,
            collection=collection, description_html="<p></p>", description_json={},
        ),
        "collection_id": str(collection.id),
    }


@pytest.mark.contract
class TestWikiPagesScopeAll:
    @pytest.mark.django_db
    def test_returns_pages_from_every_partition(self, session_client, workspace, pages):
        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/", {"scope": "all"})

        assert response.status_code == status.HTTP_200_OK
        assert {str(row["id"]) for row in response.data} == {
            str(pages["general"].id),
            str(pages["private"].id),
            str(pages["archived"].id),
            str(pages["collection"].id),
        }

    @pytest.mark.django_db
    def test_carries_the_server_computed_partition_key(self, session_client, workspace, pages):
        """分区键由服务端给（设计 B-6）——优先级是 archived > private > 集合 > general。"""
        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/", {"scope": "all"})

        keys = {str(row["id"]): row["collection_key"] for row in response.data}
        assert keys[str(pages["general"].id)] == "general"
        assert keys[str(pages["private"].id)] == "private"
        assert keys[str(pages["archived"].id)] == "archived"
        assert keys[str(pages["collection"].id)] == pages["collection_id"]

    @pytest.mark.django_db
    def test_without_scope_the_response_is_unchanged(self, session_client, workspace, pages):
        """**向后兼容是硬要求**：不带 `scope` 时既不能多字段、也不能变语义。

        `wiki-list-root.tsx` 的 `fetchPages(workspaceSlug, collection)` 走的就是这条，
        且它按 `collection` 默认 `general` 取。多出来的 `collection_key` 会顺着
        `WorkspacePage` 的 `mutateProperties` 被写成一个没人认识的属性 —— 所以
        `scope=all` 必须是**另一个序列化器**，不能让默认路径也带上它。
        """
        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/")

        assert response.status_code == status.HTTP_200_OK
        assert [str(row["id"]) for row in response.data] == [str(pages["general"].id)]
        assert "collection_key" not in response.data[0]

    @pytest.mark.django_db
    def test_scope_all_hides_other_peoples_private_pages(self, session_client, workspace, other_user):
        """可见性口径必须与计数端点**同一套**（设计 §3.4）。

        侧栏树（`scope=all`）与集合计数（`page-collections/`）若用了不同的可见性口径，
        就会出现本仓明确讨厌过的那类 bug：「私有(5) 但列表 2 行」。
        """
        Page.objects.create(
            name="别人的私密页", workspace=workspace, owned_by=other_user, is_global=True,
            access=Page.PRIVATE_ACCESS, description_html="<p></p>", description_json={},
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/", {"scope": "all"})
        assert response.data == []

    @pytest.mark.django_db
    def test_scope_all_excludes_pages_that_are_not_included(self, session_client, workspace, create_user):
        """`is_global=False` 的页面不进树 —— 与列表分支同一条 `_wiki_page_queryset`。"""
        Page.objects.create(
            name="未收录页", workspace=workspace, owned_by=create_user, is_global=False,
            description_html="<p></p>", description_json={},
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/", {"scope": "all"})
        assert response.data == []

    @pytest.mark.django_db
    def test_partition_sizes_match_the_collection_counts(self, session_client, workspace, pages):
        """**口径对账**（设计 §6 验证项 5）：树里每个分区的条数 == 该分区的计数。

        这是设计 §3.4 那条「口径一致性」的机器化版本 —— 两个端点的可见性口径
        一旦分叉，这条会先红。
        """
        tree = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/", {"scope": "all"})
        counts = session_client.get(f"/api/workspaces/{workspace.slug}/page-collections/")

        per_partition = {}
        for row in tree.data:
            per_partition[row["collection_key"]] = per_partition.get(row["collection_key"], 0) + 1

        predefined = {item["key"]: item["page_count"] for item in counts.data["predefined"]}
        for key in ("general", "private", "archived"):
            assert per_partition.get(key, 0) == predefined[key], f"{key} 分区两边对不上"

        row = next(item for item in counts.data["collections"] if str(item["id"]) == pages["collection_id"])
        assert per_partition.get(pages["collection_id"], 0) == row["page_count"]
