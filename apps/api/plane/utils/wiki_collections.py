# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""罗盘 Wiki 的集合分区逻辑。

四个预置分区（general / private / shared / archived）**不是数据库里的行**。
private 与 archived 分别派生自 Page.access 与 Page.archived_at —— 若把它们
存成行，就产生了两个真相源：用户把页面改成私有，集合归属不会跟着变，数据
立刻自相矛盾。所以它们只能是查询。

本模块刻意 **不 import 任何 Django 东西**（连 Page 模型也不 import），
这样它可以在无数据库的环境下被完整测试。
"""

from typing import Any, Dict, Iterable, List

GENERAL = "general"
PRIVATE = "private"
SHARED = "shared"
ARCHIVED = "archived"

#: 分区键的稳定顺序 —— 侧栏按这个顺序渲染。
PREDEFINED_KEYS = (GENERAL, PRIVATE, SHARED, ARCHIVED)

#: 手抄自 Page.PRIVATE_ACCESS（apps/api/plane/db/models/page.py）。
#: 本模块不 import Django 是刻意的，代价就是这行重复；
#: plane/tests/unit/utils/test_wiki_collections.py 里有一个测试锁死两者一致。
PRIVATE_ACCESS = 1


def resolve_collection_key(*, archived_at: Any, access: int, collection_id: Any) -> str:
    """返回一个页面所属的分区键。

    优先级：archived > private > 用户集合 > general。

    ``shared`` 永远不会被返回：开源版没有"发布"字段，该分区 Phase 1 恒空。
    """
    if archived_at is not None:
        return ARCHIVED
    if access == PRIVATE_ACCESS:
        return PRIVATE
    if collection_id is not None:
        return str(collection_id)
    return GENERAL


def partition_pages(pages: Iterable[Any]) -> Dict[str, List[Any]]:
    """把页面按分区键分组。

    ``pages`` 里只需是"有 archived_at / access / collection_id 三个属性"的对象
    （真实的 Page 实例、或任何替身都行）。

    返回值一定包含全部 ``PREDEFINED_KEYS``（哪怕是空列表），用户自建的集合
    只在真的命中时才出现。输入顺序在每个键内被保留。
    """
    result: Dict[str, List[Any]] = {key: [] for key in PREDEFINED_KEYS}
    for page in pages:
        key = resolve_collection_key(
            archived_at=getattr(page, "archived_at", None),
            access=getattr(page, "access", 0),
            collection_id=getattr(page, "collection_id", None),
        )
        result.setdefault(key, []).append(page)
    return result
