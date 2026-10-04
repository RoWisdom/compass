# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from uuid import uuid4

from rest_framework import serializers

from plane.api.serializers import WorkspaceLiteSerializer
from plane.app.serializers import WorkSpaceSerializer
from plane.license.api.serializers import WorkspaceSerializer as InstanceWorkspaceSerializer
from plane.db.models import Workspace, User

# Names with no letter or digit — must be rejected (issue #9255)
SYMBOL_ONLY_NAMES = ["-_________-", "---", "___", "- - -", "   ", "  -_ "]

# Names containing at least one letter or digit — must be accepted, including
# international scripts, so the rule does not regress non-Latin workspace names
VALID_NAMES = [
    "Acme Corp",
    "Acme_Corp-123",
    "123",
    "a",
    "R&D",
    "José",
    "Müller GmbH",
    "日本語",
    "株式会社",
    "محمد",
]

# Names embedding a URL — must be rejected on both create paths
URL_NAMES = ["https://evil.com", "www.example.com", "example.com"]


@pytest.mark.unit
class TestWorkspaceLiteSerializer:
    """Test the WorkspaceLiteSerializer"""

    def test_workspace_lite_serializer_fields(self, db):
        """Test that the serializer includes the correct fields"""
        # Create a user to be the owner
        owner = User.objects.create(email="test@example.com", first_name="Test", last_name="User")

        # Create a workspace with explicit ID to test serialization
        workspace_id = uuid4()
        workspace = Workspace.objects.create(name="Test Workspace", slug="test-workspace", id=workspace_id, owner=owner)

        # Serialize the workspace
        serialized_data = WorkspaceLiteSerializer(workspace).data

        # Check fields are present and correct
        assert "name" in serialized_data
        assert "slug" in serialized_data
        assert "id" in serialized_data

        assert serialized_data["name"] == "Test Workspace"
        assert serialized_data["slug"] == "test-workspace"
        assert str(serialized_data["id"]) == str(workspace_id)

    def test_workspace_lite_serializer_read_only(self, db):
        """Test that the serializer fields are read-only"""
        # Create a user to be the owner
        owner = User.objects.create(email="test2@example.com", first_name="Test", last_name="User")

        # Create a workspace
        workspace = Workspace.objects.create(name="Test Workspace", slug="test-workspace", id=uuid4(), owner=owner)

        # Try to update via serializer
        serializer = WorkspaceLiteSerializer(workspace, data={"name": "Updated Name", "slug": "updated-slug"})

        # Serializer should be valid (since read-only fields are ignored)
        assert serializer.is_valid()

        # Save should not update the read-only fields
        updated_workspace = serializer.save()
        assert updated_workspace.name == "Test Workspace"
        assert updated_workspace.slug == "test-workspace"


@pytest.mark.unit
class TestWorkSpaceSerializerNameValidation:
    """validate_name must reject symbol-only workspace names (issue #9255).

    Frontend validation is bypassable via a direct API call, so the rule is
    enforced server-side on both the create and rename (partial_update) paths,
    which share this serializer's field-level validation.
    """

    @pytest.mark.parametrize("name", SYMBOL_ONLY_NAMES)
    def test_rejects_symbol_only_names(self, name):
        serializer = WorkSpaceSerializer()
        with pytest.raises(serializers.ValidationError):
            serializer.validate_name(name)

    @pytest.mark.parametrize("name", VALID_NAMES)
    def test_accepts_names_with_alphanumeric(self, name):
        serializer = WorkSpaceSerializer()
        assert serializer.validate_name(name) == name

    @pytest.mark.parametrize("name", URL_NAMES)
    def test_rejects_names_containing_urls(self, name):
        serializer = WorkSpaceSerializer()
        with pytest.raises(serializers.ValidationError):
            serializer.validate_name(name)


@pytest.mark.unit
class TestInstanceWorkspaceSerializerNameValidation:
    """The instance/license workspace create path must enforce the same rules
    as the app serializer (symbol-only rejection AND URL rejection)."""

    @pytest.mark.parametrize("name", SYMBOL_ONLY_NAMES)
    def test_rejects_symbol_only_names(self, name):
        serializer = InstanceWorkspaceSerializer()
        with pytest.raises(serializers.ValidationError):
            serializer.validate_name(name)

    @pytest.mark.parametrize("name", VALID_NAMES)
    def test_accepts_names_with_alphanumeric(self, name):
        serializer = InstanceWorkspaceSerializer()
        assert serializer.validate_name(name) == name

    @pytest.mark.parametrize("name", URL_NAMES)
    def test_rejects_names_containing_urls(self, name):
        serializer = InstanceWorkspaceSerializer()
        with pytest.raises(serializers.ValidationError):
            serializer.validate_name(name)


