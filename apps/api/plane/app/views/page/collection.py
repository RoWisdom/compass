# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import logging
import os

# Django imports
from django.db.models import Q
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response
from plane.utils.html_to_markdown import html_to_markdown

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import (
    PageCollectionSerializer,
    WikiPageCreateSerializer,
    WikiPageDetailSerializer,
    WikiPageIncludeSerializer,
    WikiPageSerializer,
    WikiPageUpdateSerializer,
)

# 直接取自子模块：`WikiPageTreeSerializer` 只服务于本文件的 `scope=all` 分支，
# 不进 serializers 包的公共出口（那会把它变成整个 app 都能 import 的名字）。
from plane.app.serializers.page_collection import WikiPageTreeSerializer
from plane.db.models import Page, PageCollection, Project, ProjectPage, Workspace
from plane.utils.error_codes import ERROR_CODES
from plane.utils.markdown_storage import (
    get_wiki_markdown_root,
    move_mirror_file,
    page_markdown_path,
    wiki_collection_directory,
    wiki_page_markdown_path,
    write_wiki_page_markdown,
)
from plane.utils.wiki_collections import GENERAL, PREDEFINED_KEYS, resolve_collection_key

# Local imports
from ..base import BaseViewSet

# READ-ONLY import from a sibling module that is under a HARD NO-WRITE
# prohibition (`views/page/base.py` carries unrelated uncommitted work).
# Importing its mirror helpers rather than copying them keeps one definition of
# how a page becomes markdown — do not "fix" this by inlining a copy.
from .base import (
    _page_ancestors,
    _project_name,
    _resolve_asset_url,
    _resolve_user_display_name,
    _write_page_mirror,
)

logger = logging.getLogger(__name__)

