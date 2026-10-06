# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""子树软删 + 镜像收尾 —— wiki 与项目**共用**的一份级联（罗盘 Round J）。

**为什么单独一个模块**：不是「`collection.py` 太长」，是**共用**。
项目侧的 `PageViewSet.destroy` 要调用这一份实现，而 `collection.py` 在模块层
``from .base import _page_ancestors`` —— 两边互相模块级导入会成环。放进一个谁都不依赖的
模块，两个调用方各取所需。

**为什么不各写一份**：级联的四步顺序是**一条不变量**（见 `_cascade_delete_pages`
的 docstring）。复制一份到项目侧，两条级联迟早漂 —— 那正是 Round I 刻意避免的事。
两棵树**不同**的只有一件事：「这一行的镜像在哪儿」（wiki 走集合/常规树、项目走项目目录）。
所以那一件事被抽成**参数**（`resolve_mirror`），其余三步原样共用。
"""

import logging

from django.utils import timezone

from plane.db.models import Page
from plane.utils.markdown_storage import delete_page_file, prune_empty_directories

logger = logging.getLogger(__name__)


#: 下行遍历的深度上限。
#:
#: `parent` 是普通外键，**没有任何约束**禁止 A 的父是 B、B 的父是 A。级联是**写**操作，
#: 撞上环会写成死循环。20 与 `_page_ancestors`（`views/page/base.py`）往上走时用的自保
#: 上限一致。
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


def _cascade_delete_pages(nodes, *, resolve_mirror):
    """把一棵子树**整棵软删**，并把我们写过的镜像收掉（罗盘 Round I，设计 §4.1/§4.2）。

    ``nodes`` 是**已经读出来的** ``Page`` 实例（页面与文件夹都要，文件夹没有镜像、
    在下面第 ① 步被跳过）。文件夹那条路径用 ``_descendant_ids`` 取，集合那条用
    ``collection_id`` 取，项目那条第 ① 步换成项目的寻址函数 —— 三个调用方共用
    **这一份**实现，没有第二份级联。

    ``resolve_mirror`` 是**寻址函数**：给定一行 ``Page``，返回 ``(path, root)``。
    ``root`` 是这条路径所属那棵树的根部（删除要用它当「自底向上收目录」的**止步点**，
    见 ``prune_empty_directories`` 的 ``stop_at``）—— 少了它，``rmdir`` 会顺着父目录
    一路爬出 vault。

    调用方必须各传各的：wiki 传 ``_wiki_mirror_target_for_row``（行为逐字不变），
    项目传 ``_project_mirror_target_resolver(project_id)`` 的返回值。

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
            path, root = resolve_mirror(row)
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
    #    `is_global=False` 一并落下：被删的行不该再留在任何按「收录」过滤的读者眼里
    #    （设计 I-3 乙 —— 连双身份的页面也整棵软删）。放在**这一份** helper 里，
    #    文件夹级联与集合级联才是同一条不变量；只写在 `_destroy_folder` 里会让两条分叉。
    now = timezone.now()
    Page.objects.filter(id__in=[row.id for row in rows]).update(deleted_at=now, updated_at=now, is_global=False)

    # ③ 删镜像 —— 只删我们自己写的（`delete_page_file` 逐字守住 §4.3 规则一）。
    #    按 root 分组，给 ④ 当止步点：页面可能落在 wiki 树**或**项目树里。
    by_root = {}
    for page_id, path, root in targets:
        if delete_page_file(path, str(page_id)):
            by_root.setdefault(root, []).append(path)

    # ④ 收空掉的那几段目录，非空即停（§4.3 规则二/三，`prune_empty_directories` 里）。
    for root, paths in by_root.items():
        prune_empty_directories(paths, stop_at=root)
