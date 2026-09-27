# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db.models import Q
from django.shortcuts import get_object_or_404

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import (
    PageCollectionSerializer,
    WikiPageDetailSerializer,
    WikiPageIncludeSerializer,
    WikiPageSerializer,
    WikiPageUpdateSerializer,
)
from plane.db.models import Page, PageCollection
from plane.utils.wiki_collections import GENERAL, PREDEFINED_KEYS, resolve_collection_key

# Local imports
from ..base import BaseViewSet


def _visible_page_q(user):
    """可见性条件：非私有页面人人可见，私有页面只有属主可见。

    私有 = 只看自己的。这里的判定刻意与 resolve_collection_key 对 private 的
    定义（``access == Page.PRIVATE_ACCESS``）用同一个常量，将来多出第三种
    access 值时两边不会打架。
    """
    return ~Q(access=Page.PRIVATE_ACCESS) | Q(owned_by=user)


def _wiki_page_queryset(request, slug):
    """工作区里「已收录进 Wiki」、且对调用者可见的页面。

    is_global=True 是收录标记 —— 项目页面不会自动出现在 Wiki 里。

    私有页面必须在这里滤掉：过滤若只写在某个 action 里，别的 action（以及
    共用本函数的计数端点）就会把整个工作区的私有页面元信息发给任何成员，
    侧栏还会出现「私有(5) 但列表 2 行」的口径分裂。
    """
    return Page.objects.filter(workspace__slug=slug, is_global=True).filter(_visible_page_q(request.user))


class PageCollectionViewSet(BaseViewSet):
    model = PageCollection

    def get_serializer_class(self):
        return PageCollectionSerializer

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def list(self, request, slug):
        pages = list(_wiki_page_queryset(request, slug).values("id", "archived_at", "access", "collection_id"))

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
        # 收录弹窗要的是「还没收录的」页面 —— 同一个端点把过滤方向反过来，
        # 于是「未收录」的定义（is_global）在整个仓库里只有一处。
        if request.GET.get("include_candidates") == "true":
            candidates = (
                Page.objects.filter(workspace__slug=slug, is_global=False)
                .select_related("workspace")
                .select_related("owned_by")
                .order_by("-updated_at")
            )
            return Response(WikiPageSerializer(candidates, many=True).data, status=status.HTTP_200_OK)

        collection_key = request.GET.get("collection", GENERAL)
        pages = _wiki_page_queryset(request, slug).select_related("workspace").select_related("owned_by")
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

        # 集合必须是本工作区的 —— 与 partial_update 同一套校验。缺了它，别家的
        # 集合 id 会被直接写进 FK（页面在本工作区落不进任何分区，等于从侧栏
        # 消失），不存在的 id 则在提交时炸成 500
        collection = None
        if collection_id is not None:
            collection = PageCollection.objects.filter(id=collection_id, workspace__slug=slug).first()
            if collection is None:
                return Response({"error": "Collection not found."}, status=status.HTTP_404_NOT_FOUND)

        # 只收录本工作区、且对调用者可见的页面 —— 防止跨工作区越权写入，也防止
        # 别人的私有页面被 is_global=True 发布进共享的私有分区。别人的私有页面
        # 与别的工作区的页面一样：静默跳过，不进 included 计数，不报 403
        pages = Page.objects.filter(id__in=page_ids, workspace__slug=slug).filter(_visible_page_q(request.user))
        updated = pages.update(is_global=True, collection=collection)
        return Response({"included": updated}, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def retrieve(self, request, slug, page_id):
        """单个已收录页面（含正文）。

        作用域全部继承自 _wiki_page_queryset：未收录、别的工作区、别人的私有
        页面一律 404 —— 这里不再叠第二层过滤。
        """
        page = get_object_or_404(_wiki_page_queryset(request, slug), pk=page_id)
        return Response(WikiPageDetailSerializer(page).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def partial_update(self, request, slug, page_id):
        """换集合（collection_id）与/或写正文（description_*），两组可同时给。"""
        page = get_object_or_404(_wiki_page_queryset(request, slug), pk=page_id)

        serializer = WikiPageUpdateSerializer(page, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # 集合必须是本工作区的 —— 与 create 同一套校验、同一个 404、同一个 body。
        # 校验排在写之前：404 路径下正文一个字段都不会动
        collection_id = serializer.validated_data.get("collection_id")
        if collection_id is not None:
            target = PageCollection.objects.filter(id=collection_id, workspace__slug=slug).first()
            if target is None:
                return Response({"error": "Collection not found."}, status=status.HTTP_404_NOT_FOUND)

        page = serializer.save()
        return Response(WikiPageDetailSerializer(page).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def destroy(self, request, slug, page_id):
        page = _wiki_page_queryset(request, slug).filter(id=page_id).first()
        if page is None:
            return Response({"error": "Page not found in this wiki."}, status=status.HTTP_404_NOT_FOUND)

        # 移出 Wiki 只是取消收录，绝不删除页面本身 —— 页面承载版本历史与评论
        Page.objects.filter(id=page.id).update(is_global=False, collection=None)
        return Response(status=status.HTTP_204_NO_CONTENT)
