# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import Page, PageCollection

from .base import BaseSerializer


class PageCollectionSerializer(BaseSerializer):
    """用户自建集合。

    只暴露元信息，不暴露页面列表 —— 页面走 /wiki-pages/ 端点。
    """

    class Meta:
        model = PageCollection
        fields = ["id", "name", "sort_order", "created_at"]
        read_only_fields = ["id", "created_at"]


class WikiPageSerializer(serializers.ModelSerializer):
    """Wiki 列表里的页面行。

    刻意不复用 PageSerializer：那个类的 label_ids / project_ids 依赖 queryset
    里的 ArrayAgg 注解，直接序列化模型实例会炸；而且列表不需要描述正文。
    """

    collection_id = serializers.UUIDField(read_only=True, allow_null=True)

    class Meta:
        model = Page
        fields = [
            "id",
            "name",
            "access",
            "color",
            "parent",
            "is_locked",
            "archived_at",
            "workspace",
            "owned_by",
            "collection_id",
            "logo_props",
            "view_props",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class WikiPageIncludeSerializer(serializers.Serializer):
    """POST /wiki-pages/ 的请求体 —— 把已有页面收录进 Wiki。"""

    page_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)
    collection_id = serializers.UUIDField(required=False, allow_null=True)


class WikiPageMoveSerializer(serializers.Serializer):
    """PATCH /wiki-pages/<page_id>/ 的请求体 —— 换集合。"""

    collection_id = serializers.UUIDField(required=False, allow_null=True)
