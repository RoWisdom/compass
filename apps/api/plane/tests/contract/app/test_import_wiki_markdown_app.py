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


class _sink:
    """吞掉命令的 stdout，测试只关心库里的结果。"""

    def write(self, _text):
        pass

    def flush(self):
        pass
