# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import logging
import os
import uuid

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
    GENERAL_DIRECTORY,
    delete_page_file,
    get_markdown_root,
    get_wiki_markdown_root,
    move_mirror_file,
    page_markdown_path,
    prune_empty_directories,
    wiki_collection_directory,
    wiki_general_page_markdown_path,
    wiki_page_markdown_path,
    write_wiki_general_page_markdown,
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


def _general_name_error(name):
    """``name`` 撞上「常规」时的 400 载荷，否则 ``None``。

    判据刻意用常量比较（``GENERAL_DIRECTORY``）而不是字符串字面量：目录名只在一个
    地方定义，改的时候不会漏掉这里。

    为什么要挡：``_sanitize_name("常规")`` 原样返回，所以一个叫「常规」的普通集合，
    它算出的目录与本轮的 ``wiki_general_directory`` **是同一个 ``Path``** ——
    两个语义共用一个文件夹，页面的归属就看不出区别了。

    形状照 DRF 的字段错误（``{"name": [...]}``）—— 前端 ``create_modal`` /
    ``edit_modal`` 已有 ``toasts.create_error`` / ``toasts.rename_error`` 兜底，
    不需要为这条新写任何文案。
    """
    if name == GENERAL_DIRECTORY:
        return {"name": [f"Collection name '{GENERAL_DIRECTORY}' is reserved for the General partition."]}
    return None


