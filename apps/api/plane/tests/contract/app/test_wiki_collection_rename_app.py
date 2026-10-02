# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""集合改名：vault 里的目录跟着改，`external_id` 的路径前缀跟着重写。

集合的名字同时存在于**三个**地方 —— `PageCollection.name`、vault 里的目录
`3-Wiki/<集合名>/`（`wiki_page_markdown_path` 的第一段）、以及导入时记进
`Page.external_id` / `PageCollection.external_id` 的 vault 相对路径前缀
（`import_wiki_markdown.py:79/:124`）。只动第一个会有两条后果：

1. 下一次写正文按**新**名字算路径 ⇒ 凭空多建一个 `3-Wiki/<新名>/`，旧目录的文件
   留在原地，同一个集合的文件散在两处；
2. `_wiki_page_own_path`（`collection.py`）用 `external_id` 认「本页自己的来源文件」，
   而 `_resolve_page_path` 只在 `path == own_path` 时才把无 `id:` 的文件算作我们的。
   目录一改、`external_id` 还指着旧路径 ⇒ 导入页（vault 里 27 篇有 24 篇没有 `id:` 行）
   的现有文件不再被认作自己的 ⇒ 下次保存写 `X-<id8>.md`，**原文件从此变陈旧**。

`isolate_markdown_mirror` 把两棵镜像根都钉进 tmp_path：项目根 = `markdown-mirror`，
wiki 根 = `3-Wiki`（tmp_path 下的**兄弟**目录）。vault 根 = wiki 根的 parent，
所以 `Page.external_id`（vault 相对路径，如 `3-Wiki/C/X.md`）在这里就落在 tmp_path 下。
"""

import pytest
from rest_framework import status

from plane.db.models import Page, PageCollection

WIKI = "3-Wiki"


def _wiki_page(workspace, user, name, **kwargs):
    """A public page included in the wiki — the shape the metadata route accepts."""
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


def _collection(workspace, user, name, **kwargs):
    return PageCollection.objects.create(workspace=workspace, name=name, owned_by=user, **kwargs)


def _wiki_root(isolate_markdown_mirror):
    return isolate_markdown_mirror.parent / WIKI


def _rename(client, workspace, collection, new_name):
    """Rename a collection through the route the sidebar PATCHes."""
    return client.patch(
        f"/api/workspaces/{workspace.slug}/page-collections/{collection.id}/",
        {"name": new_name},
        format="json",
    )


def _save_body(client, workspace, page, html="<p>Plane 正文</p>"):
    """Edit a wiki page's body through the description route (what the editor hits)."""
    return client.patch(
        f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/description/",
        {"description_html": html},
        format="json",
    )


