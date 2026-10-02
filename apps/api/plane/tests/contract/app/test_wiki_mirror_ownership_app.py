# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""镜像的「文件归属」判据（最终评审 W2）。

导入**不写镜像**，所以 vault 里被导入的 27 篇笔记里只有 3 篇带 `id:` —— 其余都是
「无 id」的现有文件。而 `_resolve_page_path` 旧判据是 `_frontmatter_id(path) not in
(None, page_id)`：`None in (None, page_id)` 为真 ⇒ **无 id 的文件被判成「可覆盖」**。
于是任何一篇与它同名的页面（包括**不是**它的那一页）保存正文时，写侧 `.tmp`+`replace`、
搬侧 `old_path.replace(new_path)`，都会把用户手写的整篇笔记静默盖掉。

用户已裁定的语义（计划 R-10 ①）：一个无 `id:` 的文件算「我们自己的」**仅当**它的路径
就是本页记录在案的来源路径（`Page.external_id` 记下的 vault 相对路径）。其余情况一律
保守：目标位置有不是我们的文件 ⇒ 加 `-{id[:8]}` 后缀；要搬走的文件其 `id:` 不是本页
⇒ **不搬**。失败方向永远是「没搬」，不是「覆盖/删除」。

`isolate_markdown_mirror` 把两棵镜像根都钉进 tmp_path：项目根 = `markdown-mirror`，
wiki 根 = `3-Wiki`（tmp_path 下的**兄弟**目录）。vault 根 = wiki 根的 parent，
所以 `Page.external_id`（vault 相对路径，如 `3-Wiki/C/X.md`）在这里就落在 tmp_path 下。
"""

import pytest
from rest_framework import status

from plane.db.models import Page, PageCollection
from plane.utils.markdown_storage import move_mirror_file

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


def _collection(workspace, user, name):
    return PageCollection.objects.create(workspace=workspace, name=name, owned_by=user)


def _wiki_root(isolate_markdown_mirror):
    return isolate_markdown_mirror.parent / WIKI


def _save_body(client, workspace, page, html="<p>Plane 正文</p>"):
    """Edit a wiki page's body through the description route (what the editor hits)."""
    return client.patch(
        f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/description/",
        {"description_html": html},
        format="json",
    )


