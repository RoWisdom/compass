# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import json
from datetime import datetime
from django.core.serializers.json import DjangoJSONEncoder

# Django imports
from django.db import connection
from django.db.models import (
    Exists,
    OuterRef,
    Q,
    Value,
    UUIDField,
    Count,
    Case,
    When,
    IntegerField,
)
from django.http import StreamingHttpResponse
from django.contrib.postgres.aggregates import ArrayAgg
from django.contrib.postgres.fields import ArrayField
from django.db.models.functions import Coalesce
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import allow_permission, ROLE
from plane.app.serializers import (
    PageSerializer,
    PageDetailSerializer,
    PageBinaryUpdateSerializer,
)
# 直接取自子模块 —— 两个都只服务于本文件的 `scope=all` / 项目侧建页分支，
# 不进 serializers 包的公共出口（理由与 `collection.py:32-34` 逐字相同）。
from plane.app.serializers.page import ProjectPageCreateSerializer, ProjectPageTreeSerializer
from plane.db.models import (
    FileAsset,
    Page,
    PageLog,
    Project,
    ProjectMember,
    ProjectPage,
    User,
    UserFavorite,
    UserRecentVisit,
)
from plane.utils.error_codes import ERROR_CODES
from plane.utils.html_to_markdown import html_to_markdown
from plane.utils.markdown_storage import (
    delete_page_markdown,
    get_markdown_root,
    move_page_markdown,
    page_markdown_path,
    write_page_markdown,
)

# Local imports
from ..base import BaseAPIView, BaseViewSet
from .cascade import _cascade_delete_pages, _descendant_ids
from plane.bgtasks.page_transaction_task import page_transaction
from plane.bgtasks.page_version_task import track_page_version
from plane.bgtasks.recent_visited_task import recent_visited_task
from plane.bgtasks.copy_s3_object import copy_s3_objects_of_description_and_assets
from plane.app.permissions import ProjectPagePermission


def unarchive_archive_page_and_descendants(page_id, archived_at):
    # Your SQL query
    sql = """
    WITH RECURSIVE descendants AS (
        SELECT id FROM pages WHERE id = %s
        UNION ALL
        SELECT pages.id FROM pages, descendants WHERE pages.parent_id = descendants.id
    )
    UPDATE pages SET archived_at = %s WHERE id IN (SELECT id FROM descendants);
    """

    # Execute the SQL query
    with connection.cursor() as cursor:
        cursor.execute(sql, [page_id, archived_at])


def _project_name(project_id):
    """Return a project's human name, falling back to its id when absent."""
    return Project.objects.filter(pk=project_id).values_list("name", flat=True).first() or str(project_id)


def _project_mirror_root(project_id):
    """Return the Markdown mirror root for a project's pages.

    One query, the same shape as ``_project_name`` above: the workspace a project
    belongs to carries the configured root. Falls back to ``None`` (which
    ``get_markdown_root`` reads as "use the env var / built-in default") when the
    project row is gone, so a deleted project can never crash a mirror call.
    """
    project = Project.objects.select_related("workspace").filter(pk=project_id).first()
    return get_markdown_root(project.workspace if project else None)


def _page_ancestors(parent_id):
    """Return a page's ancestor chain as [(name, id), ...], root-first.

    Walks the ``parent`` self-FK upward; depth-capped as a safety net against a
    malformed/cyclic parent graph.
    """
    ancestors = []
    depth = 0
    while parent_id and depth < 20:
        parent = Page.objects.filter(pk=parent_id).only("id", "name", "parent_id").first()
        if parent is None:
            break
        ancestors.append((parent.name or "", str(parent.id)))
        parent_id = parent.parent_id
        depth += 1
    ancestors.reverse()
    return ancestors


def _resolve_user_display_name(user_id, cache):
    if user_id not in cache:
        user = User.objects.filter(pk=user_id).first()
        name = None
        if user:
            name = user.display_name or (user.first_name + " " + user.last_name).strip() or user.email
        cache[user_id] = name or None
    return cache[user_id]


