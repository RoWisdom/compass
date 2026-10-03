# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""`import_wiki_markdown`（设计 §3.6）。

四条不变量在这里锁死：

1. **只补缺**：`external_id` 已存在就跳过，一个字都不改 —— 否则重跑会抹掉
   用户在 Plane 里改过的正文。
2. **只读 vault**：导入**不写**任何文件。第一次落盘发生在用户编辑那一页时。
3. **frontmatter 不进正文**：它是 Obsidian 的元数据，页面正文是它**之后**的部分。
4. **顺序**：按字母序**逆序**创建，配合 `-created_at` 的默认排序，侧栏里就是正序。

vault 用 `tmp_path` 现造，绝不碰真实目录。"""

import pytest
from django.core.management import call_command

from plane.db.models import Page, PageCollection


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """一个假 vault：两个文件夹、三篇笔记（其中一篇带 frontmatter、一篇在子目录）。"""
    root = tmp_path / "3-Wiki"
    (root / "Claude Code").mkdir(parents=True)
    (root / "Claude Code" / "安装与更新.md").write_text("## 标题\n\n正文。\n", encoding="utf-8")
    (root / "Claude Code" / "剪藏.md").write_text(
        "---\ntags:\n  - 罗盘\nsource: https://x\n---\n\n剪藏正文\n", encoding="utf-8"
    )
    (root / "OpenClaw").mkdir()
    (root / "OpenClaw" / "Quick Start.md").write_text("起步。\n", encoding="utf-8")
    (root / ".hidden").mkdir()
    (root / ".hidden" / "不该导入.md").write_text("x\n", encoding="utf-8")
    monkeypatch.setenv("WIKI_MARKDOWN_STORAGE_PATH", str(root))
    return root


@pytest.mark.contract
class TestImportWikiMarkdown:
    @pytest.mark.django_db
    def test_creates_one_collection_per_folder_and_one_page_per_note(self, vault, workspace, create_user):
        call_command(
            "import_wiki_markdown",
            workspace_slug=workspace.slug,
            owner=create_user.email,
            stdout=_sink(),
        )

        assert PageCollection.objects.filter(workspace=workspace).count() == 2
        assert Page.objects.filter(workspace=workspace, external_source="obsidian-vault").count() == 3
        assert not PageCollection.objects.filter(workspace=workspace, name=".hidden").exists()

    @pytest.mark.django_db
    def test_pages_land_in_their_collection_with_a_vault_relative_external_id(self, vault, workspace, create_user):
        call_command("import_wiki_markdown", workspace_slug=workspace.slug, owner=create_user.email, stdout=_sink())

        page = Page.objects.get(external_id="3-Wiki/Claude Code/安装与更新.md")
        assert page.collection.name == "Claude Code"
        assert page.name == "安装与更新"
        assert page.is_global is True
        assert page.access == 0
        assert page.owned_by_id == create_user.id

    @pytest.mark.django_db
    def test_frontmatter_is_not_imported_into_the_body(self, vault, workspace, create_user):
        call_command("import_wiki_markdown", workspace_slug=workspace.slug, owner=create_user.email, stdout=_sink())

        page = Page.objects.get(external_id="3-Wiki/Claude Code/剪藏.md")
        assert "tags" not in page.description_html
        assert "罗盘" not in page.description_html
        assert "剪藏正文" in page.description_html

    @pytest.mark.django_db
    def test_cjk_heading_survives_as_html_headings(self, vault, workspace, create_user):
        call_command("import_wiki_markdown", workspace_slug=workspace.slug, owner=create_user.email, stdout=_sink())

        page = Page.objects.get(external_id="3-Wiki/Claude Code/安装与更新.md")
        assert "<h2>标题</h2>" in page.description_html

    @pytest.mark.django_db
    def test_rerunning_changes_nothing(self, vault, workspace, create_user):
        """幂等的核心断言：第二次跑，零新建、零修改。"""
        call_command("import_wiki_markdown", workspace_slug=workspace.slug, owner=create_user.email, stdout=_sink())

        Page.objects.filter(external_id="3-Wiki/Claude Code/安装与更新.md").update(
            description_html="<p>我在 Plane 里改过了</p>"
        )
        PageCollection.objects.filter(external_id="3-Wiki/Claude Code").update(name="我改的集合名")

        call_command("import_wiki_markdown", workspace_slug=workspace.slug, owner=create_user.email, stdout=_sink())

        assert PageCollection.objects.filter(workspace=workspace).count() == 2
        assert Page.objects.filter(workspace=workspace, external_source="obsidian-vault").count() == 3
        assert (
            Page.objects.get(external_id="3-Wiki/Claude Code/安装与更新.md").description_html
            == "<p>我在 Plane 里改过了</p>"
        )
        assert PageCollection.objects.get(external_id="3-Wiki/Claude Code").name == "我改的集合名"

    @pytest.mark.django_db
    def test_import_writes_no_files(self, vault, workspace, create_user, isolate_markdown_mirror):
        before = sorted(p.relative_to(vault) for p in vault.rglob("*"))
        call_command("import_wiki_markdown", workspace_slug=workspace.slug, owner=create_user.email, stdout=_sink())
        assert sorted(p.relative_to(vault) for p in vault.rglob("*")) == before

    @pytest.mark.django_db
    def test_nested_file_gets_a_parent_page_named_after_its_folder(self, vault, workspace, create_user):
        (vault / "终端工具").mkdir()
        (vault / "终端工具" / "iTerm").mkdir()
        (vault / "终端工具" / "iTerm" / "快捷键.md").write_text("正文\n", encoding="utf-8")

        call_command("import_wiki_markdown", workspace_slug=workspace.slug, owner=create_user.email, stdout=_sink())

        parent = Page.objects.get(external_id="3-Wiki/终端工具/iTerm")
        child = Page.objects.get(external_id="3-Wiki/终端工具/iTerm/快捷键.md")
        assert parent.name == "iTerm"
        assert child.parent_id == parent.id

    @pytest.mark.django_db
    def test_two_level_nesting_keeps_each_directory_page_under_its_own_parent(self, vault, workspace, create_user):
        """两级以上的嵌套：中间那级目录页要挂在**上一级目录页**下，不是挂在根上。

        回归锁（裁定 ②）：`defaults` 曾漏掉 `parent`，于是 `A/B` 的目录页 parent 为空
        —— 文件挂在 B 下、B 却挂在根上，树被展平。一级嵌套看不出来（那级 parent 本来就是空）。
        """
        (vault / "终端工具" / "iTerm" / "配置").mkdir(parents=True)
        (vault / "终端工具" / "iTerm" / "配置" / "快捷键.md").write_text("正文\n", encoding="utf-8")

        call_command("import_wiki_markdown", workspace_slug=workspace.slug, owner=create_user.email, stdout=_sink())

        outer = Page.objects.get(external_id="3-Wiki/终端工具/iTerm")
        middle = Page.objects.get(external_id="3-Wiki/终端工具/iTerm/配置")
        leaf = Page.objects.get(external_id="3-Wiki/终端工具/iTerm/配置/快捷键.md")
        assert outer.parent_id is None, "最外层目录页本来就该在根上"
        assert middle.parent_id == outer.id, "中间那级目录页要挂在上一级目录页下"
        assert leaf.parent_id == middle.id, "笔记挂在它自己那一级目录页下"

    @pytest.mark.django_db
    def test_dry_run_creates_nothing(self, vault, workspace, create_user):
        call_command(
            "import_wiki_markdown", workspace_slug=workspace.slug, owner=create_user.email, dry_run=True, stdout=_sink()
        )
        assert PageCollection.objects.filter(workspace=workspace).count() == 0
        assert Page.objects.filter(workspace=workspace).count() == 0

    @pytest.mark.django_db
    def test_missing_owner_fails_loudly(self, vault, workspace):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            call_command("import_wiki_markdown", workspace_slug=workspace.slug, owner="nobody@nowhere", stdout=_sink())

    @pytest.mark.django_db
    def test_directory_pages_are_stamped_as_folders(self, vault, workspace, create_user):
        """目录页是**文件夹**，笔记是**页面** —— 判别符不能混（罗盘 Round D）。"""
        (vault / "终端工具").mkdir()
        (vault / "终端工具" / "iTerm").mkdir()
        (vault / "终端工具" / "iTerm" / "配置").mkdir()
        (vault / "终端工具" / "iTerm" / "配置" / "快捷键.md").write_text("正文\n", encoding="utf-8")

        call_command("import_wiki_markdown", workspace_slug=workspace.slug, owner=create_user.email, stdout=_sink())

        # `3-Wiki/终端工具` 是**集合**（顶层文件夹被建成集合），不是页面 —— 所以没有
        # 它的 node_type 可断言。目录**页**从集合内的第一层子目录才开始。
        assert not Page.objects.filter(external_id="3-Wiki/终端工具").exists(), "顶层文件夹是集合，不是页面"
        assert Page.objects.get(external_id="3-Wiki/终端工具/iTerm").node_type == Page.NODE_TYPE_FOLDER
        assert Page.objects.get(external_id="3-Wiki/终端工具/iTerm/配置").node_type == Page.NODE_TYPE_FOLDER
        assert Page.objects.get(external_id="3-Wiki/终端工具/iTerm/配置/快捷键.md").node_type == Page.NODE_TYPE_DOC, (
            "笔记还是页面 —— 目录页与笔记的判别符不能混"
        )

    @pytest.mark.django_db
    def test_an_existing_directory_page_is_restamped(self, vault, workspace, create_user):
        """**已经导过一遍**的目录页要在重跑时被补盖。

        `get_or_create` 的 `defaults` 只在新建时生效，所以 226 上那 6 个已存在的目录页
        会留在 `"doc"`。这条锁住"重跑一次导入就能修好"，而不是要用户去手改数据库。

        （补盖不违反本命令"重跑不抹掉用户在 Plane 里的改动"那条不变量：`node_type`
        在 Plane 里**没有任何写入口** —— 树/详情序列化器都把它放在 `read_only_fields`、
        更新序列化器根本不声明它 —— 所以它不是"用户的改动"。）
        """
        (vault / "终端工具").mkdir()
        (vault / "终端工具" / "iTerm").mkdir()
        (vault / "终端工具" / "iTerm" / "快捷键.md").write_text("正文\n", encoding="utf-8")
        call_command("import_wiki_markdown", workspace_slug=workspace.slug, owner=create_user.email, stdout=_sink())

        directory_page = Page.objects.get(external_id="3-Wiki/终端工具/iTerm")
        # 模拟"本轮之前导过的库"：判别符还是默认值。
        Page.objects.filter(pk=directory_page.id).update(node_type=Page.NODE_TYPE_DOC)

        call_command("import_wiki_markdown", workspace_slug=workspace.slug, owner=create_user.email, stdout=_sink())

        assert Page.objects.get(pk=directory_page.id).node_type == Page.NODE_TYPE_FOLDER

    @pytest.mark.django_db
    def test_restamping_touches_only_the_node_type(self, vault, workspace, create_user):
        """补盖**只准动 `node_type` 一个字段**。

        用目录页上"用户在 Plane 里改过的名字"做哨兵：`save(update_fields=[...])` 一旦
        漏掉这个纪律（写成 `save()`，或把 `name` 顺手带进 `defaults` 的更新分支），
        这条就红 —— 而 `test_rerunning_changes_nothing`（既有）盯的是**笔记**，
        盯不到目录页那条 `get_or_create` 分支。
        """
        (vault / "终端工具").mkdir()
        (vault / "终端工具" / "iTerm").mkdir()
        (vault / "终端工具" / "iTerm" / "快捷键.md").write_text("正文\n", encoding="utf-8")
        call_command("import_wiki_markdown", workspace_slug=workspace.slug, owner=create_user.email, stdout=_sink())

        directory_page = Page.objects.get(external_id="3-Wiki/终端工具/iTerm")
        Page.objects.filter(pk=directory_page.id).update(
            node_type=Page.NODE_TYPE_DOC, name="用户在 Plane 里改过的目录名"
        )

        call_command("import_wiki_markdown", workspace_slug=workspace.slug, owner=create_user.email, stdout=_sink())

        refreshed = Page.objects.get(pk=directory_page.id)
        assert refreshed.node_type == Page.NODE_TYPE_FOLDER
        assert refreshed.name == "用户在 Plane 里改过的目录名", "补盖只准动 node_type 一个字段"


class _sink:
    """吞掉命令的 stdout，测试只关心库里的结果。"""

    def write(self, _text):
        pass

    def flush(self):
        pass
