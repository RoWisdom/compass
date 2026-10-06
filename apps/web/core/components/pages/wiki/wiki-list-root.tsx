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
import { FolderListRow } from "@/components/pages/wiki/folder-list-row";
import { MoveToModal } from "@/components/pages/wiki/move-to-modal";
import { buildWikiTreeLines, wikiTreeIndentClass } from "@/components/pages/tree/page-tree";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
import { useUserPermissions } from "@/hooks/store/user";
// services
import { PAGE_NODE_TYPE_FOLDER } from "@/services/page";

type Props = {
  /**
   * 当前视图的键：预置分区键、自建集合 uuid，或**文件夹 uuid**（`isFolder` 时）。
   * 三者共用一个值域是刻意的 —— 见 `wiki/store` 的 `collectionPageIds`（同一个键空间）
   * 与 `fetchFolderPages` 的注释。
   */
  collection: string;
  /** 这个键是一个**文件夹**（`?folder=`）而不是集合/分区。 */
  isFolder?: boolean;
};

export const WikiListRoot = observer(function WikiListRoot(props: Props) {
  const { collection, isFolder = false } = props;
  // router
  const { workspaceSlug } = useParams();
  // plane hooks
  const { t } = useTranslation();
  // states
  const [pageIdToMove, setPageIdToMove] = useState<string | null>(null);
  // store hooks
  const {
    getFilteredPageIdsByCollection,
    fetchPagesList,
    fetchFolderPages,
    getPageNodeType,
    removeFromWiki,
    pageParentIds,
  } = usePageStore(EPageStoreType.WORKSPACE);
  const { allowPermissions } = useUserPermissions();
  // derived values
  const filteredPageIds = getFilteredPageIdsByCollection(collection);

  /**
   * 把当前视图的行排成树，只为拿到**层深**（设计 F-4）。
   *
   * 喂进去的是 `filteredPageIds`（已按搜索/排序过滤过），**不是**全部页面 ——
   * 名次因此保持列表自己的排序，只是子行会紧跟到它父行后面。
   * 父行被过滤掉时子行按根渲染（`buildWikiTreeLines` 的兜底，设计 R-2）。
   *
   * **Round D 起这里也服务文件夹视图**：`?folder=` 返回的是整棵子树且**含子文件夹**
   * （后端裁定 5），而"含子文件夹"正是为了让这一层的 `ancestorIds` 连得起来 ——
   * 否则摆在子文件夹里的页面全都会拿到层深 0，设计 §10 第 4 条要的缩进直接落空。
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
  // 写权限：两处行菜单里的动作都是**写** —— 页面行的「移动到… / 移出 Wiki」（PATCH 换集合 /
  // DELETE 移出），文件夹行的「移动到… / 重命名 / 删除」（Round F 起三项，同一个 `•••` 组件）。
  // 后端只给 ADMIN/MEMBER（`apps/api/plane/app/views/page/collection.py` 的 partial_update/destroy）——
  // 这是**用户 2026-09-27 的裁定**，有意收窄设计与计划原文里的 `[ADMIN, MEMBER, GUEST]`。
  // 前端不收窄的话，GUEST 会看见一个点下去必然 403 的菜单项。
  // 与顶栏按钮、空态 CTA 用的是同一个谓词（工作区级 ADMIN/MEMBER）。
  const canWriteWiki = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
    EUserPermissionsLevel.WORKSPACE
  );

  /**
   * 重拉**当前这个视图**。两种视图的键都放在 `collectionPageIds` 里（同一个键空间），
   * 所以查询那一半完全同形；分叉的只有**打哪个端点** —— `?collection=` 还是 `?folder=`。
   *
   * 这个分叉必须在**这里**做一次，不能让调用方各自记着：`?collection=<文件夹 uuid>`
   * **不报错**，它会按 `collection_id` 过滤后静默返回空列表，把用户的右半边无声清空。
   *
   * **不 await、吞掉错误**：动作这时已经落库，让刷新失败把它报成错误是撒谎
   * （store 自己会把失败记进 `this.error`）。与收录弹窗同款。
   */
  const refreshList = () => {
    if (!workspaceSlug) return;
    const request = isFolder ? fetchFolderPages(workspaceSlug, collection) : fetchPagesList(workspaceSlug, collection);
    request.catch(() => {});
  };

  /**
   * 移出 Wiki（只取消收录，不删页面）。
   * 落库后重拉当前视图（成员变了），**不 await、吞掉错误** —— 见 `refreshList`。
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
    refreshList();
  };

  /**
   * 工作区专属的**行内**动作。经由 `PageListBlock` 的 `extraActions` 传进去 ——
   * 它们是**工作区 store 的分区概念**（`collection` 查询参数 + `wiki-pages/` 端点），
   * 项目页没有对应物，所以由 wiki 侧构建、而不是在共享的 `PageListBlock` 里按 `storeType` 分支。
   * 放在这里而不是 `MoveToModal` 里，是因为「移到集合」需要把被点的**行**记下来。
   *
   * 权限：**只在 ADMIN/MEMBER 时传给行菜单**（调用处 `canWriteWiki ? … : undefined`）。
   * 两个动作都是写操作，后端在 2026-09-27 按用户裁定收窄到 `[ADMIN, MEMBER]`
   * （`apps/api/plane/app/views/page/collection.py` 的 partial_update/destroy）——
   * 这里不跟着收窄就会留下「看得见、点得动、一点就 403」的入口。
   * 同样用**隐藏**表达，而不是渲染成 disabled。
   *
   * **Round D：只给页面行**（裁定 11）。文件夹行有自己的 `•••`（Round E 起），里面的
   * 动作是「移动到… / 重命名 / 删除」（Round F 起三项）—— 与这里的两个**不是同一套**：本函数给页面的
   * 「移出 Wiki」对文件夹没有对应物（文件夹的对应动作是删除，语义见后端 `destroy`），
   * 所以两处各自构建，不合并。
   */
  const buildRowActions = (pageId: string): (TContextMenuItem & { key: TPageActions })[] => [
    {
      key: "move-to",
      action: () => setPageIdToMove(pageId),
      title: t("wiki_collections.menu.move_to"),
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
        {treeLines.map((line) => {
          // **类型从 store 的旁挂索引拿**（裁定 6）：列表响应里**没有** `node_type`
          // （`WikiPageSerializer` 一个字不动）。取不到 ⇒ 按页面渲染，那是**今天的
          // 既有行为**（裁定 7 的已知后果，MobX 一变就自愈）。
          const isFolderRow = getPageNodeType(line.pageId) === PAGE_NODE_TYPE_FOLDER;
          return (
            // 缩进加在**外层 div** 上，不改共享的 `PageListBlock` ——
            // 那个组件被项目页共用，它没有也不该有「层级」这个概念。
            <div key={line.pageId} className={wikiTreeIndentClass(line.depth)}>
              {isFolderRow ? (
                // 子文件夹：可点的行（下钻进它自己的 `?folder=`），**有** `•••`（Round E 起，裁定 11 作废）。
                <FolderListRow pageId={line.pageId} canWrite={canWriteWiki} onChanged={refreshList} />
              ) : (
                <PageListBlock
                  pageId={line.pageId}
                  storeType={EPageStoreType.WORKSPACE}
                  extraActions={canWriteWiki ? buildRowActions(line.pageId) : undefined}
                />
              )}
            </div>
          );
        })}
      </ListLayout>
      <MoveToModal
        isOpen={!!pageIdToMove}
        pageId={pageIdToMove}
        onMoved={refreshList}
        handleClose={() => setPageIdToMove(null)}
      />
    </>
  );
});