class PageCollectionViewSet(BaseViewSet):
    model = PageCollection

    def get_serializer_class(self):
        return PageCollectionSerializer

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def list(self, request, slug):
        pages = list(
            _wiki_page_queryset(request, slug).values("id", "archived_at", "access", "collection_id", "node_type")
        )

        counts = {key: 0 for key in PREDEFINED_KEYS}
        per_collection = {}
        for page in pages:
            # 文件夹**不进任何计数**（Confluence F14：它是一类，不是页面）。
            #
            # 用 `continue` 而不是在 queryset 上 `.exclude(node_type=...)`：后者会把
            # 「哪些行该被排除」这条规则从它的**消费者**旁边挪到一处看不见的地方，
            # 而本方法就是全仓唯一读 `node_type` 做过滤的计数点 —— 规则留在循环里，
            # 读一遍循环就知道口径。这也是设计 §5.2 写的机制。
            if page["node_type"] == Page.NODE_TYPE_FOLDER:
                continue
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

        name_error = _general_name_error(serializer.validated_data.get("name"))
        if name_error is not None:
            return Response(name_error, status=status.HTTP_400_BAD_REQUEST)

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
        collection = get_object_or_404(
            PageCollection.objects.filter(workspace__slug=slug).select_related("workspace"), pk=pk
        )

        old_name = collection.name

        # `partial=True`：PATCH 的语义是「只改传了的字段」。序列化器的 `name` 是
        # required，不加 `partial` 的话一个只有 `{"name": ...}` 的载荷确实能过，
        # 但日后加字段时第一个只传单字段的调用方就会撞 400 —— 这里明确表态。
        serializer = PageCollectionSerializer(collection, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        name_error = _general_name_error(serializer.validated_data.get("name"))
        if name_error is not None:
            return Response(name_error, status=status.HTTP_400_BAD_REQUEST)

        collection = serializer.save()

        # 集合的名字是三份拷贝（`PageCollection.name`、vault 里的目录名、导入时记进
        # `external_id` 的路径前缀），改名必须让三份一起动 —— 理由见
        # `_rename_collection_mirror` 的 docstring。
        if collection.name != old_name:
            _rename_collection_mirror(collection, old_name)

        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def destroy(self, request, slug, pk):
        """删除集合（Round H，设计 §4.1 / §4.6）。

        **页面与文件夹一个都不删** —— 它们整体上浮到「常规」，结构原封不动。
        权限与同 ViewSet 的 `create` / `partial_update` 逐字一致（不允许 GUEST）。

        **不接受任何 query 参数**：设计 §1.1 那条 `?transfer_to=` / `?delete_pages=`
        二选一是 Phase 2 的事。**传了就 400 而不是静默忽略** —— 静默忽略会让一个
        照旧文档写客户端的调用方以为「连删页面」生效了。判据是**出现**而不是取值：
        `?delete_pages=false` 同样 400，因为它说明调用方以为那份契约存在。
        """
        collection = get_object_or_404(
            PageCollection.objects.filter(workspace__slug=slug).select_related("workspace"), pk=pk
        )

        if request.query_params.get("transfer_to") is not None or request.query_params.get("delete_pages") is not None:
            return Response(
                {
                    "error": (
                        "transfer_to and delete_pages are not supported: deleting a collection "
                        "moves its pages and folders to the General partition."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        _destroy_collection(collection)

        return Response(status=status.HTTP_204_NO_CONTENT)


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

        # `folder=<uuid>`：某个文件夹的**整棵子树** —— 设计 §5.3 的「文件夹列表视图」。
        #
        # 独立参数、**不复用** `collection`（与 `scope` 同一条理由）：`collection` 的值域是
        # 「预置键或集合 uuid」，塞一个文件夹 uuid 进去会落进自建集合那条分支、按
        # `collection_id` 过滤，**静默**返回空列表 —— 比报错难查得多。
        #
        # 放在这里而不是更靠前：上面那条 `pages` queryset 已经带好了
        # `select_related` + `defer` 那组优化，本分支要**照抄同样的优化**（见下），
        # 而下面那条 O(全部页面) 的 `resolved` 列表推导对本分支是纯浪费 —— 这一分支
        # 直接 return，那一遍就不跑了。
        folder_id = request.GET.get("folder")
        if folder_id is not None:
            # 先解析成 UUID 再进查询：`filter(id="abc")` 会抛 `ValidationError`
            # （Django 的 UUIDField 在**过滤时**也校验），一路冒到 DRF 就是 500。
            # `?collection=abc` 今天也是这个下场，但那条路径前端只会喂预置键或真 uuid；
            # 这条是**新的、可以直接手改 URL 的**入口，所以自己把畸形输入兜住。
            try:
                folder_uuid = uuid.UUID(folder_id)
            except ValueError:
                return Response([], status=status.HTTP_200_OK)

            # 文件夹必须是本工作区、可见、已收录，**且真的是文件夹** —— 与 `parent` 那三条
            # （`create_page` 的注释）同一条纪律：调用者指名的 id 一律过
            # `_wiki_page_queryset`。找不到就返回空列表、不落 404 —— 口径与
            # `?collection=<不存在的 uuid>`（今天也是静默空列表）保持一致。
            folder = (
                _wiki_page_queryset(request, slug)
                .filter(id=folder_uuid, node_type=Page.NODE_TYPE_FOLDER)
                .first()
            )
            if folder is None:
                return Response([], status=status.HTTP_200_OK)

            # **自己不算**（裁定 4）：设计 §5.3/§5.4 两处都写「自己那一层不算」，
            # §5.2 的括注写成「它自己 + 全部后代的 id 集」是那处括注自己错了。
            # 语义上也必须这样：文件夹在自己列表里占一行、点进去回到同一处，荒谬。
            #
            # `_descendant_ids` 只按 workspace 收窄（BFS + 去重 + 深度上限 20），
            # **不带可见性与收录过滤** —— 那两条由下面那次 `_wiki_page_queryset` 补上。
            # 两个条件各只写一处，不重复实现；别人的私有子页因此也不会漏出来。
            #
            # **有意不过滤归档后代**（与 `?collection=` 的分区路由不同源）：子树是
            # **导航性下钻**，语义是「这个节点下的全部内容」，归档行到这里混列是刻意的 ——
            # 静默省略反而会让用户以为后代丢了（分区路由才把归档行归到「已归档」）。别当 bug 修。
            descendant_ids = _descendant_ids(root=folder)
            if not descendant_ids:
                return Response([], status=status.HTTP_200_OK)

            subtree = (
                _wiki_page_queryset(request, slug)
                .filter(id__in=descendant_ids)
                # 与上面那条 `pages` 同一组优化，理由逐条相同（`select_related` 免 N+1、
                # `defer` 把三个重列移出 SELECT）。
                .select_related("workspace")
                .select_related("owned_by")
                .defer("description_json", "description_binary", "description_html", "description_stripped")
            )
            # 用 `WikiPageSerializer`，**不带** `node_type`（裁定 6）：前端从 store 的
            # `pageNodeTypes` 拿类型，那份由侧栏的 `scope=all` 取数灌满，
            # 覆盖任何子树的**超集**。响应顺序沿用模型默认排序（`-created_at`）。
            return Response(WikiPageSerializer(subtree, many=True).data, status=status.HTTP_200_OK)

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
        """换集合（collection_id）、换位置（parent）、改标题（name）与/或写正文（description_*），可任意组合。"""
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

        # 目标位置（parent）。两条判定，都排在 save() 之前 —— 404 / 400 路径下
        # **一个字段都不会动**。
        #
        # 可达性直接复用 `_wiki_page_queryset`（工作区 + 可见 + 已收录），
        # 与 create_page 收 parent 时**逐字同一条**。只在这里加 `.filter(id=...)`
        # 收窄到指名的那一行 —— 不把条件抄第二遍：抄一遍就多一处会与它走样的副本。
        parent_id = serializer.validated_data.get("parent")
        if parent_id is not None:
            target_parent = _wiki_page_queryset(request, slug).filter(id=parent_id).first()
            if target_parent is None:
                return Response({"error": "Parent page not found."}, status=status.HTTP_404_NOT_FOUND)

            # 环。`_descendant_ids` 自带 `seen` 集与深度封顶 —— 写操作撞上环会写成
            # 死循环，那正是它存在的理由（见其 docstring）。直接复用，不自己写遍历。
            if target_parent.id == page.id or target_parent.id in set(_descendant_ids(root=page)):
                return Response(
                    {"error": "Cannot move a page into itself or its own descendant."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # **位置决定集合**（E-10）：给了非空 parent，目标那一行的集合说了算，
            # 同一请求里的 `collection_id` 被它覆盖。与建页时的继承同一条规则
            # （子页跟随父页的集合，`create_page` 的 :484-488）。
            # 推导出来的值不必再验归属：`target_parent` 取自 `_wiki_page_queryset`
            # （工作区作用域），它的 `collection_id` 天然是本工作区的集合或 None。
            serializer.validated_data["collection_id"] = target_parent.collection_id

        # 旧名与旧集合都必须在 save() **之前**记下来：serializer.update() 就地改
        # instance，save() 之后 page 上已经是新值，就再也算不出旧的了。
        old_name = page.name
        old_collection_id = page.collection_id
        old_parent_id = page.parent_id

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
        # 三个条件里任何一个变了，这一页的镜像路径就变了（镜像路径 = 集合名 / 祖先链 / 文件名）。
        if page.name != old_name or page.collection_id != old_collection_id or page.parent_id != old_parent_id:
            # 只有 parent 真的变了才需要旧祖先链。`old_parent_id` 是 save() 之前抓的
            # 裸 UUID，所以这里再算它的祖先链是正确的 —— 但 `_page_ancestors` 是逐层
            # 查库，而改名是协同编辑器**每次防抖都会发**的常规路径，没必要为它白走一趟。
            old_ancestors = _page_ancestors(old_parent_id) if page.parent_id != old_parent_id else None
            _move_wiki_page_mirror(page, old_name, old_collection_id, old_ancestors=old_ancestors)

        return Response(WikiPageDetailSerializer(page).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def destroy(self, request, slug, page_id):
        # 作用域与 create 同一条裁定：按**工作区**收窄，不按「我参与的项目」。
        # 与 create 一样，操作对象是调用者已经知道 id 的页面，收窄在这里只是让
        # 工作区级动作变得不可预期；需要按项目收窄的是候选**发现**面，不是这里。
        page = _wiki_page_queryset(request, slug).filter(id=page_id).first()
        if page is None:
            return Response({"error": "Page not found in this wiki."}, status=status.HTTP_404_NOT_FOUND)

        if page.node_type == Page.NODE_TYPE_FOLDER:
            return self._destroy_folder(page)

        # 移出 Wiki 只是取消收录，绝不删除页面本身 —— 页面承载版本历史与评论。
        # 同 create：QuerySet.update() 绕过 auto_now，updated_at 要显式传。
        Page.objects.filter(id=page.id).update(is_global=False, collection=None, updated_at=timezone.now())
        return Response(status=status.HTTP_204_NO_CONTENT)

    @staticmethod
    def _destroy_folder(folder):
        """删一个文件夹（罗盘 Round E，设计 §4.3）。语义照 Confluence Cloud：
        **内容不删，整体上浮一级**。

        与页面那条路径的差别只有一条，但它决定了这个功能对不对：页面被「移出 Wiki」时
        **不动它的子节点**（子页变成孤儿、按根渲染 —— 那是既有行为，本轮不碰）；
        而文件夹必须把**直接子节点重挂到自己的父级**上，否则用户看到的是「文件夹没了，
        里面的东西也跟着没了」—— 与 Confluence 的承诺正好相反。

        **为什么只重挂直接子节点就够**：后代的路径就是祖先链。被删的是 F，F 的子节点 C
        挂到 F 的父级；C 以下的整棵子树里每一个节点的父**都没变**，所以祖先链里唯一变的
        那一段（F 被摘掉）是通过 C 传导的。重挂 C 一处，全子树自动正确。

        文件夹自己**不删行**：`is_global=False`（出 Wiki），与页面那条路径同一条不变量。
        文件夹没有正文因此也没有自己的镜像（`create_page` 对文件夹跳过镜像），所以
        子树里第一个要搬的就是它的子节点。

        **顺序是载荷**：① 拍快照 → ② 重挂 → ③ 自己出 Wiki → ④ 搬镜像。
        ④ 必须在 ② 之后 —— 搬移要读 `parent_id` 算新路径。
        """
        # ① 快照必须在重挂之前（重挂之后这些行就不再挂在 folder 下面了）。
        child_ids, state = _snapshot_children_mirror_state(folder)

        # ② 直接子节点上浮到被删文件夹的原父级。
        # `collection` 不动（E-4）：被删文件夹与它的兄弟同属一个集合，上浮仍在同一集合里。
        Page.objects.filter(workspace_id=folder.workspace_id, parent_id=folder.id).update(
            parent_id=folder.parent_id, updated_at=timezone.now()
        )

        # ③ 自己出 Wiki —— 只取消收录，绝不删行（与页面那条同一条不变量）。
        Page.objects.filter(id=folder.id).update(is_global=False, collection=None, updated_at=timezone.now())

        # ④ 子节点的镜像。它们不会自己动：重挂是纯 SQL，磁盘上没有任何目录搬移跟着发生
        #    （`_move_page_file` 的目录逻辑搬的是「这一页**同名**的目录」，而这里被摘掉的
        #    那个目录属于**被删的文件夹**，没有任何一页的同名目录对应它）。
        _move_children_mirrors(child_ids, state)

        return Response(status=status.HTTP_204_NO_CONTENT)


def _destroy_collection(collection):
    """删一个集合：**页面与文件夹一个都不删**，整体上浮到「常规」。

    与 `_destroy_folder` 的关系：那个搬的是「一棵子树里被摘掉的那一段」，这个搬的是
    「整片森林从一个目录挪到另一个目录」。相同点是**都只对顶层节点下手** ——
    后代由承载它的目录一次带走（`_move_page_file` 的同名目录分支，见
    `_snapshot_children_mirror_state` 的 docstring）。

    **顺序是载荷**（① 快照 → ② 整棵子树出集合 → ③ 搬镜像 → ④ 软删集合行 → ⑤ rmdir）：

    ② 必须在 ④ 之前，且**不能**依赖 `SoftDeleteModel` 的 Celery 级联。那个任务
    （`bgtasks/deletion_task.py`）对 SET_NULL 关系做的正是我们要的浮升，但它是
    `.delay()`，需要 worker 活着 —— worker 不在时集合行已经软删、`list` 已经过滤掉它，
    而页面的 `collection_id` **还指着它** ⇒ `resolve_collection_key` 返回那个 uuid ⇒
    侧栏没有这一行 ⇒ **页面在 UI 里凭空消失**，与「一个都不删」的承诺正好相反。

    ② 对整棵子树**一次**生效：后代行本来就都带 `collection_id`
    （`_move_descendants_to_collection` 就是靠这一点工作的），所以一条
    `WHERE collection_id = A` 覆盖页面、文件夹、嵌套页面全部。`Page.parent` 一个都不动
    ⇒ 结构自动正确。
    """
    # ① 顶层节点快照 —— **必须**在 ② 之前（② 之后这些行就不再挂在集合下面了）。
    #
    #    判据刻意用 Python 而不是 SQL 的 `~Q(parent__collection_id=collection.id)`：
    #    三值逻辑下，父节点**不属于任何集合**（`collection_id IS NULL`）时
    #    `parent.collection_id = A` 求值为 NULL、`NOT NULL` 仍是 NULL ⇒ 那一行被判成
    #    非顶层，而它的镜像其实在 `3-Wiki/<A>/<父页名>/…` 下 —— `_page_ancestors`
    #    **不看集合边界**，只沿 parent 链往上走。
    #
    #    键统一成 `str(uuid)`：③ 要按 id 查回名字，而 `Page.id` 是 `UUID` 对象、
    #    `values()` 吐出来的也是 `UUID` —— 字符串键同时服务 `id__in` 过滤与查表。
    rows = list(
        Page.objects.filter(workspace_id=collection.workspace_id, collection_id=collection.id).values(
            "id", "name", "parent_id"
        )
    )
    old_names = {str(row["id"]): row["name"] for row in rows}
    in_collection = {str(row["id"]) for row in rows}
    top_ids = [
        str(row["id"]) for row in rows if row["parent_id"] is None or str(row["parent_id"]) not in in_collection
    ]

    # ② 整棵子树出集合。
    # `QuerySet.update()` 绕过 auto_now ⇒ `updated_at` 必须显式刷新（与 `create` /
    # `destroy` / `_move_descendants_to_collection` 三条既有路径同一条纪律）；
    # `updated_by` **不写** —— 刷新时间戳，不把执行者盖到「最后编辑人」上。
    Page.objects.filter(collection_id=collection.id).update(collection=None, updated_at=timezone.now())

    # ③ 只搬**顶层**节点的镜像，逐一 best-effort。
    #    **重新取行**，不重用 ① 的 values 结果：搬移要读 `page.collection_id` 算**新**路径，
    #    而那个字段刚被 ② 改成 None，内存里必须也是 None。
    #    `_move_wiki_page_mirror(node, old_name, old_collection_id)` —— 第四个参数省略即
    #    `old_ancestors=None`，语义是「祖先链没变」，正是这里的情形（② 不动 `parent`）。
    #    文件夹节点没有 `.md`（`old_path.exists()` 为假、文件分支空转），真正干活的是
    #    `_move_page_file` 后面那段**同名目录搬移**：`old_dir.replace(new_dir)` 把整棵
    #    子树一次带走，所以后代不需要单独搬。
    for node in Page.objects.filter(id__in=top_ids):
        try:
            _move_wiki_page_mirror(node, old_names[str(node.id)], collection.id)
        except (OSError, UnicodeDecodeError) as exc:
            # 兜底：`move_mirror_file` 自己已经吞 OSError 了，这里是第二层保险 ——
            # 「永远不能让一次删除因为磁盘问题失败」是设计 §8 写死的失败方向。
            logger.warning("Failed to move the mirror of page %s: %s", node.id, exc)

    # ④ 软删集合行。此时 Celery 那条 SET_NULL 级联已经无事可做（② 清空了）。
    collection.delete()

    # ⑤ 目录空了就删，非空原样留下 —— `os.rmdir` 非空即抛（`ENOTEMPTY`），一个字都不碰。
    #    目录名用**写侧同一套拼法**（`wiki_collection_directory` 会过 `_sanitize_name`），
    #    否则集合名里有连续空格这类差别时会找一个不存在的目录、静默 no-op
    #    （`test_wiki_collection_rename_app.py` 的 F2 那条锁是同一个坑）。
    directory = wiki_collection_directory(
        collection_name=collection.name,
        collection_id=str(collection.id),
        root=get_wiki_markdown_root(collection.workspace),
    )
    try:
        os.rmdir(directory)
    except OSError as exc:
        # 三种正常结局：目录本来就不存在、非空（还有孤儿文件）、只读盘。
        # 这是「wiki 永不主动删 vault 内容」（设计 §243）唯一的一次例外，且**只删空目录**。
        logger.info("Not removing the collection directory %s: %s", directory, exc)


def _wiki_page_project_id(page):
    """Resolve the project a wiki page mirrors into, or ``None`` when it has none.

    ``_write_page_mirror`` is project-scoped by construction: it resolves a
    directory name through ``Project.objects.filter(pk=project_id)``, and
    ``MARKDOWN_STORAGE_PATH`` points at the projects folder itself
    (``…/ObsidianVault/2-项目``). A page with no live ``ProjectPage`` link has
    no project directory of its own, and guessing one would put a file somewhere
    the user did not ask for — so this resolver answers ``None`` and leaves the
    routing to its caller.

    Such pages are reachable: ``WikiPageViewSet.create`` only excludes private
    and archived pages from inclusion, so a public page belonging to no project
    can be included in the wiki. Until Round H the ruling (design §2.3c,
    2026-09-27) was to skip and log; Round H added the missing third home —
    ``3-Wiki/常规/`` — so this resolver's ``None`` now means "write into the
    General folder", not "write nowhere". The write and the move both honour it,
    which is why they share this resolver instead of each deciding for itself.

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
    old_dir = wiki_collection_directory(
        collection_name=old_name, collection_id=collection.id, root=get_wiki_markdown_root(collection.workspace)
    )
    new_dir = wiki_collection_directory(
        collection_name=collection.name, collection_id=collection.id, root=get_wiki_markdown_root(collection.workspace)
    )
    vault_root = get_wiki_markdown_root(collection.workspace).parent
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
    return get_wiki_markdown_root(page.workspace).parent / page.external_id


def _description_to_markdown(description_html):
    """把正文 HTML 转成 Markdown —— 集合档与「常规」档共用这一份。

    两份拷贝的代价不是行数，是**解析器配置漂移**：`resolve_user` / `resolve_asset_url`
    决定正文里的 @提及与附件链接长什么样，两处一旦分叉，同一篇正文在不同分区
    镜像出的 Markdown 会不一样。
    """
    user_cache, asset_cache = {}, {}
    return html_to_markdown(
        description_html,
        resolve_user=lambda uid: _resolve_user_display_name(uid, user_cache),
        resolve_asset_url=lambda aid: _resolve_asset_url(aid, asset_cache),
    )


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
    write_wiki_page_markdown(
        collection_name=collection.name,
        collection_id=str(collection.id),
        ancestors=_page_ancestors(page.parent_id),
        page_id=str(page.id),
        name=page.name,
        markdown=_description_to_markdown(description_html),
        root=get_wiki_markdown_root(collection.workspace),
        own_path=_wiki_page_own_path(page),
    )


def _mirror_wiki_page(page, description_html):
    """Mirror a wiki page's body to a local ``.md``, or skip when it has no home.

    Routing is three-way and **collection-first** (design §3.3): a page in a
    collection mirrors under the wiki vault root, keyed by the collection name —
    the collection is the axis the user sees in the Wiki, so that is where the
    file belongs. A page with no collection but a live ``ProjectPage`` link
    keeps the pre-existing project behaviour verbatim.

    A page with neither lands in ``3-Wiki/常规/`` (Round H) — that folder is the
    one place a page with no collection and no project can be written, and it is
    where the pages of a deleted collection land.
    """
    if page.collection_id is not None:
        collection = PageCollection.objects.filter(id=page.collection_id).select_related("workspace").first()
        if collection is not None:
            _write_collection_page_mirror(page, collection, description_html)
            return

    project_id = _wiki_page_project_id(page)

    if project_id is None:
        # 第三档（Round H，设计 §4.2）：既没有集合、又没有活着的项目链接 ⇒ 落「常规」。
        # 此前这一档是「不写、只记 warning」；226 实测 35 个 wiki 页面**全部**没有项目
        # 链接，所以「删集合」若不补这一档，等于把这 35 个页面永久踢出镜像同步。
        write_wiki_general_page_markdown(
            ancestors=_page_ancestors(page.parent_id),
            page_id=str(page.id),
            name=page.name,
            markdown=_description_to_markdown(description_html),
            root=get_wiki_markdown_root(page.workspace),
            own_path=_wiki_page_own_path(page),
        )
        return

    _write_page_mirror(
        project_id,
        page.id,
        page.name,
        _page_ancestors(page.parent_id),
        description_html,
    )


def _wiki_mirror_target(page, *, collection_id, name, ancestors):
    """这一页的镜像落在**哪条路径、哪棵树** —— 三档路由的**唯一一份**判定。

    返回 ``(path, root)``：``root`` 是这条路径所属那棵树的根部（wiki 树给自己那份，
    项目树给 ``get_markdown_root``）。删除要用它当「自底向上收目录」的**止步点**
    （``prune_empty_directories`` 的 ``stop_at``）—— 少了它，`rmdir` 会顺着父目录
    一路爬出 vault。

    路由与 ``_mirror_wiki_page`` 逐字同源（集合 → 项目 → 「常规」），``own_path``
    的传法也逐字照抄它：集合档与常规档传 ``_wiki_page_own_path(page)``、项目档不传
    （``_write_page_mirror`` 自己那侧也不传）。

    ⚠️ **改这里必须同时看 ``_mirror_wiki_page``**：写侧那份是两个独立的 if/return，
    本函数是它的取路径版本。两者的分档条件（集合行在不在、项目链接在不在）必须一直
    一样，否则「写在哪、删在哪」会分叉 —— 那正是 Round F 那次改名搬错目录的同款事故。
    """
    if collection_id is not None:
        collection = PageCollection.objects.filter(id=collection_id).select_related("workspace").first()
        if collection is not None:
            root = get_wiki_markdown_root(collection.workspace)
            return (
                wiki_page_markdown_path(
                    collection_name=collection.name,
                    collection_id=str(collection.id),
                    ancestors=ancestors,
                    name=name,
                    page_id=str(page.id),
                    root=root,
                    own_path=_wiki_page_own_path(page),
                ),
                root,
            )
        # collection_id 有值但行没了 —— 与 _mirror_wiki_page 一样往下落到项目档。

    project_id = _wiki_page_project_id(page)
    if project_id is None:
        # 第三档（Round H）：无集合、无项目 ⇒ 「常规」。返回 None 会让**搬移**把这类
        # 页面判成「没有家」而原地不动（`_move_wiki_page_mirror` 的 `new_path is None`
        # 分支），删集合时它们会永远留在 `3-Wiki/<集合>/` 下面。
        root = get_wiki_markdown_root(page.workspace)
        return (
            wiki_general_page_markdown_path(
                ancestors=ancestors,
                name=name,
                page_id=str(page.id),
                root=root,
                own_path=_wiki_page_own_path(page),
            ),
            root,
        )
    root = get_markdown_root(page.workspace)
    return (
        page_markdown_path(
            project_name=_project_name(project_id),
            project_id=str(project_id),
            ancestors=ancestors,
            name=name,
            page_id=str(page.id),
            root=root,
        ),
        root,
    )


def _wiki_mirror_path(page, *, collection_id, name, ancestors):
    """Resolve where ``page``'s mirror lives when keyed by ``collection_id``/``name``.

    Factored out of ``_mirror_wiki_page``/``_move_wiki_page_mirror`` because the
    *move* needs this for two different states at once: the old path must be
    computed from the page's **pre-save** collection and name. Reading them off
    the saved instance is what made a collection change silently move nothing.

    Routing is the same three-way, collection-first rule as ``_mirror_wiki_page``:
    collection root, else project root, else the 「常规」 folder (Round H).

    Round I made this a one-line delegate to ``_wiki_mirror_target``, which is
    now the single definition of that routing — the delete path needs the same
    answer *plus* the tree root it belongs to, so it cannot be duplicated.
    """
    return _wiki_mirror_target(page, collection_id=collection_id, name=name, ancestors=ancestors)[0]


def _move_wiki_page_mirror(page, old_name, old_collection_id, old_ancestors=None):
    """Move a wiki page's mirror after a rename, a collection change, a re-parent, or any mix.

    Both states are computed explicitly: the old path from ``old_name`` +
    ``old_collection_id`` + ``old_ancestors`` (the values before the write), the new
    path from the saved instance. A move that crosses roots (collection → project,
    or the reverse) is just two paths in different trees — nothing here cares which.

    ``old_ancestors`` defaults to ``None``, which asserts **「the ancestor chain did
    not change」** and resolves to the current one — that is exactly the pre-Round-E
    behaviour, so the two callers that only rename or only change the collection keep
    working untouched. A caller that changes ``parent`` **must** pass the chain it
    captured before the write: ``save()`` overwrites ``parent_id``, so by the time
    this runs the old chain is unrecoverable. ``None`` therefore means "unchanged",
    never "unknown" — passing it while the parent *did* move computes the old path at
    the new location, and the move silently finds nothing.

    Both paths always resolve: ``_wiki_mirror_path``'s three-way routing
    (collection → project → the 「常规」 folder) never comes back ``None``, so the
    two ``None`` guards below are unreachable defensive branches. They are kept
    only so a future change to that routing fails softly instead of half-moving a
    file: were one to fire, the old file would be **left where it is** and a
    warning logged, because the wiki never deletes vault folders (删集合不删文件夹).

    The recorded path (``Page.external_id``) follows the file, so the next body write
    still recognises it as this page's own — see ``_repoint_page_external_id`` for the
    guards.

    A descendant whose file moves merely because its *parent's* folder moved keeps a
    stale ``external_id`` — that is **still** not covered here, and deliberately so:
    the descendant's file rides along inside the moved folder (``_move_page_file``'s
    same-named-directory branch), so there is nothing for this function to move, and
    re-pointing without a signal that the carrier actually moved risks claiming a
    file that is not ours. Closing that gap is its own round — see
    `罗盘-Wiki文件夹删除与移动-设计.md` §2.2 / §7 (E-16).
    """
    new_ancestors = _page_ancestors(page.parent_id)
    if old_ancestors is None:
        # 祖先链**没变**（只改名 / 只换集合）：新旧共用一条链，与加这个参数之前逐字相同。
        old_ancestors = new_ancestors
    old_path = _wiki_mirror_path(page, collection_id=old_collection_id, name=old_name, ancestors=old_ancestors)
    new_path = _wiki_mirror_path(page, collection_id=page.collection_id, name=page.name, ancestors=new_ancestors)

    if old_path is None:
        # Unreachable as of Round H: ``_wiki_mirror_path`` always resolves (collection
        # → project → 「常规」), so neither state can come back ``None``. Kept as a
        # defensive guard — if it ever fires there is simply nothing on disk to move.
        if new_path is None:
            logger.warning(
                "Skipping markdown mirror move for wiki page %s: neither the old nor the new "
                "state resolved to a mirror root (unreachable — the three-way routing always "
                "picks one).",
                page.id,
            )
        return

    if new_path is None:
        # Unreachable for the same reason, so the old file can no longer be orphaned
        # here. Kept defensive; the file would be left in place, never deleted.
        logger.warning(
            "Skipping markdown mirror move for wiki page %s: the new state resolved to no mirror "
            "root (unreachable — the three-way routing always picks one). The old file is left in "
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

    vault_root = get_wiki_markdown_root(page.workspace).parent
    try:
        old_rel = old_path.relative_to(vault_root).as_posix()
        new_rel = new_path.relative_to(vault_root).as_posix()
    except ValueError:
        return
    if page.external_id != old_rel:
        return
    try:
        new_path.relative_to(get_wiki_markdown_root(page.workspace))
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
    B 的父是 A。Round E 起 wiki 的路由**可以**改 ``parent``（``WikiPageUpdateSerializer``
    有这个字段），但环仍能从数据层造出来（绕过 API 直接改库）—— 级联是**写**操作，
    撞上环会写成死循环。

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


def _cascade_delete_pages(nodes):
    """把一棵子树**整棵软删**，并把我们写过的镜像收掉（罗盘 Round I，设计 §4.1/§4.2）。

    ``nodes`` 是**已经读出来的** ``Page`` 实例（页面与文件夹都要，文件夹没有镜像、
    在下面第 ① 步被跳过）。文件夹那条路径用 ``_descendant_ids`` 取，集合那条用
    ``collection_id`` 取 —— 两个调用方共用**这一份**实现，没有第二份级联。

    **顺序是载荷**（设计 §4.4）：

      ① 读行 + 算镜像路径   ← 必须在 ② 之前
      ② 软删整棵子树
      ③ 删镜像（best-effort）
      ④ 自底向上收空目录（best-effort）

    ① 为什么必须在 ② 之前：``Page.objects`` 是 ``SoftDeletionManager``（滤
    ``deleted_at__isnull=True``）。``deleted_at`` 一落值，这些行就**再也查不出来**、
    路径也就再也算不出来 —— 磁盘上会留下一整棵再也没人认领的镜像。

    ③④ 为什么绝不影响 ② 的成败：它们全部 best-effort。允许的结局是
    「**页面已删、文件残留**」（人工可清），**不是**「文件先没了、页面还在」
    （设计 §7 写死的失败方向）。所以 ③ 里每一行都单独 try，一行炸不影响其余行。

    某一行读不到 / 算路径抛错（并发、已软删、非 UTF-8 文件）⇒ **跳过它**，
    其余照删 —— 不允许一行把整次删除拖失败（设计 §7 第四行）。
    """
    rows = list(nodes)
    if not rows:
        return

    # ① 读行 + 算路径（软删之前！）。
    #    文件夹行没有正文、也就没有自己的 .md —— 直接跳过，它的**目录**由 ④ 里
    #    从它后代的文件往上爬的那条链收掉。
    targets = []
    for row in rows:
        if row.node_type != Page.NODE_TYPE_DOC:
            continue
        try:
            path, root = _wiki_mirror_target(
                row,
                collection_id=row.collection_id,
                name=row.name,
                ancestors=_page_ancestors(row.parent_id),
            )
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Skipping the mirror of page %s: %s", row.id, exc)
            continue
        targets.append((row.id, path, root))

    # ② 软删整棵子树，**一条 SQL**。
    #    `QuerySet.update()` 绕过 auto_now ⇒ `updated_at` 显式刷新（与 create /
    #    destroy / `_move_descendants_to_collection` 三条既有路径同一条纪律）；
    #    `updated_by` **不写** —— 刷新时间戳，不把执行者盖到「最后编辑人」上。
    #
    #    不经 `SoftDeleteModel.delete()`：那个会 `.delay()` 一个 Celery 任务
    #    （`bgtasks/deletion_task.py`），worker 不在时留下半个状态，而这里一条 SQL
    #    就已经覆盖整棵子树，不需要它（与 `_destroy_collection` 的既有论证同源）。
    now = timezone.now()
    Page.objects.filter(id__in=[row.id for row in rows]).update(deleted_at=now, updated_at=now)

    # ③ 删镜像 —— 只删我们自己写的（`delete_page_file` 逐字守住 §4.3 规则一）。
    #    按 root 分组，给 ④ 当止步点：页面可能落在 wiki 树**或**项目树里。
    by_root = {}
    for page_id, path, root in targets:
        if delete_page_file(path, str(page_id)):
            by_root.setdefault(root, []).append(path)

    # ④ 收空掉的那几段目录，非空即停（§4.3 规则二/三，`prune_empty_directories` 里）。
    for root, paths in by_root.items():
        prune_empty_directories(paths, stop_at=root)


def _snapshot_children_mirror_state(folder):
    """``folder`` 的**直接子节点** id 序列 + 每个节点**改之前**的 ``(name, collection_id, ancestors)``。

    只要**直接子节点**，不要整棵子树 —— 与「只重挂直接子节点」是**同一条道理**：一条链上
    唯一变的那一段通过直接子节点传导。多拍一层不仅白花，还要为每个节点各走一趟
    ``_page_ancestors``。

    ⚠️ **子节点里有文件夹，而且必须留着 —— 它们是嵌套子树唯一的搬运工。**
    对**页面**子节点，``_move_page_file`` 搬的是它的 ``.md``。对**文件夹**子节点，
    它连 ``.md`` 都不存在（文件夹没有正文，也就没有镜像）：``old_path.exists()`` 为假，
    那个分支什么也不做；真正干活的是**后面那段同名目录搬移** ——
    ``old_dir = <旧父>/<文件夹名>``、``new_dir = <新父>/<文件夹名>``，
    然后 ``old_dir.replace(new_dir)``（``markdown_storage.py:289-297``）——
    整个目录连同里面所有后代**一次搬完**。所以「删 B，B 的 C2 里还有 t2」这种情况，
    搬 C2 那一次目录替换就把 t2 带走了，**不需要**为 t2 单独做任何事。

    （顺带：``_move_wiki_page_mirror`` 对文件夹走完会落到
    ``_repoint_page_external_id``，但那里第一行就是
    ``if not page.external_id or page.external_source != EXTERNAL_SOURCE: return``
    —— 应用内建的文件夹 ``external_id`` 是 ``None``，直接返回，不会写坏任何东西。）

    **必须在重挂之前取**：重挂之后这些行就不再挂在 ``folder`` 下面了。
    """
    children = list(
        Page.objects.filter(workspace_id=folder.workspace_id, parent_id=folder.id).values(
            "id", "name", "parent_id", "collection_id"
        )
    )
    # 每行都是 folder 的直接子节点（上面的 filter 就是 parent_id=folder.id），
    # 祖先链**同一个值**，只算一次。
    ancestors = _page_ancestors(folder.id)
    state = {str(row["id"]): (row["name"], row["collection_id"], ancestors) for row in children}
    return [str(row["id"]) for row in children], state


def _move_children_mirrors(ordered_ids, state):
    """搬 ``ordered_ids`` 里每个节点的镜像，逐个跟随 ``external_id``。

    必须在**重挂之后**调用：搬移要读 ``page.parent_id`` 算**新**路径，重挂之前它还是旧值。

    ``state`` 里查不到的节点按「什么都没变」处理（old == new ⇒ ``_move_wiki_page_mirror``
    内部直接返回），所以多传几个 id 是安全的，不会误搬。
    """
    for node in Page.objects.filter(id__in=ordered_ids):
        old_name, old_collection_id, old_ancestors = state.get(str(node.id), (node.name, node.collection_id, None))
        _move_wiki_page_mirror(node, old_name, old_collection_id, old_ancestors=old_ancestors)


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
            # 侧栏的文件夹行靠改名，`move-to` 靠换集合。一刀切成 400
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

        # `parent` 是**结构性**变更，只走 metadata 路由（设计 §4.7）。这个端点复用
        # 同一个序列化器，不加这条守卫就会**连带接受** `parent` 并在下面直接 save() ——
        # 绕过 404 / 环检测 / 集合推导**全部**校验，还能把页面挂到别的工作区的页下面。
        # 移动只有一个门。
        if "parent" in request.data:
            return Response(
                {"error": "Use the wiki-pages endpoint to move a page."},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
