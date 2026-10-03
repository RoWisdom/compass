# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""换位置 ⇒ 镜像跟着换目录（罗盘 Round E，设计 §4.4）。

镜像路径 = **集合名 / 祖先链 / 文件名**（设计 §2.2）。换 `parent` 改的正是中间那段，
而 `partial_update` 今天**根本不调用** `_move_wiki_page_mirror`（闸门只看名字与集合），
于是 DB 里的路径变了、vault 里的目录**一动不动**。

`isolate_markdown_mirror` 是 autouse（`plane/tests/conftest.py:20`）：项目根 =
`markdown-mirror`，wiki 根 = `markdown-mirror.parent / "3-Wiki"`。

**不测「后代」那一段**：节点自己的**同名目录**会被 `_move_page_file` 整体搬走
（`markdown_storage.py:289-297`），后代住在里面，今天就已经跟着走了
（`test_wiki_mirror_collection_move_app.py` 里那几条搬集合的测试是那一段的锁）。
"""

import pytest
from rest_framework import status

from plane.db.models import Page, PageCollection

WIKI = "3-Wiki"


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


def _collection(workspace, user, name):
    return PageCollection.objects.create(workspace=workspace, name=name, owned_by=user)


def _write_mirror(page, html="<p>正文</p>"):
    """用**写侧同一个函数**把镜像落盘 —— 不自己拼路径。"""
    from plane.app.views.page.collection import _mirror_wiki_page

    _mirror_wiki_page(page, html)


def _wiki_root(isolate_markdown_mirror):
    return isolate_markdown_mirror.parent / WIKI


def _move(client, workspace, page, payload):
    return client.patch(f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/", payload, format="json")


@pytest.mark.contract
class TestReparentMovesTheMirror:
    @pytest.mark.django_db
    def test_moving_a_folder_to_another_collection_takes_its_directory(
        self, session_client, isolate_markdown_mirror, workspace, create_user
    ):
        """文件夹 A 从集合 X 移到集合 Y ⇒ 它在 vault 里的目录整体跟着走。"""
        root = _wiki_root(isolate_markdown_mirror)
        x = _collection(workspace, create_user, "X")
        y = _collection(workspace, create_user, "Y")
        a = _folder(workspace, create_user, "A", collection=x)
        t1 = _wiki_page(workspace, create_user, "t1", parent=a, collection=x)

        _write_mirror(t1)
        assert (root / "X" / "A" / "t1.md").is_file(), "前置：镜像先落在 X/A/ 下"

        response = _move(session_client, workspace, a, {"collection_id": str(y.id)})
        assert response.status_code == status.HTTP_200_OK

        assert (root / "Y" / "A" / "t1.md").is_file(), "文件夹目录必须跟着集合搬"
        assert not (root / "X" / "A").exists(), "旧集合下不得留残骸"

    @pytest.mark.django_db
    def test_moving_a_page_into_a_folder_moves_its_file(
        self, session_client, isolate_markdown_mirror, workspace, create_user
    ):
        """集合顶层的页面移进文件夹 ⇒ 文件从集合根下移进文件夹目录。"""
        root = _wiki_root(isolate_markdown_mirror)
        c = _collection(workspace, create_user, "C")
        folder = _folder(workspace, create_user, "夹", collection=c)
        page = _wiki_page(workspace, create_user, "页", collection=c)

        _write_mirror(page)
        assert (root / "C" / "页.md").is_file(), "前置：先落在集合根下"

        response = _move(session_client, workspace, page, {"parent": str(folder.id)})
        assert response.status_code == status.HTTP_200_OK

        assert (root / "C" / "夹" / "页.md").is_file(), "文件必须进到文件夹目录里"
        assert not (root / "C" / "页.md").exists(), "旧位置不得留残骸"

    @pytest.mark.django_db
    def test_reparenting_inside_one_collection_moves_the_directory(
        self, session_client, isolate_markdown_mirror, workspace, create_user
    ):
        """同一集合内换父：集合名那一段不变，变的是祖先段 —— 也要搬。"""
        root = _wiki_root(isolate_markdown_mirror)
        c = _collection(workspace, create_user, "C")
        a = _folder(workspace, create_user, "A", collection=c)
        z = _folder(workspace, create_user, "Z", collection=c)
        t1 = _wiki_page(workspace, create_user, "t1", parent=a, collection=c)

        _write_mirror(t1)
        assert (root / "C" / "A" / "t1.md").is_file(), "前置"

        response = _move(session_client, workspace, a, {"parent": str(z.id)})
        assert response.status_code == status.HTTP_200_OK

        assert (root / "C" / "Z" / "A" / "t1.md").is_file(), "祖先段变了，目录要跟着搬"
        assert not (root / "C" / "A").exists(), "旧祖先段下不得留残骸"

    @pytest.mark.django_db
    def test_moving_a_page_to_the_collection_root_pulls_its_file_out(
        self, session_client, isolate_markdown_mirror, workspace, create_user
    ):
        """反向：从文件夹里挪回集合顶层。"""
        root = _wiki_root(isolate_markdown_mirror)
        c = _collection(workspace, create_user, "C")
        folder = _folder(workspace, create_user, "夹", collection=c)
        page = _wiki_page(workspace, create_user, "页", parent=folder, collection=c)

        _write_mirror(page)
        assert (root / "C" / "夹" / "页.md").is_file(), "前置"

        response = _move(session_client, workspace, page, {"parent": None})
        assert response.status_code == status.HTTP_200_OK

        assert (root / "C" / "页.md").is_file(), "文件必须回到集合根下"
        assert not (root / "C" / "夹" / "页.md").exists(), "旧位置不得留残骸"

    @pytest.mark.django_db
    def test_a_rename_still_moves_as_before(
        self, session_client, isolate_markdown_mirror, workspace, create_user
    ):
        """护栏：加了 `old_ancestors` 参数之后，改名那条既有路径**行为逐字不变**。"""
        root = _wiki_root(isolate_markdown_mirror)
        c = _collection(workspace, create_user, "C")
        page = _wiki_page(workspace, create_user, "旧名", collection=c)

        _write_mirror(page)
        assert (root / "C" / "旧名.md").is_file()

        response = _move(session_client, workspace, page, {"name": "新名"})
        assert response.status_code == status.HTTP_200_OK

        assert (root / "C" / "新名.md").is_file()
        assert not (root / "C" / "旧名.md").exists()
