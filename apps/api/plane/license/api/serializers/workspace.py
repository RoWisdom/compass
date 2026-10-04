# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third Party Imports
from rest_framework import serializers

# Module imports
from .base import BaseSerializer
from .user import UserLiteSerializer
from plane.app.serializers.workspace import WorkSpaceSerializer
from plane.db.models import Workspace
from plane.utils.constants import RESTRICTED_WORKSPACE_SLUGS
from plane.utils.content_validator import has_alphanumeric
from plane.utils.url import contains_url


class WorkspaceSerializer(BaseSerializer):
    owner = UserLiteSerializer(read_only=True)
    logo_url = serializers.CharField(read_only=True)
    total_projects = serializers.IntegerField(read_only=True)
    total_members = serializers.IntegerField(read_only=True)

    def validate_name(self, value):
        # Check if the name contains a URL (kept consistent with the app-level
        # WorkSpaceSerializer so both workspace-create paths validate alike).
        if contains_url(value):
            raise serializers.ValidationError("Name must not contain URLs")
        # Reject symbol-only names like "-_________-" that have no letter or
        # digit. Mirrors the frontend HAS_ALPHANUMERIC_REGEX check so the rule
        # cannot be bypassed via a direct API call.
        if not has_alphanumeric(value):
            raise serializers.ValidationError(
                "Name must contain at least one letter or number"
            )
        return value

    def validate_slug(self, value):
        # Check if the slug is restricted
        if value in RESTRICTED_WORKSPACE_SLUGS:
            raise serializers.ValidationError("Slug is not valid")
        # Check uniqueness case-insensitively
        if Workspace.objects.filter(slug__iexact=value).exists():
            raise serializers.ValidationError("Slug is already in use")
        return value

    # `fields = "__all__"` makes both markdown-root fields writable on the
    # instance-admin create path too, so reuse the app-level rule instead of
    # letting this endpoint accept (and persist) an unvalidated path. Delegating
    # to the shared helper keeps a single implementation (see
    # `WorkSpaceSerializer._validate_markdown_path`).
    def validate_project_markdown_path(self, value):
        return WorkSpaceSerializer._validate_markdown_path(value, "Project pages directory")

    def validate_wiki_markdown_path(self, value):
        return WorkSpaceSerializer._validate_markdown_path(value, "Wiki pages directory")

    class Meta:
        model = Workspace
        fields = "__all__"
        read_only_fields = [
            "id",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
            "owner",
            "logo_url",
        ]
