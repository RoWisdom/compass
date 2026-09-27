# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

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
            # 可见性过滤不能省：候选列表把工作区里**所有**未收录页的标题摆给任何成员看，
            # 别人的私有页也在其中。这条不变量只写在 _visible_page_q 里，凡是
            # Page 查询都要带上（本文件 docstring 已写明；:87/:215/:390 各有测试锁住）。
            candidates = (
                Page.objects.filter(workspace__slug=slug, is_global=False)
                .filter(_visible_page_q(request.user))
                # 私有页**不进候选**，自己的也不进。候选摆的是「收录到这个分区」，
                # 而私有页的归属由 access 决定（resolve_collection_key 的优先级是
                # archived > private > 用户集合 > general）—— 收录后它必落 private 分区，
                # 不是用户当时所在的那个。摆出来只会让人以为「收录到这里」。
                .exclude(access=Page.PRIVATE_ACCESS)
                # 归档页同理，而且理由更强：archived 是 resolve_collection_key 的**第一**优先级。
                # 归档页的归属由 archived_at 决定，从 general 收录它必落「归档」分区。
                # `Page.archived_at` 是 DateField，`isnull=True` 即「没归档」。
                .filter(archived_at__isnull=True)
                # 候选只列「我参与的项目」的页面（+ 不属于任何项目的页面）——
                # 三个条件与上游读取路径逐条一致（`views/page/base.py:151-157`），且写在
                # **同一条 Q 内**，这样它们绑定到**同一行** join 记录。拆成两次 `.filter()`
                # 会各自生成一个 join，于是「我参与的 A 项目」与「已停用的 B 项目成员行」
                # 能分别满足条件、页面被误判为可见 —— 等于没修。
                # 列表分支不这样收窄：已收录页面对全工作区可见是工作区级 Wiki 的设计意图。
                .filter(
                    Q(projects__isnull=True)
                    | Q(
                        projects__project_projectmember__member=request.user,
                        projects__project_projectmember__is_active=True,
                        projects__archived_at__isnull=True,
                    )
                )
                # 上面那条 join 是多对多的：一个页面经 ProjectPage 属于多个项目、
                # 而我参与其中两个 ⇒ join 出两行 ⇒ 候选里出现两次。候选在 UI 上是一条
                # 一条渲染的，重复看得见。上游带同一个 join 的那条 queryset 末尾也用了
                # `.distinct()`（`views/page/base.py:189`），同理 —— 这条去重是修复的
                # 一部分，不是可选项。
                .distinct()
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

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
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
        # QuerySet.update() 绕过 auto_now：不显式给 updated_at，列表的默认排序键
        # （-updated_at）不会刷新。
        updated = pages.update(is_global=True, collection=collection, updated_at=timezone.now())
        return Response({"included": updated}, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def retrieve(self, request, slug, page_id):
        """单个已收录页面（含正文）。

        作用域全部继承自 _wiki_page_queryset：未收录、别的工作区、别人的私有
        页面一律 404 —— 这里不再叠第二层过滤。
        """
        page = get_object_or_404(_wiki_page_queryset(request, slug), pk=page_id)
        return Response(WikiPageDetailSerializer(page).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
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

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def destroy(self, request, slug, page_id):
        page = _wiki_page_queryset(request, slug).filter(id=page_id).first()
        if page is None:
            return Response({"error": "Page not found in this wiki."}, status=status.HTTP_404_NOT_FOUND)

        # 移出 Wiki 只是取消收录，绝不删除页面本身 —— 页面承载版本历史与评论。
        # 同 create：QuerySet.update() 绕过 auto_now，updated_at 要显式传。
        Page.objects.filter(id=page.id).update(is_global=False, collection=None, updated_at=timezone.now())
        return Response(status=status.HTTP_204_NO_CONTENT)