@pytest.mark.contract
class TestWikiCollectionRename:
    @pytest.mark.django_db
    def test_rename_moves_the_vault_directory(self, session_client, isolate_markdown_mirror, workspace, create_user):
        """集合改名 ⇒ `3-Wiki/<旧名>/` 整体搬成 `3-Wiki/<新名>/`。

        子目录（`C/子目录/Y.md`）跟着一起走 —— 搬的是目录本身，不是逐文件挑。
        内容逐字不变：搬移是 `os.replace`，不是重写。
        """
        root = _wiki_root(isolate_markdown_mirror)
        collection = _collection(workspace, create_user, "C")
        first = root / "C" / "X.md"
        second = root / "C" / "子目录" / "Y.md"
        second.parent.mkdir(parents=True, exist_ok=True)
        first.write_text("---\ntags:\n  - 罗盘\n---\n\n第一篇\n", encoding="utf-8")
        second.write_text("第二篇\n", encoding="utf-8")
        first_before, second_before = first.read_text(encoding="utf-8"), second.read_text(encoding="utf-8")

        response = _rename(session_client, workspace, collection, "D")
        assert response.status_code == status.HTTP_200_OK

        assert (root / "D").is_dir(), "新目录必须存在"
        assert (root / "D" / "X.md").read_text(encoding="utf-8") == first_before, "根下那篇逐字不变"
        assert (root / "D" / "子目录" / "Y.md").read_text(encoding="utf-8") == second_before, "子目录那篇也跟着走"
        assert not (root / "C").exists(), "旧目录必须整体消失，不留空壳"

    @pytest.mark.django_db
    def test_rename_rewrites_external_id_prefix(self, session_client, isolate_markdown_mirror, workspace, create_user):
        """回归锁：改名后导入页仍被认作自己的 —— `external_id` 前缀重写。

        `external_id` 是**定位符**：源文件跟着文件夹搬了，记下的路径就得跟着搬。
        不重写的话，下一篇正文保存认不出这个无 `id:` 的现有文件是本页的 ⇒ 写成
        `X-<id8>.md` 兄弟文件，原文件从此变陈旧。这条锁的就是「不再散开」。
        """
        root = _wiki_root(isolate_markdown_mirror)
        collection = _collection(
            workspace, create_user, "C", external_source="obsidian-vault", external_id=f"{WIKI}/C"
        )
        note = root / "C" / "X.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("---\ntags:\n  - 罗盘\n---\n\n用户正文\n", encoding="utf-8")

        page = _wiki_page(
            workspace,
            create_user,
            "X",
            collection=collection,
            external_id=f"{WIKI}/C/X.md",
            external_source="obsidian-vault",
        )

        response = _rename(session_client, workspace, collection, "D")
        assert response.status_code == status.HTTP_200_OK

        page.refresh_from_db()
        collection.refresh_from_db()
        assert page.external_id == f"{WIKI}/D/X.md", "页面行自己的前缀要重写"
        assert collection.external_id == f"{WIKI}/D", "集合行自己的前缀（精确形态）要重写"
        assert (root / "D" / "X.md").is_file(), "文件跟着目录搬了"

        # 关键一步：改名之后保存正文，必须**原地合并**进 3-Wiki/D/X.md。
        response = _save_body(session_client, workspace, page)
        assert response.status_code == status.HTTP_200_OK

        merged = root / "D" / "X.md"
        assert merged.is_file(), "本页自己的来源文件仍在原位"
        text = merged.read_text(encoding="utf-8")
        assert "  - 罗盘" in text, "剪藏的 tags 不得丢"
        assert f"id: {page.id}" in text, "Plane 的三个键写进去了"
        assert text.rstrip().endswith("Plane 正文"), "正文换成新写的"

        suffixed = root / "D" / f"X-{str(page.id)[:8]}.md"
        assert not suffixed.exists(), "这就是本页自己的来源文件，不该另起一份"

    @pytest.mark.django_db
    def test_rename_leaves_a_similar_named_collection_alone(
        self, session_client, isolate_markdown_mirror, workspace, create_user
    ):
        """段边界：改 `C` 不得碰 `C2`（判据是整段匹配，不是裸 `startswith`）。"""
        root = _wiki_root(isolate_markdown_mirror)
        collection = _collection(workspace, create_user, "C", external_source="obsidian-vault", external_id=f"{WIKI}/C")
        sibling = _collection(
            workspace, create_user, "C2", external_source="obsidian-vault", external_id=f"{WIKI}/C2"
        )
        sibling_page = _wiki_page(
            workspace,
            create_user,
            "Z",
            collection=sibling,
            external_id=f"{WIKI}/C2/Z.md",
            external_source="obsidian-vault",
        )
        sibling_note = root / "C2" / "Z.md"
        sibling_note.parent.mkdir(parents=True, exist_ok=True)
        sibling_note.write_text("C2 的镜像\n", encoding="utf-8")

        response = _rename(session_client, workspace, collection, "D")
        assert response.status_code == status.HTTP_200_OK

        sibling.refresh_from_db()
        sibling_page.refresh_from_db()
        collection.refresh_from_db()
        assert collection.external_id == f"{WIKI}/D"
        assert sibling.external_id == f"{WIKI}/C2", "同前缀的兄弟集合不得被改到"
        assert sibling_page.external_id == f"{WIKI}/C2/Z.md", "兄弟集合下的页面也不得被改到"
        assert (root / "C2" / "Z.md").is_file(), "兄弟集合的目录不得被搬走"

    @pytest.mark.django_db
    def test_rename_does_not_touch_the_destination_when_it_already_exists(
        self, session_client, isolate_markdown_mirror, workspace, create_user
    ):
        """目标目录已存在 ⇒ **拒绝搬**（合并两个目录是破坏，不是改名该做的事）。

        失败方向永远是「目录没搬」，绝不是「目录被覆盖/合并」。
        """
        root = _wiki_root(isolate_markdown_mirror)
        collection = _collection(workspace, create_user, "C")
        source = root / "C" / "X.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("C 的镜像\n", encoding="utf-8")
        source_before = source.read_text(encoding="utf-8")

        handwritten = root / "D" / "手写.md"
        handwritten.parent.mkdir(parents=True, exist_ok=True)
        handwritten.write_text("用户手写正文\n", encoding="utf-8")
        handwritten_before = handwritten.read_text(encoding="utf-8")

        response = _rename(session_client, workspace, collection, "D")
        assert response.status_code == status.HTTP_200_OK

        assert source.is_file(), "目标已存在时来源必须留在原地"
        assert source.read_text(encoding="utf-8") == source_before, "旧文件内容不变"
        assert handwritten.read_text(encoding="utf-8") == handwritten_before, "目标里那篇手写文件一字不得动"
        assert not (root / "D" / "X.md").exists(), "不得把旧目录的内容并进目标目录"

    @pytest.mark.django_db
    def test_rename_without_a_mirror_directory_is_a_noop(
        self, session_client, isolate_markdown_mirror, workspace, create_user
    ):
        """vault 里没有 `3-Wiki/C/`：改名照样成功，不造目录 —— 但 `external_id` 照改。

        `external_id` 的重写是纯 DB 的事，与磁盘上目录在不在无关：源文件可能只是还没
        落盘，记下的定位符仍要指向新名字。
        """
        root = _wiki_root(isolate_markdown_mirror)
        collection = _collection(workspace, create_user, "C", external_source="obsidian-vault", external_id=f"{WIKI}/C")
        page = _wiki_page(
            workspace,
            create_user,
            "X",
            collection=collection,
            external_id=f"{WIKI}/C/X.md",
            external_source="obsidian-vault",
        )
        assert not (root / "C").exists()

        response = _rename(session_client, workspace, collection, "D")
        assert response.status_code == status.HTTP_200_OK

        page.refresh_from_db()
        collection.refresh_from_db()
        assert page.external_id == f"{WIKI}/D/X.md", "纯 DB 的前缀重写与磁盘无关"
        assert collection.external_id == f"{WIKI}/D"
        assert not (root / "C").exists(), "不得凭空造出旧目录"
        assert not (root / "D").exists(), "也不得凭空造出新目录 —— 它在下一次正文写入时才出现"

    @pytest.mark.django_db
    def test_same_name_patch_does_not_move_anything(
        self, session_client, isolate_markdown_mirror, workspace, create_user
    ):
        """PATCH 一个同名 `name` ⇒ 什么都不搬（只在真的改名时才动手）。

        一次多余的 `os.replace` 会白改文件 mtime，也会让「保存」这个动作在磁盘上留下
        与内容无关的痕迹。这条守住「name 没变就不碰磁盘」。
        """
        root = _wiki_root(isolate_markdown_mirror)
        collection = _collection(workspace, create_user, "C", external_source="obsidian-vault", external_id=f"{WIKI}/C")
        page = _wiki_page(
            workspace,
            create_user,
            "X",
            collection=collection,
            external_id=f"{WIKI}/C/X.md",
            external_source="obsidian-vault",
        )
        note = root / "C" / "X.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("C 的镜像\n", encoding="utf-8")
        mtime_before = note.stat().st_mtime_ns

        response = _rename(session_client, workspace, collection, "C")
        assert response.status_code == status.HTTP_200_OK

        page.refresh_from_db()
        collection.refresh_from_db()
        assert (root / "C" / "X.md").is_file(), "旧目录仍在原位"
        assert note.stat().st_mtime_ns == mtime_before, "同名 PATCH 不得白改文件的 mtime"
        assert not (root / "D").exists(), "没有新名字，就没有新目录"
        assert page.external_id == f"{WIKI}/C/X.md", "没改名就不该动 external_id"
        assert collection.external_id == f"{WIKI}/C"
