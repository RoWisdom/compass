# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from rest_framework.test import APIClient
from pytest_django.fixtures import django_db_setup

from plane.db.models import User, Workspace, WorkspaceMember
from plane.db.models.api import APIToken


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup):  # noqa: F811
    """Set up the Django database for the test session"""
    pass


@pytest.fixture(autouse=True)
def isolate_markdown_mirror(tmp_path, monkeypatch):
    """Point the page-Markdown mirror at a throwaway directory, for every test.

    Several write paths mirror a page to a local ``.md`` file — see
    ``apps/api/plane/app/views/page/base.py`` (``_write_page_mirror``, called from
    ``PageViewSet.create`` and ``PagesDescriptionViewSet.partial_update``). That
    mirror honours ``MARKDOWN_STORAGE_PATH``, which in ``apps/api/.env`` points at
    a developer's **real Obsidian vault**.

    Left unset, a test that creates or updates a page writes a file into that
    vault. The trap is that the test which does this is the one whose assertion
    *fails*: the mirror is written only after the page is actually created, so a
    passing "this must be rejected" test writes nothing while a failing one
    writes a stray page into the user's notes. Relying on each test to remember
    ``monkeypatch.setenv`` makes that failure mode silent by construction.

    Autouse rather than opt-in, so no individual test has to know the mirror
    exists. ``markdown_storage.get_markdown_root`` reads the variable at call
    time, so ``monkeypatch`` is enough — no settings reload needed.
    """
    mirror_root = tmp_path / "markdown-mirror"
    monkeypatch.setenv("MARKDOWN_STORAGE_PATH", str(mirror_root))
    # The Wiki tree is a **second** mirror root, configured independently of the
    # projects root — production no longer derives it from ``get_markdown_root()``.
    # This fixture pins it explicitly instead of relying on any fallback:
    # ``apps/api/.env`` sets ``WIKI_MARKDOWN_STORAGE_PATH`` to the developer's real
    # vault (for the real import run), so any DB-touching test run that sources
    # ``.env`` would otherwise write into the user's notes. It has already happened
    # once. Pinning both variables at a ``tmp_path`` subdirectory isolates the two
    # roots for free, and this fixture's behaviour does not depend on how
    # production resolves a root. Assert both landed under ``tmp_path``: the
    # failure mode here is *silent*, so the guard has to be loud.
    monkeypatch.setenv("WIKI_MARKDOWN_STORAGE_PATH", str(tmp_path / "3-Wiki"))

    # Imported here rather than at module scope: this fixture is autouse, so it runs
    # for every test, and a hard failure at collection time would be a worse signal
    # than one inside the fixture that owns the invariant.
    from plane.utils.markdown_storage import get_markdown_root, get_wiki_markdown_root

    for root in (get_markdown_root(), get_wiki_markdown_root()):
        assert root.is_relative_to(tmp_path), f"markdown mirror escaped isolation: {root}"
    return mirror_root


@pytest.fixture
def api_client():
    """Return an unauthenticated API client"""
    return APIClient()


@pytest.fixture
def user_data():
    """Return standard user data for tests"""
    return {
        "email": "test@plane.so",
        "password": "test-password",
        "first_name": "Test",
        "last_name": "User",
    }


@pytest.fixture
def create_user(db, user_data):
    """Create and return a user instance"""
    user = User.objects.create(
        email=user_data["email"],
        first_name=user_data["first_name"],
        last_name=user_data["last_name"],
    )
    user.set_password(user_data["password"])
    user.save()
    return user


@pytest.fixture
def api_token(db, create_user):
    """Create and return an API token for testing the external API"""
    token = APIToken.objects.create(
        user=create_user,
        label="Test API Token",
        token="test-api-token-12345",
    )
    return token


@pytest.fixture
def api_key_client(api_client, api_token):
    """Return an API key authenticated client for external API testing"""
    api_client.credentials(HTTP_X_API_KEY=api_token.token)
    return api_client


