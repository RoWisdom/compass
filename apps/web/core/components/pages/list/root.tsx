/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo, useState } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
// types
import type { TPageNavigationTabs } from "@plane/types";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
// components
import { ListLayout } from "@/components/core/list";
import { ProjectFolderListRow } from "@/components/pages/project-folder-list-row";
import { buildWikiTreeLines, isLineHiddenByExpansion, wikiTreeIndentClass } from "@/components/pages/tree/page-tree";
// plane web hooks
import { useUserPermissions } from "@/hooks/store/user";
import type { EPageStoreType } from "@/hooks/store";
import { usePageStore } from "@/hooks/store";
// services
import { PAGE_NODE_TYPE_FOLDER } from "@/services/page";
// local imports
import { PageListBlock } from "./block";

type TPagesListRoot = {
  pageType: TPageNavigationTabs;
  storeType: EPageStoreType.PROJECT;
};

export const PagesListRoot = observer(function PagesListRoot(props: TPagesListRoot) {
  const { pageType, storeType } = props;
  // router
  const { workspaceSlug, projectId } = useParams();
  // states —— **展开集**（空集 = 全部收起），口径与 wiki 侧逐字相同。
  // 存展开集而不是折叠集，理由写在 `tree/page-tree.ts` 的 `isLineHiddenByExpansion`
  // 上（默认值是空集，最省事也最不会漂）。
  const [expandedPageIds, setExpandedPageIds] = useState<string[]>([]);
  // store hooks
  const { getCurrentProjectFilteredPageIdsByTab, pageParentIds, getPageNodeType, fetchPagesTree } =
    usePageStore(storeType);
  const { allowPermissions } = useUserPermissions();
  // derived values
  const filteredPageIds = getCurrentProjectFilteredPageIdsByTab(pageType);

  /**
   * 把当前筛选后的 id 排成树，只为拿**层深与祖先链**（设计 §4.7）。
   * 同一套算法（`buildWikiTreeLines`），不引入第二套层级算法。
   *
   * 喂的是 `filteredPageIds`（已按 tab/搜索/排序过滤）—— 名次因此保持列表自己的
   * 排序，只是子行紧跟到父行后面。父行被滤掉时子行按根渲染（设计 R-2 兜底）。
   */
  const treeLines = useMemo(
    () =>
      buildWikiTreeLines({
        pageIds: filteredPageIds ?? [],
        getParentId: (pageId) => pageParentIds[pageId] ?? null,
      }),
    [filteredPageIds, pageParentIds]
  );

  const expandedSet = useMemo(() => new Set(expandedPageIds), [expandedPageIds]);

  /**
   * 有**当前可见**子项的行 —— 只有它们渲染折叠箭头。
   *
   * 判据走 `filteredPageIds` 而不是 `pageParentIds` 的全部值：一个子项全被筛掉的
   * 文件夹不该给箭头（点开什么都没有）。父页自己被筛掉时，子行按根渲染（R-2），
   * 所以这里也不会给它箭头 —— 两边一致。
   */
  const idsWithChildren = useMemo(() => {
    const visible = new Set(filteredPageIds ?? []);
    const parents = new Set<string>();
    for (const id of visible) {
      const parentId = pageParentIds[id];
      if (parentId && visible.has(parentId)) parents.add(parentId);
    }
    return parents;
  }, [filteredPageIds, pageParentIds]);

  /**
   * 行菜单里的动作（改名 / 移动 / 删除）都是**写**。`[ADMIN, MEMBER]` 是
   * `wiki-list-main-content.tsx:41-44` 给 wiki 侧写的**同一个谓词**（`EUserPermissions`
   * + `EUserPermissionsLevel`），项目侧只把 level 换成 `PROJECT` —— 两者数值等价
   * （`EUserPermissions` 与 `EUserProjectRoles` 都是 ADMIN=20 / MEMBER=15 / GUEST=5）。
   *
   * **已知边角**：后端 `destroy` 对「删」还要「所有者或项目 ADMIN」。所以一个**不是**
   * 文件夹所有者的 MEMBER 点「删除」会拿到 403 —— 前端没有把这颗菜单项收窄到
   * 所有者（`ProjectPage` 实例上没有 `owned_by` 的现成读法，为一个边角去加一层
   * 判据不划算）。这与 wiki 侧同一条纪律：那里也只按角色收窄。
   */
  const canWrite = allowPermissions([EUserPermissions.ADMIN, EUserPermissions.MEMBER], EUserPermissionsLevel.PROJECT);

  const handleToggle = (pageId: string) =>
    setExpandedPageIds((current) =>
      current.includes(pageId) ? current.filter((id) => id !== pageId) : [...current, pageId]
    );

  /** 动作落库后重拉整棵树。**不 await、吞掉错误** —— 动作这时已经落库。 */
  const refreshTree = () => {
    if (!workspaceSlug || !projectId) return;
    fetchPagesTree(workspaceSlug, projectId).catch(() => {});
  };

  if (!filteredPageIds) return <></>;
  return (
    <ListLayout>
      {treeLines
        .filter((line) => !isLineHiddenByExpansion(line, expandedSet))
        .map((line) => {
          // 类型从 store 的**旁挂索引**拿（不进 `BasePage`，理由见 `services/page`）。
          // 取不到 ⇒ 按页面渲染，那正是今天的既有行为（MobX 一变就自愈）。
          const isFolderRow = getPageNodeType(line.pageId) === PAGE_NODE_TYPE_FOLDER;
          return (
            // 缩进加在**外层 div** 上，不改共享的 `PageListBlock` —— 那个组件是
            // 「一个 Page」，它没有也不该有「层级」这个概念（与 wiki 同款）。
            <div key={line.pageId} className={wikiTreeIndentClass(line.depth)}>
              {isFolderRow ? (
                <ProjectFolderListRow
                  pageId={line.pageId}
                  isExpanded={expandedSet.has(line.pageId)}
                  hasChildren={idsWithChildren.has(line.pageId)}
                  onToggle={() => handleToggle(line.pageId)}
                  canWrite={canWrite}
                  onChanged={refreshTree}
                />
              ) : (
                <PageListBlock pageId={line.pageId} storeType={storeType} />
              )}
            </div>
          );
        })}
    </ListLayout>
  );
});
