# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import (
    PageCollectionSerializer,
    WikiPageIncludeSerializer,
    WikiPageMoveSerializer,
    WikiPageSerializer,
)
from plane.db.models import Page, PageCollection
from plane.utils.wiki_collections import GENERAL, PREDEFINED_KEYS, resolve_collection_key

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


class WikiPageViewSet(BaseViewSet):
    """Wiki 里的页面 —— 收录 / 移出 / 换集合。

    收录与移出都只动 ``is_global`` 和 ``collection`` 两个字段：页面本身承载
    版本历史与评论，移出 Wiki 不等于删除页面。
    """

    model = Page

    def get_serializer_class(self):
        return WikiPageSerializer

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def list(self, request, slug):
        collection_key = request.GET.get("collection", GENERAL)
        pages = _wiki_page_queryset(slug).select_related("workspace").select_related("owned_by")
        pages = [
            page
            for page in pages
            if resolve_collection_key(
                archived_at=page.archived_at, access=page.access, collection_id=page.collection_id
            )
            == collection_key
        ]
        serializer = WikiPageSerializer(pages, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def create(self, request, slug):
        serializer = WikiPageIncludeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        page_ids = serializer.validated_data["page_ids"]
        collection_id = serializer.validated_data.get("collection_id")

        # 只收录本工作区的页面 —— 防止跨工作区越权写入
        pages = Page.objects.filter(id__in=page_ids, workspace__slug=slug)
        updated = pages.update(is_global=True, collection_id=collection_id)
        return Response({"included": updated}, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def partial_update(self, request, slug, page_id):
        serializer = WikiPageMoveSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        page = _wiki_page_queryset(slug).filter(id=page_id).first()
        if page is None:
            return Response({"error": "Page not found in this wiki."}, status=status.HTTP_404_NOT_FOUND)

        collection_id = serializer.validated_data.get("collection_id")
        target = None
        if collection_id is not None:
            target = PageCollection.objects.filter(id=collection_id, workspace__slug=slug).first()
            if target is None:
                return Response({"error": "Collection not found."}, status=status.HTTP_404_NOT_FOUND)

        Page.objects.filter(id=page.id).update(collection=target)
        page.refresh_from_db()
        return Response(WikiPageSerializer(page).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def destroy(self, request, slug, page_id):
        page = _wiki_page_queryset(slug).filter(id=page_id).first()
        if page is None:
            return Response({"error": "Page not found in this wiki."}, status=status.HTTP_404_NOT_FOUND)

        # 移出 Wiki 只是取消收录，绝不删除页面本身 —— 页面承载版本历史与评论
        Page.objects.filter(id=page.id).update(is_global=False, collection=None)
        return Response(status=status.HTTP_204_NO_CONTENT)