#: 与 ``import_wiki_markdown.EXTERNAL_SOURCE`` **同一个值**。刻意不 import 那个常量：
#: 它是管理命令模块里的东西，为了一根字符串就把整条 CLI 依赖链拉进视图模块不划算。
#: 两边一旦分叉，过滤会静默匹配不到任何行 —— ``test_view_constant_matches_the_importer``
#: 就是为此存在的（它直接把两个值比一遍）。
EXTERNAL_SOURCE = "obsidian-vault"


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

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def partial_update(self, request, slug, pk):
        """重命名集合。写契约只有 `name`（`sort_order` 已只读）。

        注释里刻意**不**复述 `WikiPageDescriptionViewSet.partial_update` 里那段关于
        deferrable 外键 / autocommit 的长说明：它解释的是**外键字段**写入的结局，
        而本端点没有外键可写 —— 照抄会产生一份与实际不符的第三份副本。
        这里只有一个作用域纪律要记：与 `WikiPageViewSet.partial_update` 同一套。

        返回体**不带 `page_count`**：那是 `list` 为了侧栏一次渲染完才现算的，重命名这一刀
        再算一遍就要复制那段计数查询，而调用方（弹窗）本来就只拿它判成功、随后由 store
        重拉整个集合列表。

        改名的副作用不止落在 DB 上：集合的名字有三份拷贝（``PageCollection.name``、
        vault 里的目录名 ``3-Wiki/<集合名>/``、导入时记进 ``external_id`` 的路径前缀），
        三份必须一起动 —— 搬目录与重写 ``external_id`` 前缀都由
        ``_rename_collection_mirror`` 完成，理由见它的 docstring。
        """
        collection = get_object_or_404(PageCollection.objects.filter(workspace__slug=slug), pk=pk)

        old_name = collection.name

        # `partial=True`：PATCH 的语义是「只改传了的字段」。序列化器的 `name` 是
        # required，不加 `partial` 的话一个只有 `{"name": ...}` 的载荷确实能过，
        # 但日后加字段时第一个只传单字段的调用方就会撞 400 —— 这里明确表态。
        serializer = PageCollectionSerializer(collection, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        collection = serializer.save()

        # 集合的名字是三份拷贝（`PageCollection.name`、vault 里的目录名、导入时记进
        # `external_id` 的路径前缀），改名必须让三份一起动 —— 理由见
        # `_rename_collection_mirror` 的 docstring。
        if collection.name != old_name:
            _rename_collection_mirror(collection, old_name)

        return Response(serializer.data, status=status.HTTP_200_OK)


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

        # `scope=all`：侧栏建树要一次拿到**所有**分区的页面（设计 B-5）。
        #
        # 独立参数，**不复用** `collection=all` —— `collection` 的值域是「预置键或
        # UUID」，往里塞一个哨兵值等于在一个已有的值域里开洞；独立参数不产生二义。
        #
        # 不带 `scope` 时行为逐字不变（`collection` 仍默认 GENERAL）：本改动对既有
        # 调用方是**完全向后兼容**的，`wiki-list-root.tsx` 那条按集合取数的路径
        # 不能被这次改动碰到。
        scope_all = request.GET.get("scope") == "all"
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

        # 分区键**只算一次**：`resolve_collection_key` 是有优先级的业务规则，全仓只有
        # 这一处实现。`scope=all` 时把它随行发给前端（B-6），否则拿它做过滤。
        resolved = [
            (
                page,
                resolve_collection_key(
                    archived_at=page.archived_at, access=page.access, collection_id=page.collection_id
                ),
            )
            for page in pages
        ]

        if scope_all:
            serializer = WikiPageTreeSerializer(
                [page for page, _ in resolved],
                many=True,
                context={"collection_keys": {page.id: key for page, key in resolved}},
            )
        else:
            serializer = WikiPageSerializer(
                [page for page, key in resolved if key == collection_key], many=True
            )

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

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def create_page(self, request, slug):
        """在 Wiki 里**新建**一个页面。

        与 ``create`` 分居两个动作：那个是「把已有页面收录进来」（``is_global=False``
        → ``True``），这个是「从零建一个 ``is_global=True`` 的页面」。共用一个 action
        名字会让两条完全不同的写路径挤在一处，所以路由也用字面段 ``create/`` 区分。

        权限与 ``create`` / ``partial_update`` / ``destroy`` 同一条收窄裁定：写端点
        不含 GUEST。

        与项目页的 ``PageViewSet.create``（``views/page/base.py:192-223``）**有意分叉**，
        **别以为这里漏抄了**：那个端点存盘后还会调 ``page_transaction.delay``（``:215``），
        这里**不调** —— Phase1B 裁定「wiki 的写路径不攒版本历史与事务记录」。
        （顺带校准一个容易记错的细节：那个 ``create`` 里也**没有**
        ``track_page_version.delay``，本仓唯一一处它在 ``:694``，属于正文端点
        ``PagesDescriptionViewSet.partial_update``。）
        **后果不可逆**：通过 Wiki 建出的页面不产生版本行，事后无法回填。
        """
        workspace = get_object_or_404(Workspace, slug=slug)

        serializer = WikiPageCreateSerializer(
            data=request.data,
            context={"workspace": workspace, "owned_by": request.user},
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # 两个外键都必须属于路径上的那个工作区，否则 404 且**一页都不建**。
        # 校验排在 save() 之前：404 路径下一个字段都不会动。
        #
        # 这里的纪律与 `create` / `partial_update` 对 collection_id 的前置查是同一条
        # （同一个 404、同一个 body），但**机制不同**，所以本端点的注释自己写一遍，
        # 不照抄那边关于 deferrable 外键 / autocommit 的长说明 —— 那段解释的是
        # "字段写入触发了延迟约束"的结局，而这里做的是"建页之前先校验归属"，
        # 抄过来会变成一份与实际不符的副本。
        project_id = serializer.validated_data.get("project_id")
        if project_id is not None:
            # `Project.objects` 是 `SoftDeletionManager`，已经滤掉 `deleted_at` 非空的行
            # （`db/mixins.py:56-58`），所以已删除的项目在这里同样落 404。
            if not Project.objects.filter(id=project_id, workspace=workspace).exists():
                return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)

        # 父页要同时满足**三条**，少一条都是洞（设计 B-2）：
        #   1. 属于本工作区 —— 与另外两个外键同一条；
        #   2. 对调用者**可见**（复用 `_visible_page_q`）—— 否则直接调 API 就能把子页挂到
        #      **别人的私有页**底下。这与「私有页不进收录候选」（本文件 `list` 分支）是
        #      **同一类纪律**：凡是「由调用者指定一个已存在的页面」的入口，都要过可见性；
        #   3. 本身**已收录**（`is_global=True`）—— 树只列已收录页，所以挂在未收录父页下面的
        #      子页会是一个**永远看不见的孤儿**。界面上点不出来（`＋` 只长在树的行上），
        #      但 API 能调出来。
        # 三条就在 `_wiki_page_queryset` 里 —— 工作区 + 可见 + 已收录，它已经逐条
        # 表达过（见其定义处的注释），这里只再用 `id` 收窄到**指名的那一页**，不把
        # 条件抄第二遍：抄一遍就多一处会与它走样的副本。
        # 校验排在 `save()` 之前：404 路径下**一页都不建**。
        parent = None
        parent_id = serializer.validated_data.get("parent")
        if parent_id is not None:
            parent = _wiki_page_queryset(request, slug).filter(id=parent_id).first()
            if parent is None:
                return Response({"error": "Parent page not found."}, status=status.HTTP_404_NOT_FOUND)

        # 建时继承（设计 B-3）：**没显式给**才继承。
        #
        # 判据是**原始请求体里有没有这个键**，不是 `validated_data` 里有没有值 ——
        # `access` 在序列化器上带 `default=`，缺键时 DRF 也会往 `validated_data` 里填，
        # 那里分不出「没传」与「传了 0」；`collection_id` 显式传 `null` 同理，
        # 那是「落 general」的表态，不该被父页的集合覆盖。
        #
        # 写回 `validated_data` 之后再 `save()`，于是**下面那条**集合归属校验
        # 顺带把继承来的值也验了 —— 不需要为继承的集合再写第二遍校验。
        if parent is not None:
            if "access" not in request.data:
                serializer.validated_data["access"] = parent.access
            if "collection_id" not in request.data:
                serializer.validated_data["collection_id"] = parent.collection_id

        collection_id = serializer.validated_data.get("collection_id")
        if collection_id is not None:
            target = PageCollection.objects.filter(id=collection_id, workspace__slug=slug).first()
            if target is None:
                return Response({"error": "Collection not found."}, status=status.HTTP_404_NOT_FOUND)

        page = serializer.save()

        if page.node_type == Page.NODE_TYPE_FOLDER:
            # 文件夹没有正文，也就没有可镜像的东西（设计 §5.2）。
            #
            # **不是**"镜像会失败"—— 是对一个没有正文的节点跑一遍 markdown 落盘，
            # 会在用户的**真实笔记库**里凭空生出一个 `<文件夹名>.md` 空文件。
            # 静默跳过，不 warn：这是**预期**路径，不是降级。
            pass
        else:
            # 把正文镜像成本地 ``.md``。**尽力而为**：无项目的页面由 ``_mirror_wiki_page``
            # 自己 warn 后跳过（镜像根 ``MARKDOWN_STORAGE_PATH`` 指向「项目」那一层，
            # 无项目页按定义无处可写 —— 见 ``_wiki_page_project_id`` 的 docstring）。
            # 无项目的页面照样建成功，那才是本端点的重点。
            _mirror_wiki_page(page, "<p></p>")

        return Response(WikiPageSerializer(page).data, status=status.HTTP_201_CREATED)

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

        if page.node_type == Page.NODE_TYPE_FOLDER:
            # 文件夹没有正文（Confluence F2 / F4）。与正文端点
            # （`WikiPageDescriptionViewSet.partial_update`）**同一条不变量、同一个错误码** ——
            # 那条路径负责落 vault 镜像，这条不落；但"文件夹没有正文"两条都必须成立，
            # 否则直连本端点就能给文件夹塞进一段正文。
            #
            # **只拦正文三个键，不拦整个端点**：本端点同时承载改名（name）与换集合
            # （collection_id），这两件事对文件夹是**合法**的 —— 标题防抖同步发的正是
            # `{ name }`（base-page.ts:200-220），换集合发的正是 `{ collection_id }`
            # （workspace-page.store.ts:454）。一刀切成 400 会把这两条既有能力一起砍掉。
            #
            # 三个键都拦：协同编辑器走的是 `description_binary`（Yjs 全量二进制），
            # 与 `description_html` / `description_json` 一样是正文（执行期裁定 3）。
            if {"description_html", "description_json", "description_binary"} & set(request.data.keys()):
                return Response(
                    {
                        "error_code": ERROR_CODES["PAGE_IS_FOLDER"],
                        "error_message": "PAGE_IS_FOLDER",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

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

        # 旧名与旧集合都必须在 save() **之前**记下来：serializer.update() 就地改
        # instance，save() 之后 page 上已经是新值，就再也算不出旧的了。
        old_name = page.name
        old_collection_id = page.collection_id

        page = serializer.save()

        # 换了集合 ⇒ 整棵子树跟着走（设计 B-4，裁定 6 明确接受这个代价）。
        # 只在真的变了时才走这一趟 —— 每次 PATCH 都遍历一遍子树是白烧。
        if page.collection_id != old_collection_id:
            _move_descendants_to_collection(page, page.collection_id)

        # 改名**或换集合**都会改变镜像路径（集合是路径的第一段，见 `_wiki_mirror_path`），
        # 与项目页路径同一条裁定（views/page/base.py:260-269）。两者任一变化都要搬。
        # 不搬的话：DB 说这页在新家、文件却留在旧家，下一次正文写入按新路径再写一份 ——
        # 同一个 frontmatter.id 出现两份。协同服务器的标题同步会对这个端点做防抖 PATCH，
        # 所以这是常规路径，不是边角。
        # 旧路径必须按 save() **之前**的集合与名字算：用保存后的集合去算旧路径，
        # 会指向一个不存在的文件，搬移静默失败、旧文件留在原集合文件夹里。
        # 搬移是尽力而为（镜像搬移内部吞 OSError），失败方向永远是「文件没搬」而不是「改名失败」。
        if page.name != old_name or page.collection_id != old_collection_id:
            _move_wiki_page_mirror(page, old_name, old_collection_id)

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


def _rename_collection_mirror(collection, old_name):
    """Follow a collection rename with the matching folder move and the matching
    ``external_id`` rewrite — see the module's ``_wiki_page_own_path`` for why all
    three copies of the name have to move together.

    Best-effort on the filesystem, like every other mirror call: a failure is
    logged, never raised — a read-only vault must not fail a rename.

    The recorded path is re-pointed **only** when the destination folder is free
    and the collection's files are either already there (the move succeeded) or
    not on disk at all. Every other exit — the destination is occupied, or
    ``os.replace`` failed — leaves it exactly where it was, because the files are
    still under the old name: re-pointing it would tell the next body write that
    whatever sits at the destination is this page's own file, and it would
    overwrite it. The failure direction is therefore "a stale folder / a duplicate
    file", never "someone else's file was written".
    """
    old_dir = wiki_collection_directory(old_name, collection.id)
    new_dir = wiki_collection_directory(collection.name, collection.id)
    vault_root = get_wiki_markdown_root().parent
    old_rel = old_dir.relative_to(vault_root).as_posix()
    new_rel = new_dir.relative_to(vault_root).as_posix()

    if old_dir == new_dir:
        # Both names land on the same folder (``_sanitize_name`` collapsed the
        # difference — "C  D" and "C D" are one folder on disk). The folder is
        # already the right one and the two relative paths are the same string,
        # so neither the disk nor the recorded path has anything to change.
        return

    if new_dir.exists():
        # The destination is occupied, so nothing is re-pointed into it. Refusing
        # is a **decision**, not a failure: the folder sitting there may be someone
        # else's (and merging two folders is destruction), and when nothing has been
        # mirrored yet there is simply nothing to move into it either. Either way the
        # rows keep pointing at the folder their files are really in, and a later
        # write under the new name lands on a ``-{id8}`` sibling instead of on
        # whatever is at the destination. This check has to come **before** the
        # "no folder to move" exit below: that exit re-points the rows, and doing
        # that into an occupied folder is how a hand-written note gets adopted and
        # then overwritten.
        logger.warning(
            "Not re-pointing collection mirror rows %s -> %s: the destination already exists, and "
            "claiming it is not something a rename may do. Rows keep pointing at %s.",
            old_rel,
            new_rel,
            old_rel,
        )
        return

    if not old_dir.exists():
        # Nothing mirrored yet — the folder appears under the new name on the next
        # body write. The recorded path is still re-pointed: it is what tells a
        # later write which file is this page's own. (Safe because the destination
        # was just proven free.)
        _reprefix_external_id(collection.workspace_id, old_rel, new_rel)
        return

    try:
        os.replace(old_dir, new_dir)
    except (OSError, UnicodeDecodeError) as exc:
        # Same reasoning as the refusal above: the folder did not move, so the
        # recorded path must not move either.
        logger.warning(
            "Failed to rename collection mirror directory %s -> %s: %s. Rows keep pointing at %s.",
            old_rel,
            new_rel,
            exc,
            old_rel,
        )
        return

    _reprefix_external_id(collection.workspace_id, old_rel, new_rel)


def _reprefix_external_id(workspace_id, old_rel, new_rel):
    """Re-point every imported row under ``<old_rel>/`` at ``<new_rel>/``.

    Only ever called once the destination folder is free and the collection's
    files are either already at the new name or not on disk at all — never after
    a refused or failed move, where the files are still under the old name and
    re-pointing them would hand a later body write someone else's file to
    overwrite. Exactly two of ``_rename_collection_mirror``'s exits call it; see
    its docstring for which two and why.

    ``old_rel``/``new_rel`` are folder paths **relative to the vault root**
    (``3-Wiki/<集合>``) — the same spelling ``Page.external_id`` uses, and the same
    spelling ``_wiki_page_own_path`` turns back into an absolute path. They are
    derived from the very folders the move used, so the pointer and the disk can
    no longer be computed from two different spellings of the name.

    Only rows this project's importer owns are touched (``external_source``), and
    the match is on **whole path segments** — renaming ``C`` must not touch ``C2``
    — hence the exact-or-slash-suffixed pair rather than a bare ``startswith``.
    ``<old_rel>`` **is** matched exactly too: that is the collection row's own
    shape, and a sub-directory page's shape (``3-Wiki/<old>/sub``).

    A row whose ``external_id`` spells the folder differently from what
    ``wiki_collection_directory`` computes (e.g. a collection imported from a
    vault folder named outside the writer) is left pointing at the old name —
    conservative, and it costs a duplicate file rather than a clobbered one.
    """
    prefix = f"{old_rel}/"

    for model in (Page, PageCollection):
        rows = (
            model.objects.filter(workspace_id=workspace_id, external_source=EXTERNAL_SOURCE)
            .filter(Q(external_id=old_rel) | Q(external_id__startswith=prefix))
            .values_list("id", "external_id")
        )
        for row_id, value in rows:
            # A single-column ``update`` rather than ``save()``: the only thing
            # changing is a pointer, and ``save()`` would drag every other field
            # and the model's own write path into a rename that has nothing to do
            # with them.
            model.objects.filter(id=row_id).update(external_id=new_rel + value[len(old_rel) :])


def _wiki_page_own_path(page):
    """The vault path this page was imported from, absolute — or ``None``.

    ``Page.external_id`` records a **vault-relative** path
    (``3-Wiki/<集合>/<笔记>.md``, see ``import_wiki_markdown``), so the vault
    root is the wiki root's parent. ``_resolve_page_path`` uses this to tell
    "the file this page owns" apart from a same-named note the user wrote by
    hand: without it, a page landing on a hand-written note's name would write
    over it. Factored out so the write and the move agree on one definition.
    """
    if not page.external_id:
        return None
    return get_wiki_markdown_root().parent / page.external_id


def _write_collection_page_mirror(page, collection, description_html):
    """Mirror a page body into its collection's folder under the wiki vault root.

    The project path mirrors through ``base._write_page_mirror``, which is
    project-scoped by construction (it resolves a directory name through
    ``Project.objects``). A collection is not a project, so this is the sibling
    writer rather than a flag on that one — ``base.py`` is under a hard
    no-write prohibition and its helper is left exactly as it is.

    Best-effort like every other mirror: ``write_wiki_page_markdown`` swallows
    ``OSError`` and logs, so a read-only vault cannot fail a page save.
    """
    user_cache, asset_cache = {}, {}
    markdown = html_to_markdown(
        description_html,
        resolve_user=lambda uid: _resolve_user_display_name(uid, user_cache),
        resolve_asset_url=lambda aid: _resolve_asset_url(aid, asset_cache),
    )
    write_wiki_page_markdown(
        collection_name=collection.name,
        collection_id=str(collection.id),
        ancestors=_page_ancestors(page.parent_id),
        page_id=str(page.id),
        name=page.name,
        markdown=markdown,
        own_path=_wiki_page_own_path(page),
    )


def _mirror_wiki_page(page, description_html):
    """Mirror a wiki page's body to a local ``.md``, or skip when it has no home.

    Routing is three-way and **collection-first** (design §3.3): a page in a
    collection mirrors under the wiki vault root, keyed by the collection name —
    the collection is the axis the user sees in the Wiki, so that is where the
    file belongs. A page with no collection but a live ``ProjectPage`` link
    keeps the pre-existing project behaviour verbatim. A page with neither is
    skipped and logged — see ``_wiki_page_project_id`` for why guessing a
    location is worse than writing nothing.
    """
    if page.collection_id is not None:
        collection = PageCollection.objects.filter(id=page.collection_id).first()
        if collection is not None:
            _write_collection_page_mirror(page, collection, description_html)
            return

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


def _wiki_mirror_path(page, *, collection_id, name, ancestors):
    """Resolve where ``page``'s mirror lives when keyed by ``collection_id``/``name``.

    Factored out of ``_mirror_wiki_page``/``_move_wiki_page_mirror`` because the
    *move* needs this for two different states at once: the old path must be
    computed from the page's **pre-save** collection and name. Reading them off
    the saved instance is what made a collection change silently move nothing.

    Routing is the same three-way, collection-first rule as ``_mirror_wiki_page``:
    collection root, else project root, else ``None`` (no home — see
    ``_wiki_page_project_id``). Returns ``None`` rather than a guessed path.
    """
    if collection_id is not None:
        collection = PageCollection.objects.filter(id=collection_id).first()
        if collection is not None:
            return wiki_page_markdown_path(
                collection_name=collection.name,
                collection_id=str(collection.id),
                ancestors=ancestors,
                name=name,
                page_id=str(page.id),
                own_path=_wiki_page_own_path(page),
            )
        # collection_id set but the row is gone — fall through to the project
        # branch, exactly as _mirror_wiki_page does.

    project_id = _wiki_page_project_id(page)
    if project_id is None:
        return None
    return page_markdown_path(
        project_name=_project_name(project_id),
        project_id=str(project_id),
        ancestors=ancestors,
        name=name,
        page_id=str(page.id),
    )


def _move_wiki_page_mirror(page, old_name, old_collection_id):
    """Move a wiki page's mirror after a rename, a collection change, or both.

    Both states are computed explicitly: the old path from ``old_name`` +
    ``old_collection_id`` (the values before ``save()``), the new path from the
    saved instance. A move that crosses roots (collection → project, or the
    reverse) is just two paths in different trees — nothing here cares which.

    When the new state has no home the old file is **left where it is** and a
    warning is logged: the wiki never deletes vault folders (design §243
    「删集合不删文件夹」), and a page leaving the wiki for a project-less
    limbo is not a reason to silently destroy the user's notes.

    The recorded path (``Page.external_id``) follows the file, so the next body write
    still recognises it as this page's own — see ``_repoint_page_external_id`` for the
    guards. One case is deliberately **not** covered: a descendant whose file moves
    merely because its *parent's* folder moved keeps a stale ``external_id`` — the
    pointer is only re-pointed when the page's own route runs. The next write for such
    a page lands on a ``-{id[:8]}`` sibling; the original is left alone.
    """
    ancestors = _page_ancestors(page.parent_id)
    old_path = _wiki_mirror_path(page, collection_id=old_collection_id, name=old_name, ancestors=ancestors)
    new_path = _wiki_mirror_path(page, collection_id=page.collection_id, name=page.name, ancestors=ancestors)

    if old_path is None:
        # Nothing on disk to move. Either the page never had a home, or it is
        # arriving at one for the first time — the next body write creates it.
        if new_path is None:
            logger.warning(
                "Skipping markdown mirror move for wiki page %s: no live ProjectPage link "
                "and no collection, so the page has no mirror root. The wiki never writes "
                "a page to a guessed location.",
                page.id,
            )
        return

    if new_path is None:
        logger.warning(
            "Orphaning markdown mirror for wiki page %s: the page now has neither a collection "
            "nor a live ProjectPage link, so it has no mirror root. The old file is left in "
            "place — the wiki never deletes vault files (design §243 「删集合不删文件夹」).",
            page.id,
        )
        return

    if old_path == new_path:
        # 路径没变（名字与集合都没动，或 `_sanitize_name` 把差别吃掉了）：
        # 磁盘与行指针都已经是对的，没有什么可跟。
        return

    moved = move_mirror_file(old_path, new_path, old_name, page.name, str(page.id))

    # `external_id` 是**定位符**不是来源凭证（与集合改名同一条裁定）：文件搬到哪，
    # 行指针就跟到哪。只有两种情况跟：
    #   · `moved` —— 文件确实被这次调用搬到了新路径；
    #   · 新路径上**什么都不存在** —— 那里没有别人的文件可以被认领，指针指向的是
    #     下一次写正文会落笔的地方。
    # 这两条之外就是不跟：目标位置已经躺着别人的文件时指针留在原处，下一次写正文
    # 只会多出一个 `-{id8}` 兄弟文件，绝不会覆盖它。
    if moved or not new_path.exists():
        _repoint_page_external_id(page, old_path, new_path)


def _repoint_page_external_id(page, old_path, new_path):
    """Let ``Page.external_id`` follow the mirror file — but only when it really
    named the old location.

    ``external_id`` is a **locator**, not an immutable record of provenance (the
    same ruling a collection rename is built on): the file moved, so the recorded
    path has to move with it. Left behind, the next body write resolves
    ``own_path`` off the stale value, fails to recognise the file it just moved as
    its own, and writes a ``-{id[:8]}`` sibling instead — one file at a time, the
    same defect a collection rename had for a whole folder.

    The row must be one this project's importer owns (``external_source``) — the same
    narrowing ``_reprefix_external_id`` applies — and the path it records must still be
    the old one. Three further guards, all in the direction of *not* changing anything:

    * the recorded path must be exactly ``old_path`` (vault-relative) — anything
      else points somewhere this move has nothing to say about;
    * both paths must be under the wiki root — ``external_id``-as-locator is a
      wiki-tree notion, and a page crossing to the project tree is not this
      function's business;
    * the caller has already established that the move happened or that the
      destination is empty (see ``_move_wiki_page_mirror``).

    One column, one ``update`` — the same discipline as ``_reprefix_external_id``.
    The in-memory instance is updated too, and that is not a nicety: the
    description endpoint moves the file and writes the body **in the same
    request**, resolving ``own_path`` off this very attribute — a database-only
    fix would leave that first write landing on the sibling anyway.
    """
    if not page.external_id or page.external_source != EXTERNAL_SOURCE:
        return

    vault_root = get_wiki_markdown_root().parent
    try:
        old_rel = old_path.relative_to(vault_root).as_posix()
        new_rel = new_path.relative_to(vault_root).as_posix()
    except ValueError:
        return
    if page.external_id != old_rel:
        return
    try:
        new_path.relative_to(get_wiki_markdown_root())
    except ValueError:
        return

    Page.objects.filter(id=page.id).update(external_id=new_rel)
    page.external_id = new_rel


#: 级联下行的深度上限。
#: `_page_ancestors`（`views/page/base.py`）往上走时用 20 自保，级联是**往下**走，
#: 同样需要一个界：畸形或成环的 parent 图会让遍历不终止。
MAX_SUBTREE_DEPTH = 20


def _descendant_ids(*, root, max_depth=MAX_SUBTREE_DEPTH):
    """``root`` 的**全部后代** id，不含它自己。广度优先、有界、去重。

    必须带 ``seen`` 集：``parent`` 是普通外键，**没有任何约束**禁止 A 的父是 B、
    B 的父是 A。今天 wiki 的路由改不了 ``parent``（``WikiPageUpdateSerializer``
    没有这个字段），环只能从数据层造出来 —— 但级联是**写**操作，撞上环会写成死循环。

    按 ``workspace_id`` 收窄：正常写入路径下后代必然同工作区（建页时 workspace 与
    parent 一起给），这一层过滤是给「数据被绕过 API 改过」留的边界 —— 级联是写操作，
    没有理由去写别的工作区的行。
    """
    seen = {root.id}
    frontier = [root.id]
    found = []

    for _ in range(max_depth):
        children = list(
            Page.objects.filter(workspace_id=root.workspace_id, parent_id__in=frontier).values_list("id", flat=True)
        )
        frontier = [child for child in children if child not in seen]
        if not frontier:
            break
        seen.update(frontier)
        found.extend(frontier)

    return found


def _move_descendants_to_collection(page, collection_id):
    """把 ``page`` 的整棵子树换到同一个集合（设计 B-4）。

    裁定 6 明确接受这条级联的代价：移动一个父页会连带移动用户**没有直接选中**的页面。
    """
    descendant_ids = _descendant_ids(root=page)
    if not descendant_ids:
        return

    # `QuerySet.update()` 绕过 auto_now：不显式给 `updated_at`，列表的默认排序键
    # （`Page.Meta.ordering = ("-created_at",)`，`db/models/page.py:71`）不动 ——
    # 但 `create` / `destroy` 两条既有的 update() 路径都显式刷新了时间戳，这里跟它们
    # 保持同一条纪律。
    # `updated_by` **不写**：与 include / remove 两条路径同一裁定 ——
    # 刷新时间戳，不把执行者盖到「最后编辑人」上。
    Page.objects.filter(id__in=descendant_ids).update(collection=collection_id, updated_at=timezone.now())


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

        if page.node_type == Page.NODE_TYPE_FOLDER:
            # 文件夹没有正文（Confluence F2 / F4）。
            #
            # **只拦正文三个键，不拦整个端点**：这个端点同时承载改名与换集合
            # （`WikiPageUpdateSerializer` 的字段），而这两件事对文件夹是**合法**的 ——
            # 侧栏的文件夹行靠改名，`move-to-collection` 靠换集合。一刀切成 400
            # 会把这两条既有能力一起砍掉。
            #
            # 三个键都拦：协同编辑器 PATCH 的是 `description_binary`（Yjs 全量二进制），
            # 它和 `description_html` / `description_json` 一样是正文。设计 §5.2 只点名
            # 了后两个 —— 那是设计漏了，见执行期裁定 3。
            #
            # 用集合求交而不是三个 `in` 串联：键名清单只有一份，将来正文键多一个
            # （或改名）时改一处。
            if {"description_html", "description_json", "description_binary"} & set(request.data.keys()):
                return Response(
                    {
                        "error_code": ERROR_CODES["PAGE_IS_FOLDER"],
                        "error_message": "PAGE_IS_FOLDER",
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

        # 旧名与旧集合都必须在 save() **之前**记下来：serializer.update() 就地改
        # instance，save() 之后 page.name / page.collection_id 已经是新值，
        # 就再也算不出旧路径了。
        old_name = page.name
        old_collection_id = page.collection_id

        page = serializer.save()

        # 改名**或换集合** = 镜像换路径。**必须先搬、后写正文**，顺序是载荷：
        # 镜像搬移做的是 old_path.replace(new_path)。如果正文镜像先把新路径写好了，
        # 这一搬就会拿旧文件把新正文盖掉 —— 内容静默丢失。
        # 先搬后写的最终状态才两个都对：一个文件、新路径、新正文。
        if page.name != old_name or page.collection_id != old_collection_id:
            _move_wiki_page_mirror(page, old_name, old_collection_id)

        # Mirror the page body as a local Markdown file (best-effort, skips
        # pages with no project — see _mirror_wiki_page).
        if request.data.get("description_html"):
            _mirror_wiki_page(page, request.data.get("description_html"))

        return Response({"message": "Updated successfully"})
