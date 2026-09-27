# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import logging

# Django imports
from django.db.models import Q
from django.http import StreamingHttpResponse
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
from plane.db.models import Page, PageCollection, ProjectPage, Workspace
from plane.utils.error_codes import ERROR_CODES
from plane.utils.markdown_storage import move_page_markdown
from plane.utils.wiki_collections import GENERAL, PREDEFINED_KEYS, resolve_collection_key

# Local imports
from ..base import BaseViewSet

# READ-ONLY import from a sibling module that is under a HARD NO-WRITE
# prohibition (`views/page/base.py` carries unrelated uncommitted work).
# Importing its mirror helpers rather than copying them keeps one definition of
# how a page becomes markdown — do not "fix" this by inlining a copy.
from .base import _page_ancestors, _project_name, _write_page_mirror

logger = logging.getLogger(__name__)


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

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def create(self, request, slug):
        """新建集合。

        权限**不含 GUEST** —— 承设计 §4.2 对写端点的收窄裁定（读端点 `list` 保留 GUEST）。
        """
        # `owned_by` 是模型上的必填 FK，而序列化器的写契约只有 `name` ——
        # 归属只能在这里给，别把它加进序列化器（那会让调用方能伪造别人的 owned_by）。
        workspace = get_object_or_404(Workspace, slug=slug)

        serializer = PageCollectionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        collection = serializer.save(workspace=workspace, owned_by=request.user)

        # 带上 `page_count`，与 `list` 里的每一行同形 —— 前端拿到 201 就能直接塞进侧栏。
        # 新集合必然是 0：它刚建出来，还没有任何页面能指向它。
        return Response(
            {**PageCollectionSerializer(collection).data, "page_count": 0},
            status=status.HTTP_201_CREATED,
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
                # 三个条件与上游 `PageViewSet.get_queryset()` 里那条**逐条一致**
                # （工作区当前在 `views/page/base.py:156-158`；该文件带无关的未提交改动、
                #   行号会漂，要核对请按下面的 kwargs 去 grep，别只信行号），且写在
                # **同一条 Q 内**，这样它们绑定到**同一行** join 记录。拆成三次 `.filter()`
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
                # 一条渲染的，重复看得见。上游 `PageViewSet.get_queryset()` 那条 queryset
                # 末尾也用了 `.distinct()`（工作区当前在 `views/page/base.py:189`），同理 ——
                # 这条去重是修复的一部分，不是可选项。
                .distinct()
                .select_related("workspace")
                .select_related("owned_by")
                .order_by("-updated_at")
            )
            return Response(WikiPageSerializer(candidates, many=True).data, status=status.HTTP_200_OK)

        collection_key = request.GET.get("collection", GENERAL)
        # 列表行不需要正文，但 `_wiki_page_queryset` 是**整行**取出来的 —— 其中
        # `description_binary` 是 Yjs 协同文档的全量二进制、`description_json` 是
        # jsonb 正文（`db/models/page.py:33-35`）。整行取出来只为在 Python 里读三个
        # 字段（archived_at / access / collection_id，都在 WikiPageSerializer.Meta.fields
        # 里），然后把行交给一个**根本不输出这些列**的序列化器。
        # 姊妹端点 `PageCollectionViewSet.list` 已经用 `.values(...)` 只取 4 列（:60），同理。
        # 这里用 `.defer` 而不是 `.only`：`.only` 漏一个字段就会在序列化时触发逐行补查
        # （N+1），而 `.defer` 只是把重列移出 SELECT，其余行为完全不变。
        pages = (
            _wiki_page_queryset(request, slug)
            .select_related("workspace")
            .select_related("owned_by")
            .defer("description_json", "description_binary", "description_html", "description_stripped")
        )
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
        # 消失）；不存在的 id **不会**炸成 500（原先这里这么写，是错的）——
        # 外键约束是 deferrable 的，请求照样以 200 返回、坏写入到事务收尾才炸，
        # 生产走 autocommit 时则由 handle_exception 兜成 400
        # {"error": "The payload is not valid"}（views/base.py:70-84）
        collection = None
        if collection_id is not None:
            collection = PageCollection.objects.filter(id=collection_id, workspace__slug=slug).first()
            if collection is None:
                return Response({"error": "Collection not found."}, status=status.HTTP_404_NOT_FOUND)

        # 这里按**工作区**收窄，而不是像候选分支那样再按「我参与的项目」：
        # 收录/移出是**工作区级**动作（ADMIN/MEMBER 执行），「已收录页面对全工作区可见」
        # 是工作区级 Wiki 的设计意图；而候选列表是**发现**面 —— 它会把未收录页的**标题**
        # 摆给调用者看，那才是需要按项目收窄的泄漏面（见 list 分支的候选过滤器）。
        # create/destroy 操作的是调用者**已经知道 id** 的页面，没有这层泄漏。
        # 这个不对称是有意的，别「修」成一致。
        #
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
        """换集合（collection_id）、改标题（name）与/或写正文（description_*），可任意组合。"""
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

        # 旧名必须在 save() **之前**记下来：serializer.update() 就地改 instance，
        # save() 之后 page.name 已经是新值，就再也算不出旧路径了。
        old_name = page.name

        page = serializer.save()

        # 改名 = 镜像文件换路径，与项目页路径同一条裁定（views/page/base.py:260-269）。
        # 不搬的话：DB 改了名、vault 里留下旧文件，下一次正文写入按新标题再写一份 ——
        # 同一个 frontmatter.id 出现两份。协同服务器的标题同步会对这个端点做防抖 PATCH，
        # 所以这是常规路径，不是边角。搬移是尽力而为（move_page_markdown 内部吞 OSError），
        # 失败方向永远是「文件没搬」而不是「改名失败」。
        if page.name != old_name:
            _move_wiki_page_mirror(page, old_name)

        return Response(WikiPageDetailSerializer(page).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def destroy(self, request, slug, page_id):
        # 作用域与 create 同一条裁定：按**工作区**收窄，不按「我参与的项目」。
        # 与 create 一样，操作对象是调用者已经知道 id 的页面，收窄在这里只是让
        # 工作区级动作变得不可预期；需要按项目收窄的是候选**发现**面，不是这里。
        page = _wiki_page_queryset(request, slug).filter(id=page_id).first()
        if page is None:
            return Response({"error": "Page not found in this wiki."}, status=status.HTTP_404_NOT_FOUND)

        # 移出 Wiki 只是取消收录，绝不删除页面本身 —— 页面承载版本历史与评论。
        # 同 create：QuerySet.update() 绕过 auto_now，updated_at 要显式传。
        Page.objects.filter(id=page.id).update(is_global=False, collection=None, updated_at=timezone.now())
        return Response(status=status.HTTP_204_NO_CONTENT)


def _wiki_page_project_id(page):
    """Resolve the project a wiki page mirrors into, or ``None`` when it has none.

    ``_write_page_mirror`` is project-scoped by construction: it resolves a
    directory name through ``Project.objects.filter(pk=project_id)``, and
    ``MARKDOWN_STORAGE_PATH`` points at the projects folder itself
    (``…/ObsidianVault/2-项目``). A page with no live ``ProjectPage`` link
    therefore has **no directory to be written into** — not "we have not
    written it yet", but "there is nowhere to put it". Guessing a location
    would put a file somewhere the user did not ask for.

    Such pages are reachable: ``WikiPageViewSet.create`` only excludes private
    and archived pages from inclusion, so a public page belonging to no project
    can be included in the wiki. The ruling (design §2.3c, 2026-09-27) is to
    skip and log — the failure direction is "no file", never "a file in the
    wrong place". The write and the move both honour it, which is why they
    share this resolver instead of each deciding for itself.

    Multi-project pages: a page may belong to several projects. ``.first()``
    picks an arbitrary one, which is fine while the data is 1:1 (it is today:
    3 pages, 1 project) but is a real decision the day it is not. Noted here
    rather than solved, because Phase 1B has nothing to base the choice on.
    """
    return (
        ProjectPage.objects.filter(page_id=page.id, deleted_at__isnull=True)
        .values_list("project_id", flat=True)
        .first()
    )


def _mirror_wiki_page(page, description_html):
    """Mirror a wiki page's body to a local ``.md``; skip if it has no project.

    See ``_wiki_page_project_id`` for why a project-less page is skipped
    rather than guessed at.
    """
    project_id = _wiki_page_project_id(page)

    if project_id is None:
        logger.warning(
            "Skipping markdown mirror for wiki page %s: no live ProjectPage link. "
            "MARKDOWN_STORAGE_PATH points at the projects directory, so a page with no "
            "project has no folder to mirror into.",
            page.id,
        )
        return

    _write_page_mirror(
        project_id,
        page.id,
        page.name,
        _page_ancestors(page.parent_id),
        description_html,
    )


def _move_wiki_page_mirror(page, old_name):
    """Move a wiki page's ``.md`` after a rename; skip if it has no project.

    The mirror path is name-derived, so a rename that is not carried into the
    filesystem leaves the old file behind and lets the next body write create a
    second one under the new title — two files, one ``frontmatter.id``.

    The wiki metadata route never reparents, so old and new ancestors are the
    same list; both are passed because that is ``move_page_markdown``'s
    contract. Best-effort like every other mirror call: it swallows ``OSError``
    and logs, so a read-only vault cannot fail a rename.
    """
    project_id = _wiki_page_project_id(page)

    if project_id is None:
        logger.warning(
            "Skipping markdown mirror move for wiki page %s: no live ProjectPage link. "
            "MARKDOWN_STORAGE_PATH points at the projects directory, so a page with no "
            "project has no folder to mirror into.",
            page.id,
        )
        return

    ancestors = _page_ancestors(page.parent_id)
    move_page_markdown(
        project_name=_project_name(project_id),
        project_id=str(project_id),
        old_ancestors=ancestors,
        new_ancestors=ancestors,
        page_id=str(page.id),
        old_name=old_name,
        new_name=page.name,
    )


class WikiPageDescriptionViewSet(BaseViewSet):
    """Wiki 页面的正文二进制端点 —— 协同编辑器（``apps/live``）的数据源。

    ``GET``   → ``application/octet-stream``，回 ``description_binary``
    ``PATCH`` → 收 ``TDocumentPayload``，走 ``WikiPageUpdateSerializer`` 的正文校验

    这是 ``PagesDescriptionViewSet``（``views/page/base.py``）的 wiki 姊妹端点，
    但**不是它的逐行复制** —— 副作用被收窄了。项目页那个端点存盘后做三件事：
    ``_write_page_mirror`` / ``page_transaction.delay`` / ``track_page_version.delay``；
    这里**只做镜像**。裁定见 ``罗盘-Wiki编辑器-Phase1B-设计.md`` §4：本阶段不攒
    版本历史与事务记录，通过 Wiki 产生的编辑**不进版本历史，也无法回填**。

    将来若有人来补 wiki 的版本功能，别以为这里漏抄了 —— 是有意不抄的，
    并且上面那条「不产生版本行」的测试正是为此存在的。
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def retrieve(self, request, slug, page_id):
        page = get_object_or_404(_wiki_page_queryset(request, slug), pk=page_id)
        binary_data = page.description_binary

        def stream_data():
            if binary_data:
                yield binary_data
            else:
                yield b""

        response = StreamingHttpResponse(stream_data(), content_type="application/octet-stream")
        response["Content-Disposition"] = 'attachment; filename="page_description.bin"'
        return response

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def partial_update(self, request, slug, page_id):
        page = get_object_or_404(_wiki_page_queryset(request, slug), pk=page_id)

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

        serializer = WikiPageUpdateSerializer(page, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # 集合必须是本工作区的 —— 与 metadata 路由（WikiPageViewSet.partial_update）同一个校验、
        # 同一个 404、同一个 body。复用 WikiPageUpdateSerializer 就是连带接受它声明的每个字段，
        # 所以这里欠着这条前置检查：缺了它，别的工作区的 collection_id 会被直接写成本页的 FK，
        # 而不存在的 UUID 会一路落到 DB。
        #
        # 缺守卫时**不会**给你一个 500 —— 别照直觉猜（这条注释原先就写错成 500，已按实测改正）：
        #   · FK 约束是 deferrable 的，所以请求会以 **200** 正常返回，违规直到事务收尾
        #     （测试里是 teardown 的 SET CONSTRAINTS ALL IMMEDIATE）才炸出来；
        #   · 生产走 autocommit 时则由 BaseViewSet.handle_exception 把 IntegrityError 兜成
        #     **400** {"error": "The payload is not valid"}（views/base.py:70-84）。
        # 两条路都不告诉调用方是哪个字段错了，而测试那条**看起来像写入成功了** —— 比 500 更难发现。
        #
        # 校验排在 save() 之前：404 路径下正文一个字段都不会动。
        collection_id = serializer.validated_data.get("collection_id")
        if collection_id is not None:
            target = PageCollection.objects.filter(id=collection_id, workspace__slug=slug).first()
            if target is None:
                return Response({"error": "Collection not found."}, status=status.HTTP_404_NOT_FOUND)

        # 旧名必须在 save() **之前**记下来：serializer.update() 就地改 instance，
        # save() 之后 page.name 已经是新值，就再也算不出旧路径了。
        old_name = page.name

        page = serializer.save()

        # 改名 = 镜像换路径。**必须先搬、后写正文**，顺序是载荷：
        # move_page_markdown 做的是 old_path.replace(new_path)。如果正文镜像先把新路径写好了，
        # 这一搬就会拿旧文件把新正文盖掉 —— 内容静默丢失。
        # 先搬后写的最终状态才两个都对：一个文件、新名字、新正文。
        if page.name != old_name:
            _move_wiki_page_mirror(page, old_name)

        # Mirror the page body as a local Markdown file (best-effort, skips
        # pages with no project — see _mirror_wiki_page).
        if request.data.get("description_html"):
            _mirror_wiki_page(page, request.data.get("description_html"))

        return Response({"message": "Updated successfully"})