@pytest.fixture
def session_client(api_client, create_user):
    """Return a session authenticated API client for app API testing, which is what plane.app uses"""
    api_client.force_authenticate(user=create_user)
    return api_client


@pytest.fixture
def create_bot_user(db):
    """Create and return a bot user instance"""
    from uuid import uuid4

    unique_id = uuid4().hex[:8]
    user = User.objects.create(
        email=f"bot-{unique_id}@plane.so",
        username=f"bot_user_{unique_id}",
        first_name="Bot",
        last_name="User",
        is_bot=True,
    )
    user.set_password("bot@123")
    user.save()
    return user


@pytest.fixture
def api_token_data():
    """Return sample API token data for testing"""
    from django.utils import timezone
    from datetime import timedelta

    return {
        "label": "Test API Token",
        "description": "Test description for API token",
        "expired_at": (timezone.now() + timedelta(days=30)).isoformat(),
    }


@pytest.fixture
def create_api_token_for_user(db, create_user):
    """Create and return an API token for a specific user"""
    return APIToken.objects.create(
        label="Test Token",
        description="Test token description",
        user=create_user,
        user_type=0,
    )


@pytest.fixture
def plane_server(live_server):
    """
    Renamed version of live_server fixture to avoid name clashes.
    Returns a live Django server for testing HTTP requests.
    """
    return live_server


@pytest.fixture
def workspace(create_user):
    """
    Create a new workspace and return the
    corresponding Workspace model instance.
    """
    # Create the workspace using the model
    created_workspace = Workspace.objects.create(
        name="Test Workspace",
        owner=create_user,
        slug="test-workspace",
    )

    WorkspaceMember.objects.create(workspace=created_workspace, member=create_user, role=20)

    return created_workspace


@pytest.fixture
def project(db, workspace, create_user):
    """A project inside the ``workspace`` fixture, with ``create_user`` as its admin."""
    from plane.db.models import Project, ProjectMember

    created = Project.objects.create(
        name="Test Project",
        identifier="TPJ",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(project=created, member=create_user, workspace=workspace, role=20)
    return created


@pytest.fixture
def create_issue(db, workspace, project, create_user):
    """A work item in the ``project`` fixture."""
    from plane.db.models import Issue

    return Issue.objects.create(
        name="Test Work Item",
        project=project,
        workspace=workspace,
        created_by=create_user,
    )


@pytest.fixture
def create_state(db, workspace, project, create_user):
    """A ``started`` state in the ``project`` fixture — the target the ledger tier moves to."""
    from plane.db.models import State, StateGroup

    return State.objects.create(
        name="In Progress",
        group=StateGroup.STARTED.value,
        color="#F59E0B",
        project=project,
        workspace=workspace,
        created_by=create_user,
    )


@pytest.fixture
def bot_api_key_client(api_client, create_bot_user, workspace, project):
    """An ``X-Api-Key`` client authenticated as a **bot** that can reach the project.

    Deliberately not the existing ``api_key_client``: that one carries the
    ``api_token`` fixture, whose holder is the human ``create_user``. The bot guard
    keys on ``is_bot``, so its tests need a bot's token and a human's side by side —
    this is the bot's, and the pre-existing ``api_key_client`` is the human's.
    """
    from plane.db.models import APIToken, ProjectMember, WorkspaceMember

    WorkspaceMember.objects.get_or_create(
        workspace=workspace, member=create_bot_user, defaults={"role": 15}
    )
    ProjectMember.objects.get_or_create(
        project=project, member=create_bot_user, defaults={"workspace": workspace, "role": 15}
    )
    token = APIToken.objects.create(
        user=create_bot_user,
        user_type=1,  # Bot
        workspace=workspace,
        is_service=True,
        label="bot test token",
    )
    api_client.credentials(HTTP_X_API_KEY=token.token)
    return api_client
