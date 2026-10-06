/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo, useState } from "react";
import { observer } from "mobx-react";
import { useParams, useSearchParams } from "next/navigation";
import { FileOutput } from "lucide-react";
// types
import type { TPageNavigationTabs } from "@plane/types";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { EmptyStateDetailed } from "@plane/propel/empty-state";
import type { TContextMenuItem } from "@plane/ui";
// components
import { ListLayout } from "@/components/core/list";
import type { TPageActions } from "@/components/pages/dropdowns";
import { ProjectFolderListRow } from "@/components/pages/project-folder-list-row";
import { ProjectMoveToModal } from "@/components/pages/project-move-to-modal";
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
  /** 当前下钻的文件夹 id（来自 `?folder=`）。`null` = 根视图。 */
  folderId?: string | null;
};

export const PagesListRoot = observer(function PagesListRoot(props: TPagesListRoot) {
  const { pageType, storeType, folderId } = props;
  // router
  const { workspaceSlug, projectId } = useParams();
  // plane hooks
  const { t } = useTranslation();
  // states —— **展开集**（空集 = 全部收起），口径与 wiki 侧逐字相同。
  // 存展开集而不是折叠集，理由写在 `tree/page-tree.ts` 的 `isLineHiddenByExpansion`
  // 上（默认值是空集，最省事也最不会漂）。
  const [expandedPageIds, setExpandedPageIds] = useState<string[]>([]);
  // 正在被「移动到…」的那一行的 id。`null` = 弹窗关着（与 wiki 侧同款：一个
  // 弹窗 + 用 state 记住被点的行，而不是每行各挂一个弹窗）。
  const [pageIdToMove, setPageIdToMove] = useState<string | null>(null);
  // store hooks
  const { getCurrentProjectFilteredPageIdsByTab, pageParentIds, getPageNodeType, fetchPagesTree } =
    usePageStore(storeType);
  const { allowPermissions } = useUserPermissions();
  // derived values
  const filteredPageIds = getCurrentProjectFilteredPageIdsByTab(pageType);

  /** `?folder=` 指向的真的是个文件夹才认；否则（不存在/已删/手改 URL）退回根视图。 */
  const activeFolderId = folderId && getPageNodeType(folderId) === PAGE_NODE_TYPE_FOLDER ? folderId : null;

  /**
   * 下钻时只留**这个文件夹的直接子节点**（`pageParentIds[id] === activeFolderId`）。
   * 父 id 不在集合里 ⇒ `buildWikiTreeLines` 把它们当根渲（R-2 兜底），depth 全为 0，
   * 缩进自然归零；又因为它们是**兄弟**，`idsWithChildren` 算出来是空集，
   * 所以下钻视图里**没有折叠箭头** —— 无需额外代码。
   */
  const scopedPageIds = useMemo(
    () =>
      activeFolderId
        ? (filteredPageIds ?? []).filter((id) => (pageParentIds[id] ?? null) === activeFolderId)
        : filteredPageIds,
    [activeFolderId, filteredPageIds, pageParentIds]
  );

  /**
   * 把当前筛选后的 id 排成树，只为拿**层深与祖先链**（设计 §4.7）。
   * 同一套算法（`buildWikiTreeLines`），不引入第二套层级算法。
   *
   * 喂的是 `scopedPageIds`（已按 tab/搜索/排序过滤，下钻时再收窄到直接子节点）——
   * 名次因此保持列表自己的排序，只是子行紧跟到父行后面。父行被滤掉时子行按根渲染
   * （设计 R-2 兜底）。
   */
  const treeLines = useMemo(
    () =>
      buildWikiTreeLines({
        pageIds: scopedPageIds ?? [],
        getParentId: (pageId) => pageParentIds[pageId] ?? null,
      }),
    [scopedPageIds, pageParentIds]
  );

  const expandedSet = useMemo(() => new Set(expandedPageIds), [expandedPageIds]);

  /**
   * 有**当前可见**子项的行 —— 只有它们渲染折叠箭头。
   *
   * 判据走 `scopedPageIds` 而不是 `pageParentIds` 的全部值：一个子项全被筛掉的
   * 文件夹不该给箭头（点开什么都没有）。父页自己被筛掉时，子行按根渲染（R-2），
   * 所以这里也不会给它箭头 —— 两边一致。
   */
  const idsWithChildren = useMemo(() => {
    const visible = new Set(scopedPageIds ?? []);
    const parents = new Set<string>();
    for (const id of visible) {
      const parentId = pageParentIds[id];
      if (parentId && visible.has(parentId)) parents.add(parentId);
    }
    return parents;
  }, [scopedPageIds, pageParentIds]);

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

  const searchParams = useSearchParams();

  /**
   * 造下钻链接：**保留当前 URL 的全部 query**（尤其 `?type=`），只覆盖 `folder`。
   * 不手写 `?type=${pageType}` —— 那会把「没有 type 参数的 public」写成 `type=public`，
   * 与用户在地址栏看到的不一致。
   */
  const buildFolderLink = (folderPageId: string) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("folder", folderPageId);
    return `/${workspaceSlug}/projects/${projectId}/pages/?${params.toString()}`;
  };

  /** 动作落库后重拉整棵树。**不 await、吞掉错误** —— 动作这时已经落库。 */
  const refreshTree = () => {
    if (!workspaceSlug || !projectId) return;
    fetchPagesTree(workspaceSlug, projectId).catch(() => {});
  };

  /**
   * 页面行的行内动作 —— 目前只有「移动到…」一项（罗盘 Round J 修复）。
   *
   * 形状逐字照 wiki 的 `wiki-list-root.tsx:146-161`（`buildRowActions`）：给每一行造一个
   * `TContextMenuItem & { key: "move-to" }`，经由 `PageListBlock` 的 `extraActions`
   * 传到 `PageActions`（它会把 key 追加进 `optionsOrder`，否则菜单项不会被渲染）。
   * 弹窗在**列表 root** 上渲染**一个**，用 `pageIdToMove` 记住被点的行 —— 不是每行各挂一个。
   *
   * 权限：**只在 `canWrite` 时传给行菜单**（调用处 `canWrite ? … : undefined`）。
   * 移到别处是写操作，与文件夹行的 `•••`（同一个 `canWrite`）用**同一道门** ——
   * 不跟着收窄就会让 GUEST 看见一个点下去必然 403 的项。同样用**隐藏**表达，
   * 而不是渲染成 disabled。
   *
   * **只给页面行**：文件夹行已经有自己的 `•••`（`ProjectFolderListRow`），不要动它们。
   */
  const buildRowActions = (pageId: string): (TContextMenuItem & { key: TPageActions })[] => [
    {
      key: "move-to",
      action: () => setPageIdToMove(pageId),
      // 零新增键：复用 wiki 那一项（也是 `ProjectMoveToModal` 标题用的那个键）。
      title: t("wiki_collections.menu.move_to"),
      icon: FileOutput,
    },
  ];

  if (!filteredPageIds) return <></>;

  // 下钻到一个**可见子节点为空**的文件夹 —— 给空态，而不是一片空白。
  // 只给标题、不给 description：`wiki_collections.list.no_pages_description` 的字面
  // 是「This collection doesn't have any pages right now.」—— 在项目文件夹语境里
  // 名词是错的；而新增一条文案要在 19 个语言文件里同加。标题键本身名词中立。
  if (activeFolderId && (scopedPageIds ?? []).length === 0)
    return <EmptyStateDetailed assetKey="page" title={t("wiki_collections.list.no_pages_title")} />;

  return (
    <>
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
                    itemLink={buildFolderLink(line.pageId)}
                    isExpanded={expandedSet.has(line.pageId)}
                    hasChildren={idsWithChildren.has(line.pageId)}
                    onToggle={() => handleToggle(line.pageId)}
                    canWrite={canWrite}
                    onChanged={refreshTree}
                  />
                ) : (
                  <PageListBlock
                    pageId={line.pageId}
                    storeType={storeType}
                    extraActions={canWrite ? buildRowActions(line.pageId) : undefined}
                  />
                )}
              </div>
            );
          })}
      </ListLayout>
      {/* 整棵列表共用一个弹窗。`pageId` 传被点的那一行的 id；`ProjectPageStore.moveTo`
          对任何 page id 都成立（它只是发 `{ parent }`）。落库后重拉整棵树 —— 复用
          文件夹行 `onChanged` 用的同一个 `refreshTree`。 */}
      <ProjectMoveToModal
        isOpen={!!pageIdToMove}
        pageId={pageIdToMove}
        onMoved={refreshTree}
        handleClose={() => setPageIdToMove(null)}
      />
    </>
  );
});
