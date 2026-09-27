/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
import { FileOutput, X } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { TContextMenuItem } from "@plane/ui";
// components
import { ListLayout } from "@/components/core/list";
import type { TPageActions } from "@/components/pages/dropdowns";
import { PageListBlock } from "@/components/pages/list/block";
import { MoveToCollectionModal } from "@/components/pages/wiki/move-to-collection-modal";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";

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
  const { getFilteredPageIdsByCollection, fetchPagesList, removeFromWiki } = usePageStore(EPageStoreType.WORKSPACE);
  // derived values
  const filteredPageIds = getFilteredPageIdsByCollection(collection);

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
   * 权限：不额外门控。后端 `wiki-pages/{id}/` 的 DELETE/PATCH 允许 `[ADMIN, MEMBER, GUEST]`，
   * 前端再藏一层只会把 GUEST 做得了的动作挡掉。
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
        {filteredPageIds.map((pageId) => (
          <PageListBlock
            key={pageId}
            pageId={pageId}
            storeType={EPageStoreType.WORKSPACE}
            extraActions={buildRowActions(pageId)}
          />
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
