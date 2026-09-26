# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import PageCollectionSerializer
from plane.db.models import Page, PageCollection
from plane.utils.wiki_collections import PREDEFINED_KEYS, resolve_collection_key

# Local imports
from ..base import BaseViewSet


def _wiki_page_queryset(slug):
    """工作区里「已收录进 Wiki」的页面。

    is_global=True 是收录标记 —— 项目页面不会自动出现在 Wiki 里。
    """
    return Page.objects.filter(workspace__slug=slug, is_global=True)


class PageCollectionViewSet(BaseViewSet):
    model = PageCollection

    def get_serializer_class(self):
        return PageCollectionSerializer

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def list(self, request, slug):
        pages = list(_wiki_page_queryset(slug).values("id", "archived_at", "access", "collection_id"))

        counts = {key: 0 for key in PREDEFINED_KEYS}
        per_collection = {}
        for page in pages:
            key = resolve_collection_key(
                archived_at=page["archived_at"],
                access=page["access"],
                collection_id=page["collection_id"],
            )
            if key in counts:
                counts[key] += 1
            else:
                per_collection[key] = per_collection.get(key, 0) + 1

        collections = PageCollection.objects.filter(workspace__slug=slug)
        data = [
            {**PageCollectionSerializer(collection).data, "page_count": per_collection.get(str(collection.id), 0)}
            for collection in collections
        ]

        return Response(
            {
                "predefined": [{"key": key, "page_count": counts[key]} for key in PREDEFINED_KEYS],
                "collections": data,
            },
            status=status.HTTP_200_OK,
        )