def _resolve_asset_url(asset_id, cache):
    if asset_id not in cache:
        asset = FileAsset.objects.filter(pk=asset_id).first()
        cache[asset_id] = asset.asset_url if asset else None
    return cache[asset_id]


def _write_page_mirror(project_id, page_id, name, ancestors, description_html):
    """Convert a page's HTML to Markdown and mirror it locally (best-effort)."""
    user_cache, asset_cache = {}, {}
    markdown = html_to_markdown(
        description_html,
        resolve_user=lambda uid: _resolve_user_display_name(uid, user_cache),
        resolve_asset_url=lambda aid: _resolve_asset_url(aid, asset_cache),
    )
    write_page_markdown(
        project_name=_project_name(project_id),
        project_id=str(project_id),
        ancestors=ancestors,
        page_id=str(page_id),
        name=name,
        markdown=markdown,
        root=_project_mirror_root(project_id),
    )


def _project_mirror_target_resolver(project_id):
    """返回**项目侧**的镜像寻址函数，交给 ``_cascade_delete_pages`` 当 ``resolve_mirror``。

    与 ``_write_page_mirror`` / ``destroy`` 的既有落盘路径逐字同源：项目名 + 项目 id +
    祖先链 + 页面名，根是该项目的镜像根。``ancestors`` 按 ``row.parent_id`` 现算 ——
    与 ``destroy`` 里 ``_page_ancestors(page.parent_id)`` 同一口径。

    **项目名与镜像根只查一次**：它们不随行变化，而 ``_project_mirror_root`` 每次都打一条
    SQL —— 一棵子树几十行就是几十条重复查询。
    """
    project_name = _project_name(project_id)
    root = _project_mirror_root(project_id)

    def resolve(row):
        return (
            page_markdown_path(
                project_name=project_name,
                project_id=str(project_id),
                ancestors=_page_ancestors(row.parent_id),
                name=row.name,
                page_id=str(row.id),
                root=root,
            ),
            root,
        )

    return resolve


def _can_delete_page(request, slug, project_id, page):
    """「页面所有者，或者本项目的 admin」—— 删页面与删文件夹**共用**这一条。

    裁定 甲只豁免「先归档」那一条，**不**豁免权限。抽成函数是为了让两条岔路上的
    判定逐字相同：抄一遍就有了会漂的第二份。
    """
    if page.owned_by_id == request.user.id:
        return True
    return ProjectMember.objects.filter(
        workspace__slug=slug,
        member=request.user,
        role=20,
        project_id=project_id,
        is_active=True,
    ).exists()