@pytest.mark.contract
class TestWikiMirrorOwnership:
    @pytest.mark.django_db
    def test_edited_imported_note_is_merged_not_duplicated(
        self, session_client, isolate_markdown_mirror, workspace, create_user
    ):
        """回归锁：页面 P 的来源文件（无 `id:`）被保存正文时**原地合并**。

        `external_id` 指向 `3-Wiki/C/X.md`，那篇笔记是用户剪藏来的（带 `tags` / `created`）。
        保存正文后：用户键一个不少、Plane 只多三个键、正文换成新的，**且不产生
        `X-<id8>.md`**。这条锁的是「Plane 是编辑器」+「剪藏元数据不丢」—— 改坏了 W2 就白做。
        """
        collection = _collection(workspace, create_user, "C")
        note = _wiki_root(isolate_markdown_mirror) / "C" / "X.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("---\ntags:\n  - 罗盘\ncreated: 2026-05-31\n---\n\n用户正文\n", encoding="utf-8")

        page = _wiki_page(
            workspace,
            create_user,
            "X",
            collection=collection,
            external_id=f"{WIKI}/C/X.md",
            external_source="obsidian-vault",
        )

        response = _save_body(session_client, workspace, page)
        assert response.status_code == status.HTTP_200_OK

        assert note.is_file(), "必须合并进原文件本身"
        text = note.read_text(encoding="utf-8")
        assert "  - 罗盘" in text, "剪藏的 tags 不得丢"
        assert "created: 2026-05-31" in text, "剪藏的其余键不得丢"
        assert f"id: {page.id}" in text, "Plane 的三个键写进去了"
        assert text.rstrip().endswith("Plane 正文"), "正文换成新写的"
        assert text.count("---") == 2, "只应有一个 frontmatter 块"

        suffixed = _wiki_root(isolate_markdown_mirror) / "C" / f"X-{str(page.id)[:8]}.md"
        assert not suffixed.exists(), "这就是本页自己的来源文件，不该另起一份"

    @pytest.mark.django_db
    def test_different_page_does_not_overwrite_a_handwritten_note(
        self, session_client, isolate_markdown_mirror, workspace, create_user
    ):
        """页面 Q（无 `external_id`）落到同名路径上，那里是用户手写的笔记 ⇒ 让开。

        无 `id:` 的文件只有当它**就是本页的来源路径**时才算我们的；Q 没有记录在案的
        来源 ⇒ 一律保守：写到 `X-<id8>.md`，原文件一字不动。
        """
        collection = _collection(workspace, create_user, "C")
        note = _wiki_root(isolate_markdown_mirror) / "C" / "X.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("---\ntags:\n  - 手写\n---\n\n用户手写正文\n", encoding="utf-8")
        before = note.read_text(encoding="utf-8")

        page = _wiki_page(workspace, create_user, "X", collection=collection)  # external_id: None

        response = _save_body(session_client, workspace, page)
        assert response.status_code == status.HTTP_200_OK

        assert note.read_text(encoding="utf-8") == before, "用户手写的笔记一字不得动"
        suffixed = _wiki_root(isolate_markdown_mirror) / "C" / f"X-{str(page.id)[:8]}.md"
        assert suffixed.is_file(), "本页自己的正文必须写到带后缀的兄弟文件"
        assert "Plane 正文" in suffixed.read_text(encoding="utf-8")

    @pytest.mark.django_db
    def test_a_file_owned_by_another_page_still_gets_a_suffix(
        self, session_client, isolate_markdown_mirror, workspace, create_user
    ):
        """既有行为的回归锁：同名的文件带 `id:` 且是**另一个**页面 ⇒ 仍加后缀。"""
        collection = _collection(workspace, create_user, "C")
        other = _wiki_page(workspace, create_user, "别人的页")
        note = _wiki_root(isolate_markdown_mirror) / "C" / "X.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(f"---\nid: {other.id}\n---\n\n别人的正文\n", encoding="utf-8")
        before = note.read_text(encoding="utf-8")

        page = _wiki_page(workspace, create_user, "X", collection=collection)

        response = _save_body(session_client, workspace, page)
        assert response.status_code == status.HTTP_200_OK

        assert note.read_text(encoding="utf-8") == before, "别页的镜像不得被覆盖"
        suffixed = _wiki_root(isolate_markdown_mirror) / "C" / f"X-{str(page.id)[:8]}.md"
        assert suffixed.is_file()

    @pytest.mark.django_db
    def test_move_refuses_to_clobber_a_file_owned_by_another_page(
        self, isolate_markdown_mirror, workspace, create_user
    ):
        """搬移的目标位置已有一篇**不是本页**的文件 ⇒ 不搬。

        走 `move_mirror_file` 直调：经过 API 时，`_resolve_page_path` 会先把新路径让开
        （见测试 3），所以这道守卫只在没有单一路径解析可用的跨根搬移里才暴露得到。
        """
        wiki_root = _wiki_root(isolate_markdown_mirror)
        page = _wiki_page(workspace, create_user, "搬家页")
        other = _wiki_page(workspace, create_user, "别人的页")
        source = wiki_root / "集合A" / "搬家页.md"
        target = wiki_root / "集合B" / "搬家页.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f"---\nid: {page.id}\n---\n\n我的正文\n", encoding="utf-8")
        target.write_text(f"---\nid: {other.id}\n---\n\n别人的正文\n", encoding="utf-8")
        target_before = target.read_text(encoding="utf-8")

        move_mirror_file(source, target, "搬家页", "搬家页", str(page.id))

        assert source.is_file(), "目标被占时来源必须留在原地"
        assert target.read_text(encoding="utf-8") == target_before, "别人的文件内容一字不得动"

    @pytest.mark.django_db
    def test_move_refuses_to_move_a_file_owned_by_another_page(
        self, isolate_markdown_mirror, workspace, create_user
    ):
        """搬移的**来源**文件 `id:` 不是本页 ⇒ 不搬（文件留在原地）。

        「永远不要把不是本页的文件搬走或改名」——搬走同样是毁掉别人的笔记。
        """
        wiki_root = _wiki_root(isolate_markdown_mirror)
        page = _wiki_page(workspace, create_user, "搬家页")
        other = _wiki_page(workspace, create_user, "别人的页")
        source = wiki_root / "集合A" / "搬家页.md"
        target = wiki_root / "集合B" / "搬家页.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f"---\nid: {other.id}\n---\n\n别人的正文\n", encoding="utf-8")

        move_mirror_file(source, target, "搬家页", "搬家页", str(page.id))

        assert source.is_file(), "别人的文件不得被搬走/改名"
        assert not target.exists(), "不得在目标位置留下别人的内容副本"
