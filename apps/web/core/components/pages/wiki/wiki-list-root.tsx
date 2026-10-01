/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo, useState } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
import { FileOutput, X } from "lucide-react";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { TContextMenuItem } from "@plane/ui";
// components
import { ListLayout } from "@/components/core/list";
import type { TPageActions } from "@/components/pages/dropdowns";
import { PageListBlock } from "@/components/pages/list/block";
import { MoveToCollectionModal } from "@/components/pages/wiki/move-to-collection-modal";
import { buildWikiTreeLines, wikiTreeIndentClass } from "@/components/pages/wiki/wiki-tree";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
import { useUserPermissions } from "@/hooks/store/user";

type Props = {
  collection: string;
};

export const WikiListRoot = observer(function WikiListRoot(props: Props) {
  const { collection } = props;
  // router
  const { workspaceSlug } = useParams();
  // plane hooks
  const { t } = useTranslation();
  // states
  const [pageIdToMove, setPageIdToMove] = useState<string | null>(null);
  // store hooks
  const { getFilteredPageIdsByCollection, fetchPagesList, removeFromWiki, pageParentIds } = usePageStore(
    EPageStoreType.WORKSPACE
  );
  const { allowPermissions } = useUserPermissions();
  // derived values
  const filteredPageIds = getFilteredPageIdsByCollection(collection);

  /**
   * 把当前分区的行排成树，只为拿到**层深**（设计 F-4）。
   *
   * 喂进去的是 `filteredPageIds`（已按搜索/排序过滤过的集合），**不是**全部页面 ——
   * 名次因此保持列表自己的排序，只是子行会紧跟到它父行后面。
   * 父页被过滤掉时子行按根渲染（`buildWikiTreeLines` 的兜底，设计 R-2）。
   *
   * 与侧栏**同一套算法**，不引入第二套层级算法（设计 F-4 的硬要求）。
   */
  const treeLines = useMemo(
    () =>
      buildWikiTreeLines({
        pageIds: filteredPageIds ?? [],
        getParentId: (pageId) => pageParentIds[pageId] ?? null,
      }),
    [filteredPageIds, pageParentIds]
  );
  // 写权限：行菜单里的两个动作都是**写**（PATCH 换集合 / DELETE 移出），后端只给
  // ADMIN/MEMBER（`apps/api/plane/app/views/page/collection.py` 的 partial_update/destroy）——
  // 这是**用户 2026-09-27 的裁定**，有意收窄设计与计划原文里的 `[ADMIN, MEMBER, GUEST]`。
  // 前端不收窄的话，GUEST 会看见一个点下去必然 403 的菜单项。
  // 与顶栏按钮、空态 CTA 用的是同一个谓词（工作区级 ADMIN/MEMBER）。
  const canWriteWiki = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
    EUserPermissionsLevel.WORKSPACE
  );

  /**
   * 移出 Wiki（只取消收录，不删页面）。
   * 与收录弹窗同款：落库后重拉当前分区（分区成员变了），**不 await、吞掉错误**。
   */
  const handleRemoveFromWiki = async (pageId: string) => {
    if (!workspaceSlug) return;
    try {
      await removeFromWiki(workspaceSlug, pageId);
    } catch {
      // 复用现成键（「无法将页面从集合中移除。」），不新增文案
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("common.toast.error"),
        message: t("wiki_collections.list.remove_error"),
      });
      return;
    }
    fetchPagesList(workspaceSlug, collection).catch(() => {});
  };

  /**
   * 工作区专属的**行内**动作。经由 `PageListBlock` 的 `extraActions` 传进去 ——
   * 它们是**工作区 store 的分区概念**（`collection` 查询参数 + `wiki-pages/` 端点），
   * 项目页没有对应物，所以由 wiki 侧构建、而不是在共享的 `PageListBlock` 里按 `storeType` 分支。
   * 放在这里而不是 `MoveToCollectionModal` 里，是因为「移到集合」需要把被点的**行**记下来。
   *
   * 权限：**只在 ADMIN/MEMBER 时传给行菜单**（调用处 `canWriteWiki ? … : undefined`）。
   * 两个动作都是写操作，后端在 2026-09-27 按用户裁定收窄到 `[ADMIN, MEMBER]`
   * （`apps/api/plane/app/views/page/collection.py` 的 partial_update/destroy）——
   * 这里不跟着收窄就会留下「看得见、点得动、一点就 403」的入口。
   * 同样用**隐藏**表达，而不是渲染成 disabled。
   */
  const buildRowActions = (pageId: string): (TContextMenuItem & { key: TPageActions })[] => [
    {
      key: "move-to-collection",
      action: () => setPageIdToMove(pageId),
      title: t("wiki_collections.menu.move_to_collection"),
      icon: FileOutput,
    },
    {
      key: "remove-from-wiki",
      action: () => {
        void handleRemoveFromWiki(pageId);
      },
      title: t("wiki_collections.menu.remove_from_wiki"),
      icon: X,
    },
  ];

  if (!filteredPageIds) return <></>;
  return (
    <>
      <ListLayout>
        {treeLines.map((line) => (
          // 缩进加在**外层 div** 上，不改共享的 `PageListBlock` ——
          // 那个组件被项目页共用，它没有也不该有「层级」这个概念。
          <div key={line.pageId} className={wikiTreeIndentClass(line.depth)}>
            <PageListBlock
              pageId={line.pageId}
              storeType={EPageStoreType.WORKSPACE}
              extraActions={canWriteWiki ? buildRowActions(line.pageId) : undefined}
            />
          </div>
        ))}
      </ListLayout>
      <MoveToCollectionModal
        isOpen={!!pageIdToMove}
        pageId={pageIdToMove}
        collection={collection}
        handleClose={() => setPageIdToMove(null)}
      />
    </>
  );
});
