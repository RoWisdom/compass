# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import serializers
import base64

# Module imports
from .base import BaseSerializer
from plane.utils.content_validator import (
    validate_binary_data,
    validate_html_content,
)
from plane.db.models import (
    Page,
    PageLabel,
    Label,
    ProjectPage,
    Project,
    PageVersion,
)


class PageSerializer(BaseSerializer):
    is_favorite = serializers.BooleanField(read_only=True)
    labels = serializers.ListField(
        child=serializers.PrimaryKeyRelatedField(queryset=Label.objects.all()),
        write_only=True,
        required=False,
    )
    # Many to many
    label_ids = serializers.ListField(child=serializers.UUIDField(), required=False)
    project_ids = serializers.ListField(child=serializers.UUIDField(), required=False)

    class Meta:
        model = Page
        fields = [
            "id",
            "name",
            "owned_by",
            "access",
            "color",
            "labels",
            "parent",
            "is_favorite",
            "is_locked",
            "archived_at",
            "workspace",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
            "view_props",
            "logo_props",
            "label_ids",
            "project_ids",
        ]
        read_only_fields = ["workspace", "owned_by"]

    def create(self, validated_data):
        labels = validated_data.pop("labels", None)
        project_id = self.context["project_id"]
        owned_by_id = self.context["owned_by_id"]
        description_json = self.context["description_json"]
        description_binary = self.context["description_binary"]
        description_html = self.context["description_html"]

        # `description_html` arrives via context, not as a serializer field, so a
        # `validate_description_html` method would never run for this path.
        # Sanitize it here — see the note on PageDetailSerializer for why.
        #
        # Reject when the sanitizer fails rather than falling through to the raw
        # value: `validate_html_content` signals failure with
        # `sanitized_html is None`, so a "use it only if it isn't None" guard
        # would store the *unsanitized* payload — the one outcome this whole path
        # exists to prevent. PageDetailSerializer, PageBinaryUpdateSerializer and
        # the issue/draft/project/workspace serializers all raise the same way.
        if description_html:
            is_valid, error_message, sanitized_html = validate_html_content(description_html)
            if not is_valid:
                raise serializers.ValidationError(error_message)
            description_html = sanitized_html if sanitized_html is not None else description_html

        # Get the workspace id from the project
        project = Project.objects.get(pk=project_id)

        # Create the page
        page = Page.objects.create(
            **validated_data,
            description_json=description_json,
            description_binary=description_binary,
            description_html=description_html,
            owned_by_id=owned_by_id,
            workspace_id=project.workspace_id,
        )

        # Create the project page
        ProjectPage.objects.create(
            workspace_id=page.workspace_id,
            project_id=project_id,
            page_id=page.id,
            created_by_id=page.created_by_id,
            updated_by_id=page.updated_by_id,
        )

        # Create page labels
        if labels is not None:
            PageLabel.objects.bulk_create(
                [
                    PageLabel(
                        label=label,
                        page=page,
                        workspace_id=page.workspace_id,
                        created_by_id=page.created_by_id,
                        updated_by_id=page.updated_by_id,
                    )
                    for label in labels
                ],
                batch_size=10,
            )
        return page

    def update(self, instance, validated_data):
        labels = validated_data.pop("labels", None)
        if labels is not None:
            PageLabel.objects.filter(page=instance).delete()
            PageLabel.objects.bulk_create(
                [
                    PageLabel(
                        label=label,
                        page=instance,
                        workspace_id=instance.workspace_id,
                        created_by_id=instance.created_by_id,
                        updated_by_id=instance.updated_by_id,
                    )
                    for label in labels
                ],
                batch_size=10,
            )

        return super().update(instance, validated_data)


class PageDetailSerializer(PageSerializer):
    description_html = serializers.CharField()

    def validate_description_html(self, value):
        """Sanitize the HTML content on write.

        Every other write path to `description_html` goes through
        `validate_html_content`; this serializer is the one that
        `PageViewSet.partial_update` uses, so it needs the same treatment.
        The wiki detail route renders `description_html` with
        `dangerouslySetInnerHTML`, so an unsanitized value here is stored XSS.
        """
        if not value:
            return value

        is_valid, error_message, sanitized_html = validate_html_content(value)
        if not is_valid:
            raise serializers.ValidationError(error_message)

        return sanitized_html if sanitized_html is not None else value

    class Meta(PageSerializer.Meta):
        fields = PageSerializer.Meta.fields + ["description_html"]


