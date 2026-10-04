"""The wiki-root system check must fire only when data is actually at risk."""

# Third-party imports
import pytest

# Django imports
from django.db.utils import OperationalError

# Module imports
from plane.db.models import Page, PageCollection
from plane.utils import checks
from plane.utils.checks import wiki_markdown_root_names

pytestmark = pytest.mark.django_db


def _ids(findings):
    return [finding.id for finding in findings]


def _run():
    return wiki_markdown_root_names(app_configs=None)


def test_misnamed_root_with_a_dependent_page_is_an_error(workspace, tmp_path):
    workspace.wiki_markdown_path = str(tmp_path / "wiki-not-three")
    workspace.save(update_fields=["wiki_markdown_path"])
    Page.objects.create(
        workspace=workspace,
        name="笔记",
        owned_by=workspace.owner,
        external_id="3-Wiki/集合/笔记.md",
    )
    assert "plane.E001" in _ids(_run())


def test_misnamed_root_with_a_dependent_collection_is_an_error(workspace, tmp_path):
    workspace.wiki_markdown_path = str(tmp_path / "wiki-not-three")
    workspace.save(update_fields=["wiki_markdown_path"])
    PageCollection.objects.create(
        workspace=workspace, name="集合", owned_by=workspace.owner, external_id="3-Wiki/集合"
    )
    assert "plane.E001" in _ids(_run())


def test_conforming_root_is_clean(workspace, tmp_path):
    workspace.wiki_markdown_path = str(tmp_path / "3-Wiki")
    workspace.save(update_fields=["wiki_markdown_path"])
    Page.objects.create(
        workspace=workspace,
        name="笔记",
        owned_by=workspace.owner,
        external_id="3-Wiki/集合/笔记.md",
    )
    assert _ids(_run()) == []


def test_misnamed_root_without_dependent_rows_does_not_raise_an_error(workspace, tmp_path):
    workspace.wiki_markdown_path = str(tmp_path / "wiki-not-three")
    workspace.save(update_fields=["wiki_markdown_path"])
    Page.objects.create(workspace=workspace, name="手写笔记", owned_by=workspace.owner)
    assert "plane.E001" not in _ids(_run())


def test_misnamed_env_root_is_reported_as_a_warning(monkeypatch, tmp_path):
    monkeypatch.setenv(checks.WIKI_ROOT_ENV, str(tmp_path / "wiki-not-three"))
    assert "plane.W001" in _ids(_run())


def test_conforming_env_root_is_not_warned_about(monkeypatch, tmp_path):
    # The autouse ``isolate_markdown_mirror`` fixture already pins the env var at
    # ``tmp_path / "3-Wiki"``; pin it here too so the test states its own premise.
    monkeypatch.setenv(checks.WIKI_ROOT_ENV, str(tmp_path / "3-Wiki"))
    assert _ids(_run()) == []


def test_check_is_silent_when_the_database_is_unreachable(monkeypatch, workspace, tmp_path):
    # Pin the env var at a *conforming* root, so the Warning branch contributes
    # nothing and the seeded workspace below is the only thing that could fire.
    monkeypatch.setenv(checks.WIKI_ROOT_ENV, str(tmp_path / "3-Wiki"))

    # Seed a row the check *would* flag: a misnamed workspace root plus a Page
    # whose external_id depends on the ``3-Wiki/`` prefix. Read, this is a
    # ``plane.E001``. That makes the ``== []`` below positive proof — if the
    # monkeypatch had failed to bind, the real query would surface the Error.
    workspace.wiki_markdown_path = str(tmp_path / "wiki-not-three")
    workspace.save(update_fields=["wiki_markdown_path"])
    Page.objects.create(
        workspace=workspace,
        name="笔记",
        owned_by=workspace.owner,
        external_id="3-Wiki/集合/笔记.md",
    )

    class _Down:
        def filter(self, *args, **kwargs):
            raise OperationalError("the database is unreachable")

    monkeypatch.setattr(Page, "objects", _Down())
    # No exception, and no finding invented from a database it could not read:
    # ``[]`` means the guard swallowed a finding it could not verify.
    assert _ids(_run()) == []