class PageViewSet(BaseViewSet):
    serializer_class = PageSerializer
    model = Page
    permission_classes = [ProjectPagePermission]
    search_fields = ["name"]

    def get_queryset(self):
        subquery = UserFavorite.objects.filter(
            user=self.request.user,
            entity_type="page",
            entity_identifier=OuterRef("pk"),
            workspace__slug=self.kwargs.get("slug"),
        )
        # 注意：`parent__isnull=True` **不在**这条链里 —— 它被挪到末尾条件化叠加，
        # 理由见下面那段注释。其余每一个过滤/注解/排序**逐字未动**。
        queryset = (
            super()
            .get_queryset()
            .filter(workspace__slug=self.kwargs.get("slug"))
            .filter(
                projects__project_projectmember__member=self.request.user,
                projects__project_projectmember__is_active=True,
                projects__archived_at__isnull=True,
            )
            .filter(Q(owned_by=self.request.user) | Q(access=0))
            .prefetch_related("projects")
            .select_related("workspace")
            .select_related("owned_by")
            .annotate(is_favorite=Exists(subquery))
            .order_by(self.request.GET.get("order_by", "-created_at"))
            .prefetch_related("labels")
            .order_by("-is_favorite", "-created_at")
            .annotate(
                project=Exists(
                    ProjectPage.objects.filter(page_id=OuterRef("id"), project_id=self.kwargs.get("project_id"))
                )
            )
            .annotate(
                label_ids=Coalesce(
                    ArrayAgg(
                        "page_labels__label_id",
                        distinct=True,
                        filter=~Q(page_labels__label_id__isnull=True),
                    ),
                    Value([], output_field=ArrayField(UUIDField())),
                ),
                project_ids=Coalesce(
                    ArrayAgg("projects__id", distinct=True, filter=~Q(projects__id=True)),
                    Value([], output_field=ArrayField(UUIDField())),
                ),
            )
            .filter(project=True)
            .distinct()
        )

        # 「只返回根节点」是**上游为列表**加的一条过滤，但它挂在 `get_queryset` 上，
        # 于是另外两个动作也跟着吃（罗盘 Round J，裁定 乙）：
        #
        #   · `retrieve`（本文件 `:306`）= `self.get_queryset().filter(pk=page_id).first()`
        #     ⇒ 子页被这条过滤吃掉 ⇒ **详情永远 404**；
        #   · `create`（本文件 `:238`）= `self.get_queryset().get(pk=<新页 id>)`
        #     ⇒ 刚建出来的子页同样被吃掉 ⇒ 抛 `Page.DoesNotExist` ⇒ 落进
        #     `BaseViewSet.handle_exception` 的 `ObjectDoesNotExist` 分支
        #     （`app/views/base.py:92-96`）⇒ **404，不是 500**
        #     （页面与镜像其实都落库了，客户端看到的却是一个错误）。
        #
        # 今天没有入口能建出子页，所以这两个缺口看不见。本轮一旦能建子页，它们立刻变成
        # 「建得出来、打不开」。所以这里改成**只在 list 且不带 `scope=all` 时**叠加：
        # 默认列表逐字不变，另外两个动作顺带修好。
        #
        # `?scope=all` 不是「另一个列表端点」—— 它就是**同一份 queryset 少一条 where**，
        # 其余过滤（工作区、项目成员、access、`is_favorite` 注解与排序）一条都不碰。
        if self.action == "list" and self.request.GET.get("scope") != "all":
            queryset = queryset.filter(parent__isnull=True)

        return self.filter_queryset(queryset)

    def create(self, request, slug, project_id):
        serializer = ProjectPageCreateSerializer(
            data=request.data,
            context={
                "project_id": project_id,
                "owned_by_id": request.user.id,
                "description_json": request.data.get("description_json", {}),
                "description_binary": request.data.get("description_binary", None),
                "description_html": request.data.get("description_html", "<p></p>"),
            },
        )

        if serializer.is_valid():
            page = serializer.save()
            # 文件夹**两样都跳过**（罗盘 Round J，设计 §4.3）：
            #
            #   · 镜像 —— **不是**「镜像会失败」。对一个没有正文的节点跑一遍 markdown
            #     落盘，会在用户的**真实笔记库**里凭空生出一个 `<文件夹名>.md` 空文件。
            #     静默跳过、不 warn：这是**预期**路径，不是降级。与
            #     `collection.py` 里 wiki 侧那条同源守卫逐字同源。
            #   · `page_transaction` —— 它记的是**正文**的版本流水，文件夹没有正文。
            #
            # 文件夹的目录段由它的子页面镜像在磁盘上带出来（`_resolve_page_path` 会对每个
            # 祖先叠一层目录），所以「文件夹自己不落盘」不等于「目录不存在」。
            if page.node_type != Page.NODE_TYPE_FOLDER:
                # Mirror the page as a local Markdown file (best-effort)
                _write_page_mirror(
                    project_id,
                    serializer.data["id"],
                    request.data.get("name"),
                    _page_ancestors(request.data.get("parent")),
                    request.data.get("description_html", "<p></p>"),
                )
                # capture the page transaction
                page_transaction.delay(
                    new_description_html=request.data.get("description_html", "<p></p>"),
                    old_description_html=None,
                    page_id=serializer.data["id"],
                )
            page = self.get_queryset().get(pk=serializer.data["id"])
            serializer = PageDetailSerializer(page)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def partial_update(self, request, slug, project_id, page_id):
        try:
            page = Page.objects.get(
                pk=page_id,
                workspace__slug=slug,
                projects__id=project_id,
                project_pages__deleted_at__isnull=True,
            )

            if page.is_locked:
                return Response({"error": "Page is locked"}, status=status.HTTP_400_BAD_REQUEST)

            # 目标位置。两条判定都排在 `serializer.save()` **之前** —— 400 路径下
            # **一个字段都不会动**。
            #
            # 改了两处（罗盘 Round J，设计 §4.4）：
            #
            #   ① 用 `.first()` + 自己回 400，而不是 `Page.objects.get(...)`。原来那句
            #      抛的 `Page.DoesNotExist` 会被**外层那个 try 的 `except
            #      Page.DoesNotExist`（本文件 `:299`）吞掉**，回一句「Access cannot be
            #      updated...」—— 目标不存在却报「权限不足」，误导。
            #   ② 补环检测。`parent` 是普通外键，数据层不禁止 A 的父是 B、B 的父是 A；
            #      写入路径应当拒绝，否则会造出**渲染成「根」**的畸形数据。判据与 wiki
            #      侧逐字同一条（`collection.py` 的 `partial_update`）：
            #      `target_parent.id == page.id or target_parent.id in set(descendant_ids(root=page))`
            #      —— 直接复用 `_descendant_ids`，不自己写遍历（它自带 `seen` 集与深度封顶）。
            #
            # 子树遍历与级联只有**一份**实现，住在 `cascade.py`：`collection.py` 在模块层
            # `from .base import _page_ancestors`，本文件在模块层反向导入它必然成环，所以
            # 两边都从谁都不依赖的 `cascade.py` 取。

            parent = request.data.get("parent", None)
            if parent:
                target_parent = (
                    Page.objects.filter(
                        pk=parent,
                        workspace__slug=slug,
                        projects__id=project_id,
                        project_pages__deleted_at__isnull=True,
                    )
                    .only("id", "parent_id")
                    .first()
                )
                if target_parent is None:
                    return Response(
                        {"error": "The target parent page does not belong to this project"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if target_parent.id == page.id or target_parent.id in set(_descendant_ids(root=page)):
                    return Response(
                        {"error": "Cannot move a page into itself or its own descendant"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            # Only update access if the page owner is the requesting  user
            if page.access != request.data.get("access", page.access) and page.owned_by_id != request.user.id:
                return Response(
                    {"error": "Access cannot be updated since this page is owned by someone else"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            serializer = PageDetailSerializer(page, data=request.data, partial=True)
            page_description = page.description_html
            old_name = page.name
            old_parent_id = page.parent_id
            if serializer.is_valid():
                serializer.save()
                # Mirror a rename/reparent as a local Markdown move (best-effort)
                if page.name != old_name or page.parent_id != old_parent_id:
                    move_page_markdown(
                        project_name=_project_name(project_id),
                        project_id=str(project_id),
                        old_ancestors=_page_ancestors(old_parent_id),
                        new_ancestors=_page_ancestors(page.parent_id),
                        page_id=str(page_id),
                        old_name=old_name,
                        new_name=page.name,
                        root=_project_mirror_root(project_id),
                    )
                # capture the page transaction
                if request.data.get("description_html"):
                    page_transaction.delay(
                        new_description_html=request.data.get("description_html", "<p></p>"),
                        old_description_html=page_description,
                        page_id=page_id,
                    )

                return Response(serializer.data, status=status.HTTP_200_OK)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Page.DoesNotExist:
            return Response(
                {"error": "Access cannot be updated since this page is owned by someone else"},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def retrieve(self, request, slug, project_id, page_id=None):
        page = self.get_queryset().filter(pk=page_id).first()
        project = Project.objects.get(pk=project_id)
        track_visit = request.query_params.get("track_visit", "true").lower() == "true"

        """
        if the role is guest and guest_view_all_features is false and owned by is not
        the requesting user then dont show the page
        """

        if (
            ProjectMember.objects.filter(
                workspace__slug=slug,
                project_id=project_id,
                member=request.user,
                role=5,
                is_active=True,
            ).exists()
            and not project.guest_view_all_features
            and not page.owned_by == request.user
        ):
            return Response(
                {"error": "You are not allowed to view this page"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if page is None:
            return Response({"error": "Page not found"}, status=status.HTTP_404_NOT_FOUND)
        else:
            issue_ids = PageLog.objects.filter(page_id=page_id, entity_name="issue").values_list(
                "entity_identifier", flat=True
            )
            data = PageDetailSerializer(page).data
            data["issue_ids"] = issue_ids
            if track_visit:
                recent_visited_task.delay(
                    slug=slug,
                    entity_name="page",
                    entity_identifier=page_id,
                    user_id=request.user.id,
                    project_id=project_id,
                )
            return Response(data, status=status.HTTP_200_OK)

    def lock(self, request, slug, project_id, page_id):
        page = Page.objects.get(
            pk=page_id,
            workspace__slug=slug,
            projects__id=project_id,
            project_pages__deleted_at__isnull=True,
        )

        page.is_locked = True
        page.save()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def unlock(self, request, slug, project_id, page_id):
        page = Page.objects.get(
            pk=page_id,
            workspace__slug=slug,
            projects__id=project_id,
            project_pages__deleted_at__isnull=True,
        )

        page.is_locked = False
        page.save()

        return Response(status=status.HTTP_204_NO_CONTENT)

    def access(self, request, slug, project_id, page_id):
        access = request.data.get("access", 0)
        page = Page.objects.get(
            pk=page_id,
            workspace__slug=slug,
            projects__id=project_id,
            project_pages__deleted_at__isnull=True,
        )

        # Only update access if the page owner is the requesting user
        if page.access != request.data.get("access", page.access) and page.owned_by_id != request.user.id:
            return Response(
                {"error": "Access cannot be updated since this page is owned by someone else"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        page.access = access
        page.save()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def list(self, request, slug, project_id):
        queryset = self.get_queryset()
        project = Project.objects.get(pk=project_id)
        if (
            ProjectMember.objects.filter(
                workspace__slug=slug,
                project_id=project_id,
                member=request.user,
                role=5,
                is_active=True,
            ).exists()
            and not project.guest_view_all_features
        ):
            queryset = queryset.filter(owned_by=request.user)
        # 树那条路径多一个 `node_type`（前端要据此区分文件夹行），且**只多这一个键**。
        # 单独一个子类而不是给 `PageSerializer` 加字段：默认路径的响应必须逐字不变
        # （与 `WikiPageTreeSerializer` 同一条纪律）。
        serializer_class = ProjectPageTreeSerializer if request.GET.get("scope") == "all" else PageSerializer
        pages = serializer_class(queryset, many=True).data
        return Response(pages, status=status.HTTP_200_OK)

    def archive(self, request, slug, project_id, page_id):
        page = Page.objects.get(
            pk=page_id,
            workspace__slug=slug,
            projects__id=project_id,
            project_pages__deleted_at__isnull=True,
        )

        # only the owner or admin can archive the page
        if (
            ProjectMember.objects.filter(
                project_id=project_id, member=request.user, is_active=True, role__lte=15
            ).exists()
            and request.user.id != page.owned_by_id
        ):
            return Response(
                {"error": "Only the owner or admin can archive the page"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        UserFavorite.objects.filter(
            entity_type="page",
            entity_identifier=page_id,
            project_id=project_id,
            workspace__slug=slug,
        ).delete()

        unarchive_archive_page_and_descendants(page_id, datetime.now())

        return Response({"archived_at": str(datetime.now())}, status=status.HTTP_200_OK)

    def unarchive(self, request, slug, project_id, page_id):
        page = Page.objects.get(
            pk=page_id,
            workspace__slug=slug,
            projects__id=project_id,
            project_pages__deleted_at__isnull=True,
        )

        # only the owner or admin can un archive the page
        if (
            ProjectMember.objects.filter(
                project_id=project_id, member=request.user, is_active=True, role__lte=15
            ).exists()
            and request.user.id != page.owned_by_id
        ):
            return Response(
                {"error": "Only the owner or admin can un archive the page"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # if parent archived then page will be un archived breaking hierarchy
        if page.parent_id and page.parent.archived_at:
            page.parent = None
            page.save(update_fields=["parent"])

        unarchive_archive_page_and_descendants(page_id, None)

        return Response(status=status.HTTP_204_NO_CONTENT)

    def destroy(self, request, slug, project_id, page_id):
        page = Page.objects.get(
            pk=page_id,
            workspace__slug=slug,
            projects__id=project_id,
            project_pages__deleted_at__isnull=True,
        )

        # 文件夹：**豁免「先归档」那一条**（裁定 甲），其余照跑 —— 权限判定照它原有的
        # 403 语义走，与上面的单页路径**共用同一个函数**（`_can_delete_page`）。
        if page.node_type == Page.NODE_TYPE_FOLDER:
            if not _can_delete_page(request, slug, project_id, page):
                return Response(
                    {"error": "Only admin or owner can delete the page"},
                    status=status.HTTP_403_FORBIDDEN,
                )

            # 整棵子树（含文件夹自己）。取法与 wiki 的 `_destroy_folder` 逐字同源：
            # `_descendant_ids` 按 `workspace_id` 收窄（裁定 丁）。
            subtree_ids = _descendant_ids(root=page)
            rows = list(Page.objects.filter(id__in=[page.id, *subtree_ids]).prefetch_related("projects"))

            # 同一份级联实现，只换寻址函数（设计 §4.6）。
            # 顺序是载荷：读行 + 算路径 → 一条 SQL 软删 → 删镜像 → 收空目录。
            _cascade_delete_pages(rows, resolve_mirror=_project_mirror_target_resolver(project_id))

            # ⚠️ `_cascade_delete_pages` 走**一条 SQL 批量 update**，绕过
            # `SoftDeleteModel.delete()`，也就绕过了它会 `.delay()` 的
            # `soft_delete_related_objects`（`db/mixins.py:72-78`）。项目页比 wiki 页多
            # 一层 `ProjectPage` through 行，那条级联**碰不到它** —— 在这里补一刀
            # （设计 §8.4）。少了它，直接走 `ProjectPage.objects` 的读者
            # （如 `analytic/advance.py` 的 `total_pages`）会数到已经删掉的页面。
            #
            # 补在**项目侧这一层**而不是写进 `_cascade_delete_pages`：那个 helper 是 wiki
            # 与项目**共用**的，把项目专属的 through 表清理塞进去，等于让 wiki 的级联也去
            # 打一张与它无关的表 —— T-B5「wiki 行为逐字不变」就不再是平凡成立的了。
            now = timezone.now()
            ProjectPage.objects.filter(page_id__in=[row.id for row in rows]).update(
                deleted_at=now, updated_at=now
            )

            # 收藏与最近访问：与下面单页路径的后半段逐字同源，只是从「一个 id」变成「一组 id」。
            UserFavorite.objects.filter(
                project=project_id,
                workspace__slug=slug,
                entity_identifier__in=[row.id for row in rows],
                entity_type="page",
            ).delete()
            UserRecentVisit.objects.filter(
                project_id=project_id,
                workspace__slug=slug,
                entity_identifier__in=[row.id for row in rows],
                entity_name="page",
            ).delete(soft=False)

            return Response(status=status.HTTP_204_NO_CONTENT)

        # ---- 以下**逐字未动**：删单个页面（裁定 甲只豁免文件夹那一条）----

        if page.archived_at is None:
            return Response(
                {"error": "The page should be archived before deleting"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not _can_delete_page(request, slug, project_id, page):
            return Response(
                {"error": "Only admin or owner can delete the page"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Mirror: capture the page location and direct children before deleting
        page_name = page.name
        ancestors = _page_ancestors(page.parent_id)
        project_name = _project_name(project_id)
        children = list(
            Page.objects.filter(
                parent_id=page_id,
                projects__id=project_id,
                workspace__slug=slug,
                project_pages__deleted_at__isnull=True,
            ).values_list("id", "name")
        )

        # remove parent from all the children
        _ = Page.objects.filter(
            parent_id=page_id,
            projects__id=project_id,
            workspace__slug=slug,
            project_pages__deleted_at__isnull=True,
        ).update(parent=None)

        page.delete()

        # Mirror: delete the page's .md and lift its sub-pages one level up
        delete_page_markdown(
            project_name=project_name,
            project_id=str(project_id),
            ancestors=ancestors,
            page_id=str(page_id),
            name=page_name,
            root=_project_mirror_root(project_id),
        )
        for child_id, child_name in children:
            move_page_markdown(
                project_name=project_name,
                project_id=str(project_id),
                old_ancestors=ancestors + [(page_name or "", str(page_id))],
                new_ancestors=ancestors,
                page_id=str(child_id),
                old_name=child_name,
                new_name=child_name,
                root=_project_mirror_root(project_id),
            )
        # Delete the user favorite page
        UserFavorite.objects.filter(
            project=project_id,
            workspace__slug=slug,
            entity_identifier=page_id,
            entity_type="page",
        ).delete()
        # Delete the page from recent visit
        UserRecentVisit.objects.filter(
            project_id=project_id,
            workspace__slug=slug,
            entity_identifier=page_id,
            entity_name="page",
        ).delete(soft=False)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def summary(self, request, slug, project_id):
        queryset = (
            Page.objects.filter(workspace__slug=slug)
            .filter(
                projects__project_projectmember__member=self.request.user,
                projects__project_projectmember__is_active=True,
                projects__archived_at__isnull=True,
            )
            .filter(parent__isnull=True)
            .filter(Q(owned_by=request.user) | Q(access=0))
            .annotate(
                project=Exists(
                    ProjectPage.objects.filter(page_id=OuterRef("id"), project_id=self.kwargs.get("project_id"))
                )
            )
            .filter(project=True)
            .distinct()
        )

        project = Project.objects.get(pk=project_id)
        if (
            ProjectMember.objects.filter(
                workspace__slug=slug,
                project_id=project_id,
                member=request.user,
                role=ROLE.GUEST.value,
                is_active=True,
            ).exists()
            and not project.guest_view_all_features
        ):
            queryset = queryset.filter(owned_by=request.user)

        stats = queryset.aggregate(
            public_pages=Count(
                Case(
                    When(access=Page.PUBLIC_ACCESS, archived_at__isnull=True, then=1),
                    output_field=IntegerField(),
                )
            ),
            private_pages=Count(
                Case(
                    When(access=Page.PRIVATE_ACCESS, archived_at__isnull=True, then=1),
                    output_field=IntegerField(),
                )
            ),
            archived_pages=Count(Case(When(archived_at__isnull=False, then=1), output_field=IntegerField())),
        )

        return Response(stats, status=status.HTTP_200_OK)


class PageFavoriteViewSet(BaseViewSet):
    model = UserFavorite

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def create(self, request, slug, project_id, page_id):
        _ = UserFavorite.objects.create(
            project_id=project_id,
            entity_identifier=page_id,
            entity_type="page",
            user=request.user,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def destroy(self, request, slug, project_id, page_id):
        page_favorite = UserFavorite.objects.get(
            project=project_id,
            user=request.user,
            workspace__slug=slug,
            entity_identifier=page_id,
            entity_type="page",
        )
        page_favorite.delete(soft=False)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PagesDescriptionViewSet(BaseViewSet):
    permission_classes = [ProjectPagePermission]

    def retrieve(self, request, slug, project_id, page_id):
        page = Page.objects.get(
            Q(owned_by=self.request.user) | Q(access=0),
            pk=page_id,
            workspace__slug=slug,
            projects__id=project_id,
            project_pages__deleted_at__isnull=True,
        )
        binary_data = page.description_binary

        def stream_data():
            if binary_data:
                yield binary_data
            else:
                yield b""

        response = StreamingHttpResponse(stream_data(), content_type="application/octet-stream")
        response["Content-Disposition"] = 'attachment; filename="page_description.bin"'
        return response

    def partial_update(self, request, slug, project_id, page_id):
        page = Page.objects.get(
            Q(owned_by=self.request.user) | Q(access=0),
            pk=page_id,
            workspace__slug=slug,
            projects__id=project_id,
            project_pages__deleted_at__isnull=True,
        )

        if page.is_locked:
            return Response(
                {
                    "error_code": ERROR_CODES["PAGE_LOCKED"],
                    "error_message": "PAGE_LOCKED",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if page.archived_at:
            return Response(
                {
                    "error_code": ERROR_CODES["PAGE_ARCHIVED"],
                    "error_message": "PAGE_ARCHIVED",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Store the old description_html before saving (needed for both tasks)
        old_description_html = page.description_html

        # Serialize the existing instance
        existing_instance = json.dumps({"description_html": old_description_html}, cls=DjangoJSONEncoder)

        # Use serializer for validation and update
        serializer = PageBinaryUpdateSerializer(page, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()

            # Mirror the page content as a local Markdown file (best-effort)
            if request.data.get("description_html"):
                _write_page_mirror(
                    project_id,
                    page_id,
                    page.name,
                    _page_ancestors(page.parent_id),
                    request.data.get("description_html"),
                )

            # Capture the page transaction
            if request.data.get("description_html"):
                page_transaction.delay(
                    new_description_html=request.data.get("description_html", "<p></p>"),
                    old_description_html=old_description_html,
                    page_id=page_id,
                )

            # Run background tasks
            track_page_version.delay(
                page_id=page_id,
                existing_instance=existing_instance,
                user_id=request.user.id,
            )
            return Response({"message": "Updated successfully"})
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PageDuplicateEndpoint(BaseAPIView):
    permission_classes = [ProjectPagePermission]

    def post(self, request, slug, project_id, page_id):
        page = Page.objects.get(
            pk=page_id,
            workspace__slug=slug,
            projects__id=project_id,
            project_pages__deleted_at__isnull=True,
        )

        # check for permission
        if page.access == Page.PRIVATE_ACCESS and page.owned_by_id != request.user.id:
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        # get all the project ids where page is present
        project_ids = ProjectPage.objects.filter(page_id=page_id).values_list("project_id", flat=True)

        page.pk = None
        page.name = f"{page.name} (Copy)"
        page.description_binary = None
        page.owned_by = request.user
        page.created_by = request.user
        page.updated_by = request.user
        page.save()

        for project_id in project_ids:
            ProjectPage.objects.create(
                workspace_id=page.workspace_id,
                project_id=project_id,
                page_id=page.id,
                created_by_id=page.created_by_id,
                updated_by_id=page.updated_by_id,
            )

        page_transaction.delay(
            new_description_html=page.description_html,
            old_description_html=None,
            page_id=page.id,
        )

        # Copy the s3 objects uploaded in the page
        copy_s3_objects_of_description_and_assets.delay(
            entity_name="PAGE",
            entity_identifier=page.id,
            project_id=project_id,
            slug=slug,
            user_id=request.user.id,
        )

        page = (
            Page.objects.filter(pk=page.id)
            .annotate(
                project_ids=Coalesce(
                    ArrayAgg("projects__id", distinct=True, filter=~Q(projects__id=True)),
                    Value([], output_field=ArrayField(UUIDField())),
                )
            )
            .first()
        )
        serializer = PageDetailSerializer(page)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