class ProjectPageTreeSerializer(PageSerializer):
    """``GET .../pages/?scope=all`` 的行：在 ``PageSerializer`` 之上多一个 ``node_type``。

    与 ``WikiPageTreeSerializer`` 同一条纪律 —— 只加在**子类**上，因为默认（不带 ``scope``）
    那条路径的响应必须逐字不变：多出来的键会顺着前端 ``mutateProperties``
    （一个盲写的 ``set(this, key, value)``）被写成页面实例上没人认识的属性。

    只加 ``node_type``、不加别的：前端拿它区分「文件夹行」与「页面行」。
    ``parent`` 已经在 ``PageSerializer.Meta.fields`` 里（`:45`），不必重复声明。
    """

    class Meta(PageSerializer.Meta):
        fields = [*PageSerializer.Meta.fields, "node_type"]
        read_only_fields = fields


class ProjectPageCreateSerializer(PageSerializer):
    """``POST .../pages/`` 的请求体 —— 在项目里**新建**页面或文件夹（罗盘 Round J）。

    ``node_type`` 只在这里可写，且 ``write_only=True``：类型建时定死。
    ``PageDetailSerializer``（``partial_update`` 用的那个）继承的是 ``PageSerializer``、
    **不是**这个类 —— 所以 PATCH 天生改不了类型，不需要额外写一条拒绝逻辑。
    这与 wiki 侧靠 ``WikiPageUpdateSerializer`` 不声明 ``node_type`` 是同一条纪律。

    ``ChoiceField`` 而不是 ``CharField``：合法集就是模型上的 ``NODE_TYPE_CHOICES``，
    写死一遍等于在序列化器里存了第二个真相源（与 ``WikiPageCreateSerializer.access`` 同）。
    ``default`` 是 ``"doc"`` —— 既有调用方（今天的前端发 `{access}`）一行不改。
    """

    node_type = serializers.ChoiceField(
        choices=Page.NODE_TYPE_CHOICES,
        required=False,
        default=Page.NODE_TYPE_DOC,
        write_only=True,
    )

    class Meta(PageSerializer.Meta):
        fields = [*PageSerializer.Meta.fields, "node_type"]


class PageVersionSerializer(BaseSerializer):
    class Meta:
        model = PageVersion
        fields = [
            "id",
            "workspace",
            "page",
            "last_saved_at",
            "owned_by",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
        ]
        read_only_fields = ["workspace", "page"]


class PageVersionDetailSerializer(BaseSerializer):
    class Meta:
        model = PageVersion
        fields = [
            "id",
            "workspace",
            "page",
            "last_saved_at",
            "description_binary",
            "description_html",
            "description_json",
            "owned_by",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
        ]
        read_only_fields = ["workspace", "page"]


class PageBinaryUpdateSerializer(serializers.Serializer):
    """Serializer for updating page binary description with validation"""

    description_binary = serializers.CharField(required=False, allow_blank=True)
    description_html = serializers.CharField(required=False, allow_blank=True)
    description_json = serializers.JSONField(required=False, allow_null=True)

    def validate_description_binary(self, value):
        """Validate the base64-encoded binary data"""
        if not value:
            return value

        try:
            # Decode the base64 data
            binary_data = base64.b64decode(value)

            # Validate the binary data
            is_valid, error_message = validate_binary_data(binary_data)
            if not is_valid:
                raise serializers.ValidationError(f"Invalid binary data: {error_message}")

            return binary_data
        except Exception as e:
            if isinstance(e, serializers.ValidationError):
                raise
            raise serializers.ValidationError("Failed to decode base64 data")

    def validate_description_html(self, value):
        """Validate the HTML content"""
        if not value:
            return value

        # Use the validation function from utils
        is_valid, error_message, sanitized_html = validate_html_content(value)
        if not is_valid:
            raise serializers.ValidationError(error_message)

        # Return sanitized HTML if available, otherwise return original
        return sanitized_html if sanitized_html is not None else value

    def update(self, instance, validated_data):
        """Update the page instance with validated data"""
        if "description_binary" in validated_data:
            instance.description_binary = validated_data.get("description_binary")

        if "description_html" in validated_data:
            instance.description_html = validated_data.get("description_html")

        if "description_json" in validated_data:
            instance.description_json = validated_data.get("description_json")

        instance.save()
        return instance