# ─── 镜像根路径（罗盘：Workspace 目录配置）───────────────────────────────
# 三条规则：必须绝对（或 `~` 开头）、展开后不得是 `/`、长度 ≤ 500 且不含 NUL。
# **不做「目录是否存在」** —— 镜像写入本来就是 best-effort（`OSError` 只记日志、
# 从不抛），预先探活会把「以后会建出来的目录」误判成非法。

FIELD_NAMES = ("project_markdown_path", "wiki_markdown_path")


@pytest.mark.unit
class TestMarkdownPathValidation:
    @pytest.mark.parametrize("name", FIELD_NAMES)
    @pytest.mark.parametrize(
        "value",
        ["/srv/vault/3-Wiki", "~/wiki", "~", "/tmp/空间/目录"],
    )
    def test_accepts_absolute_and_tilde_paths(self, name, value):
        serializer = WorkSpaceSerializer()
        assert getattr(serializer, f"validate_{name}")(value) == value

    @pytest.mark.parametrize("name", FIELD_NAMES)
    @pytest.mark.parametrize("value", ["", None])
    def test_accepts_empty(self, name, value):
        """空 = 回落环境变量 / 内置默认，是合法输入（字段可空）。"""
        serializer = WorkSpaceSerializer()
        assert getattr(serializer, f"validate_{name}")(value) == value

    @pytest.mark.parametrize("name", FIELD_NAMES)
    @pytest.mark.parametrize(
        "value",
        ["relative/path", "3-Wiki", "./wiki", "../wiki", "~/../wiki-x"],
    )
    def test_rejects_relative_paths(self, name, value):
        serializer = WorkSpaceSerializer()
        with pytest.raises(serializers.ValidationError):
            getattr(serializer, f"validate_{name}")(value)

    @pytest.mark.parametrize("name", FIELD_NAMES)
    @pytest.mark.parametrize("value", ["/", "//"])
    def test_rejects_filesystem_root(self, name, value):
        """`//` is the filesystem root too even though `Path("//") != Path("/")`."""
        serializer = WorkSpaceSerializer()
        with pytest.raises(serializers.ValidationError):
            getattr(serializer, f"validate_{name}")(value)

    @pytest.mark.parametrize("name", FIELD_NAMES)
    def test_rejects_nul(self, name):
        serializer = WorkSpaceSerializer()
        with pytest.raises(serializers.ValidationError):
            getattr(serializer, f"validate_{name}")("/srv/a\x00b")


@pytest.mark.unit
class TestInstanceWorkspaceSerializerMarkdownPathValidation:
    """The instance/license workspace create path must reject the same invalid
    markdown roots as the app-level WorkSpaceSerializer. The license serializer
    sets ``fields = "__all__"``, so both ``*_markdown_path`` fields are writable
    there and must be validated before they persist (validator-level coverage:
    this exercises ``validate_*`` directly, the same way the app-level tests do).
    """

    @pytest.mark.parametrize("name", FIELD_NAMES)
    @pytest.mark.parametrize(
        "value",
        ["relative/path", "3-Wiki", "./wiki", "../wiki", "~/../wiki-x"],
    )
    def test_rejects_relative_paths(self, name, value):
        serializer = InstanceWorkspaceSerializer()
        with pytest.raises(serializers.ValidationError):
            getattr(serializer, f"validate_{name}")(value)

    @pytest.mark.parametrize("name", FIELD_NAMES)
    @pytest.mark.parametrize("value", ["/", "//"])
    def test_rejects_filesystem_root(self, name, value):
        serializer = InstanceWorkspaceSerializer()
        with pytest.raises(serializers.ValidationError):
            getattr(serializer, f"validate_{name}")(value)

    @pytest.mark.parametrize("name", FIELD_NAMES)
    @pytest.mark.parametrize("value", ["/srv/vault/3-Wiki", "~/wiki", "~", "", None])
    def test_accepts_valid_paths(self, name, value):
        serializer = InstanceWorkspaceSerializer()
        assert getattr(serializer, f"validate_{name}")(value) == value

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "field,value",
        [
            ("project_markdown_path", "relative/path"),
            ("project_markdown_path", "//"),
            ("wiki_markdown_path", "relative/path"),
            ("wiki_markdown_path", "//"),
        ],
    )
    def test_is_valid_rejects_invalid_path(self, field, value):
        """The create path calls ``is_valid()``; prove the field validators are
        actually wired there, not merely defined."""
        serializer = InstanceWorkspaceSerializer(data={"name": "Acme", "slug": "acme", field: value})
        assert not serializer.is_valid()
        assert field in serializer.errors

    @pytest.mark.django_db
    def test_is_valid_accepts_valid_path(self):
        serializer = InstanceWorkspaceSerializer(
            data={"name": "Acme", "slug": "acme", "project_markdown_path": "/srv/vault"}
        )
        assert serializer.is_valid(), serializer.errors
