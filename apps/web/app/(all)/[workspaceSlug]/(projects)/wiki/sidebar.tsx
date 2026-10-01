/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { Fragment, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { observer } from "mobx-react";
import Link from "next/link";
import { useParams, usePathname, useSearchParams } from "next/navigation";
import useSWR from "swr";
import { ChevronRight, MoreHorizontal, Plus } from "lucide-react";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { IconButton } from "@plane/propel/icon-button";
import { ChevronRightIcon, HomeIcon, PageIcon, PlusIcon } from "@plane/propel/icons";
import { CustomMenu } from "@plane/ui";
import { cn, getPageName } from "@plane/utils";
// components
import { CollectionFormModal } from "@/components/pages/wiki/collection-form-modal";
import { PageFormModal } from "@/components/pages/wiki/page-form-modal";
import {
  buildWikiTreeLines,
  groupPageIdsByPartition,
  isLineHiddenByCollapse,
  wikiTreeIndentClass,
} from "@/components/pages/wiki/wiki-tree";
import type { TWikiTreeLine } from "@/components/pages/wiki/wiki-tree";
import { SidebarNavItem } from "@/components/sidebar/sidebar-navigation";
import { SidebarWrapper } from "@/components/sidebar/sidebar-wrapper";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
import { useUserPermissions } from "@/hooks/store/user";
import { useAppRouter } from "@/hooks/use-app-router";
// services
import { isPredefinedCollectionKey } from "@/services/page";
import type { TPageCollection, TPageCreateTarget, TPredefinedCollectionKey } from "@/services/page";

/**
 * 「集合」组之外的预置分区，按官方侧栏的顺序排在集合组下面。
 *
 * **渲染成「分组标题」而不是可点行**（2026-10-01 用户裁定「「集合」与「已归档」是同一层级」）。
 * 两者都走 `renderGroupHeader`，同级关系由**共用实现**保证。
 *
 * **代价记账**：它因此失去图标与计数（原来 `renderRow` 给的），点击的含义也变了 ——
 * 标题上的点击是**折叠这一组**，不再是「跳到归档分区的列表页」。也就是说归档分区的
 * **列表页从此没有导航入口**（只能手敲 `?collection=archived`）。这与「私密」摘掉
 * 那一行是**同一类有意的不对称**，不是漏做。计数：`predefinedCount("archived")`
 * 仍有值，只是不再渲染。想恢复成行：把下面 `PARTITION_ROWS.map` 里的
 * `renderGroupHeader` 换回 `renderRow`。
 * 「已归档」也没有右侧动作位 —— 归档由 `archived_at` 决定，没有「新建一个归档页」
 * 这回事，与 `canCreateIn` 排除它的理由同源。
 *
 * **`private` 不在这里** —— 2026-10-01 用户裁定摘掉（「现阶段用不上」）。
 * 只摘**导航入口**，后端分区**原样保留**：`resolve_collection_key` 仍把 `access=1`
 * 解成 `private`，`page-collections/` 仍统计它。这么做是为了**不重新解释任何已有
 * 数据**（删后端那条分支会让已有的 `access=1` 页面落到别的分区去）。
 * 代价是一条**有意保留**的不对称：界面上没有入口，但手敲 `?collection=private`
 * 仍进得去（后果见 `pageTarget` 的注释）。想恢复只要把这个数组改回去。
 * （当时全库只有 1 个 `access=1` 的页面，且是验收夹具 `7e9fba3f`。）
 *
 * `shared` **也不在这里**，但性质不同 —— 是 `resolve_collection_key` **永不返回**它
 * （`apps/api/plane/utils/wiki_collections.py`：开源版没有「发布」字段，该分区恒空）。
 * 渲染一个永远空的分区只是噪音，旧版侧栏本来就把它过滤掉了
 * （`item.key !== "shared" || item.page_count > 0`），所以那不是行为变化。
 */
const PARTITION_ROWS: TPredefinedCollectionKey[] = ["archived"];

/**
 * 集合组的折叠键。**独立成一个常量**而不是在渲染处写字面量：它和 `PARTITION_ROWS`
 * 的值同处 `collapsedGroupKeys` 一个数组里，写成两处字面量迟早会漂。
 * 取 `"collections"` 而不是 `"general"` —— 这个组包含 General **和**全部自建集合，
 * 用 `"general"` 会读成"只是 General 那一个分区"。
 */
const COLLECTIONS_GROUP_KEY = "collections";

/**
 * 分组标题。**「集合」与「已归档」共用这一个实现** —— 「同一层级」这件事靠**共用**
 * 保证，不是靠两处各写一遍相同的类名（那正是日后会漂移的地方）。
 *
 * 放在**模块作用域**而不是组件内，正是为了让「共用」是结构上的：它拿不到任何
 * props / state / store（连 `t` 都拿不到），两个调用方唯一的差别只能从参数进来。
 * （顺带：放组件内会被 oxlint 的 `unicorn/consistent-function-scoping` 点名
 * 「does not capture any variables from its parent scope」—— 那条警告在说的
 * 就是同一件事。）
 *
 * **样式逐字对齐「项目」那一组**（`workspace/sidebar/projects-list.tsx:161-186` 的
 * 组标题 + 折叠箭头），两边同为 `rounded-sm px-2 py-1.5` + `text-13 font-semibold
 * text-placeholder`，悬停 `bg-layer-transparent-hover`。注意这套配色与行**相反**：
 * 标题字号更大更粗、颜色却更浅（`placeholder` = neutral-900 亮度 0.616，行的
 * `secondary` = neutral-1100 亮度 0.438）—— 这是上游既有的层级语言，照抄。
 *
 * **与参考实现的一处有意分歧**：`「项目」` 用 HeadlessUI `Disclosure` + `Transition`
 * 做淡入淡出，这里用**普通条件渲染**。理由是本文件的折叠状态本来就统一在组件本地的
 * `useState` 数组里（页面折叠 `collapsedPageIds` 是同一套），不引入第二个折叠机制；
 * 代价是没有过渡动画。
 *
 * `action` 是标题右侧的动作位，排在折叠箭头**左边**，目前只有「集合」用它挂建集合的
 * `＋`。「已归档」没有对应动作：归档由 `archived_at` 决定，不存在"新建一个归档页"
 * 这回事 —— 与 `canCreateIn` 排除它的理由同源。
 */
const renderGroupHeader = (props: { label: string; isOpen: boolean; onToggle: () => void; action?: ReactNode }) => {
  const { label, isOpen, onToggle, action } = props;
  return (
    <div className="flex w-full items-center justify-between rounded-sm px-2 py-1.5 text-placeholder hover:bg-layer-transparent-hover">
      {/* 点标题本身也折叠 —— 与「项目」一致（那边两个 Disclosure.Button 都绑同一动作）。 */}
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={isOpen}
        className="flex w-full items-center gap-1 text-left text-13 font-semibold whitespace-nowrap text-placeholder"
      >
        <span>{label}</span>
      </button>
      <div className="flex items-center gap-1">
        {action}
        <IconButton
          variant="ghost"
          size="sm"
          icon={ChevronRightIcon}
          onClick={onToggle}
          className="text-placeholder"
          iconClassName={cn("transition-transform", { "rotate-90": isOpen })}
          aria-label={label}
        />
      </div>
    </div>
  );
};

export const WikiSidebar = observer(function WikiSidebar() {
  // router
  const router = useAppRouter();
  const { workspaceSlug } = useParams();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  /**
   * URL 上**显式写出来**的集合。`null` = 没写 —— 也就是落在 wiki 索引页
   * （`/926/wiki/`，即侧栏那颗「主页」）。
   *
   * 它单独存在（而不是直接 `?? "general"`）是为了**只在用户真点了某一集合时**
   * 才给那一行加选中态。用户 2026-10-01 的要求：点 wiki 默认落到「主页」，
   * 不要再落到「常规」—— 在此之前 `activeCollection` 一旦兜底成 `"general"`，
   * 裸 URL 下「常规」行会亮起，看着像被选中了。
   */
  const explicitCollection = searchParams.get("collection");
  /**
   * 当前**在看**的集合。没显式指定时按 `general` 算 —— 与 `wiki/page.tsx:23`
   * 的 `?? "general"` 逐字一致，所以侧栏与主列表看的是同一份数据。
   *
   * 它决定「新建页面落到哪」（`pageTarget`）与「顶栏那颗 ＋ 出不出现」
   * （`newPageAction`），**不再**决定哪一行高亮 —— 高亮用 `explicitCollection`。
   */
  const activeCollection = explicitCollection ?? "general";

  /**
   * 当前打开的页面 id，用来给侧栏那一行加选中态。**取不到就是 `undefined`**
   * （索引路由 `/926/wiki/`、或还没进任何页面）⇒ 没有行高亮，正是想要的。
   *
   * **不能改用 `useParams()`**：本组件挂在 `(projects)/_sidebar.tsx`（祖先路由）下，
   * React Router 的 `useParams` 只返回**本路由层级**匹配到的参数，`[pageId]` 是更深的
   * 一段，拿不到（而 `workspaceSlug` 拿得到，因为 `(projects)` 自己就匹配它）。
   * `usePathname()` 读的是位置本身，在任何层级都对 —— 同 `_sidebar.tsx:37` 判
   * `isWikiPath` 的用法。`push` 会补尾斜杠（`compat/next/navigation.ts:17`），
   * 两种形状这里都取得到。
   */
  const activePageId = pathname.split(`/${workspaceSlug}/wiki/`)[1]?.split("/")[0] || undefined;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { predefined, collections, fetchCollections, fetchWikiTree, treeRows, pageParentIds, getPageById } =
    usePageStore(EPageStoreType.WORKSPACE);
  const { allowPermissions } = useUserPermissions();
  // state
  const [isFormOpen, setIsFormOpen] = useState(false);
  /** `null` = 新建模式；有值 = 重命名这个。弹窗的两种模式由它一个变量分叉。 */
  const [editingCollection, setEditingCollection] = useState<TPageCollection | null>(null);
  const [isPageFormOpen, setIsPageFormOpen] = useState(false);
  /** 折叠着的页面 id。存组件本地、不落库 —— 全库 8 个页面，持久化不划算（设计 §1.5）。 */
  const [collapsedPageIds, setCollapsedPageIds] = useState<string[]>([]);
  /**
   * 折叠着的**分组**。键是 `"collections"`（那个集合组）或某个预置分区键（目前只有
   * `"archived"`）。与 `collapsedPageIds` 同一套口径：组件本地、不落库。
   *
   * 不复用 `collapsedPageIds`：两者值域会撞车（预置分区键是字符串，页面 id 是 uuid，
   * 虽然实际不会重名，但把它们混在一个数组里，日后任何一个 `includes` 都读不出意图）。
   */
  const [collapsedGroupKeys, setCollapsedGroupKeys] = useState<string[]>([]);
  /** 建子页时记下父页 id；为 `null` 时走「建在当前分区」的老路。 */
  const [pageParentId, setPageParentId] = useState<string | null>(null);

  // 权限口径沿用 wiki-list-main-content.tsx:39-42 的同一谓词：写端点不含 GUEST。
  const canManageCollections = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
    EUserPermissionsLevel.WORKSPACE
  );

  /**
   * 某个分区下能不能建页。写端点只给 ADMIN/MEMBER；`archived` 分区下**不显示** ——
   * 新建的页面不可能是归档的（归档由 `archived_at` 决定，而它在
   * `resolve_collection_key` 里优先级最高）。
   *
   * **判据必须是「这一行所属的分区」**，不能是 `activeCollection`：侧栏树是**跨分区**
   * 渲染的（设计 F-1），用户在看 general 时会看到「归档」分区里的页面行。
   * 若按 `activeCollection` 判，那些行上会长出 `＋` —— 设计 §3.1.2 断言的
   * 「用户没有路径在归档父页下面点 ＋」就不成立了。一个谓词加一个参数，不新增第二个。
   *
   * **与顶栏收录按钮的 gating 有意分叉**：顶栏用 `canIncludeIntoCollection` 把
   * `private` / `archived` 都排除（`wiki/header.tsx`），因为把一个**已存在**的页面
   * "收录"进一个由 `access` 派生的分区没有意义；而**新建**页面落在当前查看的分区
   * 是自然动作。这条差异是有意的，别"修"成一致。
   *
   * 判据本身没随侧栏入口的增删调整过：`archived` 至今是**唯一**被排除的分区，
   * 因为它是唯一一个"新建出来的页面不可能属于"的分区（`archived_at` 不可能在
   * 新建时就非空）。`private` 则从来可以新建，只是 2026-10-01 起没有 UI 入口了。
   */
  const canCreateIn = (partitionKey: string) => canManageCollections && partitionKey !== "archived";

  /**
   * 新页面的落点（设计 §3.2d + B-3）。推导只在这里做一次，弹窗只负责把结果发出去。
   *
   * **建子页时只给 `parent`** —— `access` / `collection_id` 由后端从父页继承。
   * 前端抄一遍父页的值就是第二个真相源。
   *
   * **原有的一支 `activeCollection === "private"` → `{ access: 1 }`，随侧栏入口
   * 一起摘掉了**（见 `PARTITION_ROWS`）。当时它存在的理由：「私有」是
   * `resolve_collection_key` 里优先级高于 `collection_id` 的**派生**分区，库里没有
   * 一行叫 private 的集合可传，要新建私有页只能显式给 `access: 1`。
   *
   * 摘掉后**唯一**的副作用：手敲 `?collection=private` 进来时，顶栏仍会渲染
   * `＋ New page`（`canCreateIn` 只排除 `archived`），此时新建的页面走下面这支
   * 落进 `general`，不再进私密分区。没有 UI 路径能走到那个状态，所以没有为它加
   * 特判 —— 但它是**已知**的，不是没想到。
   *
   * `archived` 到不了这里（按钮已藏），`shared` 在 `resolve_collection_key` 里
   * **永不返回**。
   */
  const pageTarget: TPageCreateTarget = pageParentId
    ? { parent: pageParentId }
    : {
        // `general`（以及任何预置键）→ 不指定集合；自建集合 → 传它自己的 uuid。
        // 用现成的 `isPredefinedCollectionKey` 而不是手写 `=== "general"`：
        // 预置键的定义只有一处，加第五个分区时这里不用改。
        collection_id: isPredefinedCollectionKey(activeCollection) ? null : activeCollection,
        access: 0,
      };

  // 集合列表（含计数）
  useSWR(
    workspaceSlug ? `WIKI_COLLECTIONS_${workspaceSlug}` : null,
    workspaceSlug ? () => fetchCollections(workspaceSlug) : null
  );

  // 侧栏那棵树的数据（设计 B-5）。**是新增一次取数，不是替换** —— 计数仍来自
  // `page-collections/`（上面那条 SWR）。两者都过 `_visible_page_q`，口径一致（设计 §3.4）。
  useSWR(
    workspaceSlug ? `WIKI_TREE_${workspaceSlug}` : null,
    workspaceSlug ? () => fetchWikiTree(workspaceSlug) : null
  );

  const partitionKeyByPageId = useMemo(
    () => new Map(treeRows.map((row) => [row.pageId, row.collectionKey])),
    [treeRows]
  );

  /**
   * 分区键 → 该分区内的**扁平行**（带层深与祖先链）。
   *
   * 树只在这里建一次，侧栏与主列表共用 `wiki-tree.ts` 那套算法（设计 F-4）。
   * 每个分区分开建：父页在别的分区时，子行在本分区里当根渲染（设计 R-2 的兜底）。
   */
  const linesByPartition = useMemo(() => {
    const grouped = groupPageIdsByPartition(
      treeRows.map((row) => row.pageId),
      (pageId) => partitionKeyByPageId.get(pageId) ?? "general"
    );
    const built: Record<string, TWikiTreeLine[]> = {};
    for (const [key, pageIds] of Object.entries(grouped)) {
      built[key] = buildWikiTreeLines({
        pageIds,
        getParentId: (pageId) => pageParentIds[pageId] ?? null,
      });
    }
    return built;
  }, [treeRows, pageParentIds, partitionKeyByPageId]);

  const collapsedSet = useMemo(() => new Set(collapsedPageIds), [collapsedPageIds]);

  const goTo = (key: string) => router.push(`/${workspaceSlug}/wiki/?collection=${key}`);

  const goToPage = (pageId: string) => router.push(`/${workspaceSlug}/wiki/${pageId}`);

  /** 预置分区的计数。列表还没回来时按 0 算 —— 先把结构渲染出来，计数随后补齐。 */
  const predefinedCount = (key: string) => predefined.find((item) => item.key === key)?.page_count ?? 0;

  /** 集合组标题里那颗 `＋` —— 打开 `CollectionFormModal`（建集合，不是建页面）。 */
  const openCreateCollection = () => {
    setEditingCollection(null);
    setIsFormOpen(true);
  };

  /** 顶部那颗 `＋ New page` —— 落在**当前分区**，没有父页。 */
  const openCreate = () => {
    setPageParentId(null);
    setIsPageFormOpen(true);
  };

  /** 页面行 hover 出来的 `＋` —— 建这一页的子页。 */
  const openCreateChild = (pageId: string) => {
    setPageParentId(pageId);
    setIsPageFormOpen(true);
  };

  const closePageForm = () => {
    setIsPageFormOpen(false);
    setPageParentId(null);
  };

  const toggleCollapsed = (pageId: string) =>
    setCollapsedPageIds((current) =>
      current.includes(pageId) ? current.filter((id) => id !== pageId) : [...current, pageId]
    );

  const toggleGroupCollapsed = (groupKey: string) =>
    setCollapsedGroupKeys((current) =>
      current.includes(groupKey) ? current.filter((key) => key !== groupKey) : [...current, groupKey]
    );

  const openEdit = (collection: TPageCollection) => {
    setEditingCollection(collection);
    setIsFormOpen(true);
  };

  /**
   * 侧栏顶部的 `＋ New page`。
   *
   * 落点是 `SidebarWrapper` 的 **`quickActions` 插槽**（`sidebar-wrapper.tsx:26,:72`）——
   * 正是为这种"标题下面一行快捷动作"准备的。但它是**共享**插槽，不是本页独占：
   * Projects 侧栏已经在用它（`(projects)/sidebar.tsx` 的
   * `quickActions={<SidebarQuickActions />}`），所以在 `sidebar-wrapper.tsx` 里改这个
   * 槽位的样式或位置，会**同时**改掉 Projects 侧栏 —— 要动它就得两头一起看。
   *
   * 隐藏（**不渲染**）而不是 disabled：一个不解释原因的灰按钮和没有入口一样糟，
   * 与侧栏集合组标题里那个 `＋`（打开 `CollectionFormModal`、由
   * `canManageCollections` 门控）和顶栏收录按钮同一条口径。
   */
  const newPageAction = canCreateIn(activeCollection) ? (
    <button
      type="button"
      onClick={openCreate}
      className="flex w-full items-center gap-2 rounded-md border-[0.5px] border-subtle px-2 py-1.5 text-13 text-secondary hover:bg-layer-1/50"
    >
      <Plus className="h-3.5 w-3.5" />
      {t("wiki_collections.menu.create_new_page")}
    </button>
  ) : undefined;

  /**
   * 集合组里的一行：`general` 或一个用户自建集合。**分区不再走这里** ——
   * `archived` 自 2026-10-01 起渲染成分组标题（见 `renderGroupHeader`），
   * 因为用户要求它与「集合」同级。代价见 `PARTITION_ROWS` 的注释。
   *
   * `options` 只给用户自建集合传 —— `General` 是**派生**分区（`collection_id IS NULL`），
   * 重命名它没有落点；官方那张图里 General 是真实行所以有 `⋯`，罗盘结构不同。
   *
   * 高亮判据是 **`explicitCollection` 而不是 `activeCollection`**：裸 URL（`/926/wiki/`）
   * 下 `activeCollection` 会兜底成 `"general"`，用它判会让「常规」行在「主页」上亮起。
   * 只有用户真点了某一集合（URL 里带了 `?collection=`）才高亮那一行。
   */
  const renderRow = (key: string, label: string, count: number, options?: ReactNode) => (
    <div key={key} className="flex w-full items-center gap-1">
      <button
        type="button"
        onClick={() => goTo(key)}
        className={cn(
          "flex min-w-0 flex-1 items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-13",
          explicitCollection === key
            ? "bg-layer-transparent-selected text-primary"
            : "text-secondary hover:bg-layer-transparent-hover"
        )}
      >
        <span className="flex items-center gap-2 truncate">
          <PageIcon className="h-4 w-4 text-tertiary" />
          <span className="truncate">{label}</span>
        </span>
        <span className="text-11 text-tertiary">{count}</span>
      </button>
      {options}
    </div>
  );

  /**
   * 一个分区下的页面行。**默认展开**（裁定 7）：`collapsedPageIds` 为空时没有任何行被藏。
   *
   * 标题用 `getPageName`，与主列表（`pages/list/block.tsx:63`）**同一处**取名字 ——
   * 空名页两边都渲染成硬编码英文 "Untitled"。这是「发现 ② 延后」的直接后果：
   * 修它要动共享的 `getPageName`（纯 util 包拿不到 `t()`），波及全站 Page 列表。
   * 这里**不**改用 `wiki_collections.list.untitled` —— 那会让侧栏与列表对同一个页面
   * 显示两个不同的名字，比英文更糟。
   */
  const renderPageRow = (line: TWikiTreeLine, partitionKey: string) => {
    const page = getPageById(line.pageId);
    if (!page || isLineHiddenByCollapse(line, collapsedSet)) return null;
    const hasChildren = (linesByPartition[partitionKey] ?? []).some((candidate) =>
      candidate.ancestorIds.includes(line.pageId)
    );
    const isCollapsed = collapsedSet.has(line.pageId);
    const isActive = line.pageId === activePageId;

    return (
      <div key={line.pageId} className="group flex w-full items-center gap-1">
        {/* 折叠三角只在真有子页时渲染 —— 一个点了没反应的三角是噪音。 */}
        {hasChildren ? (
          <button
            type="button"
            onClick={() => toggleCollapsed(line.pageId)}
            aria-label={getPageName(page.name)}
            aria-expanded={!isCollapsed}
            className="rounded-sm p-0.5 text-tertiary hover:bg-layer-1 hover:text-secondary"
          >
            <ChevronRight className={cn("h-3 w-3 transition-transform", !isCollapsed && "rotate-90")} />
          </button>
        ) : (
          <span className="w-4" />
        )}
        <button
          type="button"
          onClick={() => goToPage(line.pageId)}
          className={cn(
            "flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1 text-left text-13",
            wikiTreeIndentClass(line.depth),
            // 选中态与集合行（上面的 renderRow）**用同一对类**，而且用的就是
            // **全站侧栏的既有约定**（`core/components/sidebar/sidebar-item.tsx:59-60`
            // 的 `iconActive` / `iconInactive`、`settings/sidebar/item.tsx:31`）。
            //
            // **为什么不用 `bg-layer-1` 那一档**：`--neutral-200`（layer-1）的
            // oklch 亮度是 0.9696、侧栏底色 `--neutral-white` 是 1.000 —— 只差 0.030，
            // 肉眼基本分不出（这正是当初"看不出选中了哪一页"的成因之一；当时页面行
            // 还坐在集合组容器的 `bg-layer-1/50` 上，那一档只剩 0.015。容器底色
            // 2026-10-01 已去掉，但**不要因此把选中态降回 layer-1** —— 0.030 依然太弱）。
            // `--bg-layer-transparent-selected` 是 15% 黑，有效亮度约 0.872 ⇒ **差 0.13**，
            // 强 4 倍以上；而且它是**半透明**的，将来容器若真加了底色也叠得对，
            // 不需要原先那套"容器 50% < hover 75% < 选中 100%"的调色推理。
            isActive ? "bg-layer-transparent-selected text-primary" : "text-secondary hover:bg-layer-transparent-hover"
          )}
        >
          <PageIcon className="h-3.5 w-3.5 shrink-0 text-tertiary" />
          <span className="truncate">{getPageName(page.name)}</span>
        </button>
        {/* 入口（裁定 5）：**只在 ADMIN/MEMBER、且本行不在归档分区时**渲染。
            hover 才出现，平时不占视觉重量。 */}
        {canCreateIn(partitionKey) && (
          <button
            type="button"
            onClick={() => openCreateChild(line.pageId)}
            aria-label={t("wiki_collections.menu.create_new_page")}
            className="rounded-sm p-0.5 text-tertiary opacity-0 group-hover:opacity-100 hover:bg-layer-1 hover:text-secondary"
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
    );
  };

  /** 一个分区下的整棵页面树。空分区渲染 `null`，不留空档。 */
  const renderTree = (partitionKey: string) => {
    const lines = linesByPartition[partitionKey];
    if (!lines?.length) return null;
    return <div className="flex w-full flex-col">{lines.map((line) => renderPageRow(line, partitionKey))}</div>;
  };

  return (
    <SidebarWrapper title="Wiki" quickActions={newPageAction}>
      <div className="flex w-full flex-col gap-1">
        {/*
          「首页」行 —— 对齐「项目」侧栏顶部那颗：条目定义见
          `workspace/sidebar/user-menu.tsx:26-33`，渲染见
          `workspace/sidebar/user-menu-item.tsx:58-65`（两颗都是 `HomeIcon`）。
          这里复用**同一个** `SidebarNavItem`，不另写一套类名，
          所以行高/悬停/选中态与「项目」那边同源。

          **标签用 `wiki_home.title`，不共用那颗的 `sidebar.home`** —— 用户 2026-10-01 裁定。
          两个键在 17 个语言里同值，只有 zh-CN 分叉：Wiki 这颗要「首页」，
          而 `sidebar.home`（「项目」那颗 + 工作区首页）仍是「主页」。
          分叉只此一处，改 `sidebar.home` 会连带改掉「项目」侧栏 —— 那是另一个决定。

          **指向 `/{slug}/wiki/`（wiki 索引页），与参考那颗不同** —— 参考指向
          `/{slug}/`（工作区首页）。这是**有意分叉**，用户 2026-10-01 裁定：本侧栏只在
          wiki 路径下渲染（`(projects)/_sidebar.tsx:71`），这里给的是「wiki 自己的首页」，
          点了留在 wiki；参考那颗点了会离开 wiki、整个侧栏切回 AppSidebar。

          选中判据是 **`!activePageId && !explicitCollection`**，两半都不能少：
          - `!activePageId` —— 本侧栏只在 wiki 路径下渲染，所以「没有 pageId」就等价于
            「在索引页」，`/926/wiki` 与 `/926/wiki/` 两种尾斜杠写法都覆盖得到
            （`activePageId` 的推导见上方注释）；
          - `!explicitCollection` —— 用户真点了 `?collection=xxx` 时，亮的是**那一行**
            （见 `renderRow`），「首页」必须让位，否则两行同时亮。
        */}
        <Link href={`/${workspaceSlug}/wiki/`}>
          <SidebarNavItem isActive={!activePageId && !explicitCollection}>
            <div className="flex items-center gap-1.5 py-[1px]">
              <HomeIcon className="size-4 flex-shrink-0" />
              <p className="text-13 leading-5 font-medium">{t("wiki_home.title")}</p>
            </div>
          </SidebarNavItem>
        </Link>

        {/*
          「集合」组 = `general` 预置分区 + 全部用户自建集合。官方把 General 摆在
          集合组下，这里对齐。

          组标题借的是 `wiki_collections.fallback_name`（en "Collection" / zh「集合」）：
          i18n 里没有专给组标题的键，而新增一个键要同步 18 份 locale 文件。
          代价是这个键会同时承担两个用途（「集合没有名字时的称呼」与「组标题」）；
          日后要区分，再补 `wiki_collections.title` 并把这里换过去。
        */}
        {/*
          集合组容器。**已无底色** —— 2026-10-01 用户裁定去掉（原为
          `bg-layer-1/50 rounded-md p-1`，那层淡底与选中行只差 0.015，选哪行看不出来）。
          `p-1` 必须跟着一起去：没有背景之后，它只会让集合区比下面的
          已归档区多缩进 4px，看着像排版错了。

          现在这层容器的作用是**折叠面板的边界**：标题收在它里面，折叠时整块内容一起
          消失。「集合」与「已归档」的标题都由 `renderGroupHeader` 出，两者因此
          **结构上严格同级**（用户 2026-10-01 的要求）—— 不是靠两处各写一遍类名维持的。
          **不要顺手把底色加回来** —— 那会把选中态的对比度重新吃掉。
        */}
        <div className="flex w-full flex-col gap-1">
          {renderGroupHeader({
            label: t("wiki_collections.fallback_name"),
            isOpen: !collapsedGroupKeys.includes(COLLECTIONS_GROUP_KEY),
            onToggle: () => toggleGroupCollapsed(COLLECTIONS_GROUP_KEY),
            // 对 GUEST **隐藏**而不是 disabled —— 与既有口径一致，理由见
            // wiki-list-main-content.tsx 的同一谓词。
            action: canManageCollections ? (
              <IconButton
                variant="ghost"
                size="sm"
                icon={PlusIcon}
                onClick={openCreateCollection}
                className="text-placeholder"
                aria-label={t("wiki_collections.create_modal.title")}
              />
            ) : undefined,
          })}
          {/* 折叠时整块内容（General + 自建集合 + 它们各自的树）一起消失，标题留着 ——
              与「项目」的 Disclosure.Panel 同一行为。 */}
          {!collapsedGroupKeys.includes(COLLECTIONS_GROUP_KEY) && (
            <>
              {/* 每个集合行**紧跟**它自己分区下的页面树 —— 集合 → 父页 → 子页 的形状
                  就是靠这个相邻关系表达的，不是靠缩进。 */}
              {renderRow("general", t("wiki_collections.predefined.general"), predefinedCount("general"))}
              {renderTree("general")}
              {collections.map((collection) => (
                <Fragment key={collection.id}>
                  {renderRow(
                    collection.id,
                    collection.name,
                    collection.page_count,
                    canManageCollections ? (
                      <CustomMenu
                        customButton={<IconButton icon={MoreHorizontal} variant="ghost" size="sm" />}
                        ariaLabel={t("wiki_collections.menu.collection_options")}
                        closeOnSelect
                      >
                        <CustomMenu.MenuItem onClick={() => openEdit(collection)}>
                          {t("wiki_collections.menu.edit_collection")}
                        </CustomMenu.MenuItem>
                      </CustomMenu>
                    ) : undefined
                  )}
                  {renderTree(collection.id)}
                </Fragment>
              ))}
            </>
          )}
        </div>

        {/* 标题，不是行 —— 见 `PARTITION_ROWS` 的注释（同级关系的由来与代价记账都在那）。 */}
        {PARTITION_ROWS.map((key) => (
          <Fragment key={key}>
            {renderGroupHeader({
              label: t(`wiki_collections.predefined.${key}`),
              isOpen: !collapsedGroupKeys.includes(key),
              onToggle: () => toggleGroupCollapsed(key),
            })}
            {!collapsedGroupKeys.includes(key) && renderTree(key)}
          </Fragment>
        ))}
      </div>

      {/*
        弹窗挂在侧栏自己身上，**不**提到 `wiki/layout.tsx` 的 provider 层：侧栏
        （`(projects)/_sidebar.tsx:71`）根本不在那个 provider 的子树里，而且它只有
        侧栏一个调用方、侧栏又是单实例，一份 state 就够。详见
        `collection-form-modal.tsx` 的 docblock。
      */}
      <CollectionFormModal
        isOpen={isFormOpen}
        collection={editingCollection}
        handleClose={() => setIsFormOpen(false)}
        onCreated={(created) => router.push(`/${workspaceSlug}/wiki/?collection=${created.id}`)}
      />

      <PageFormModal isOpen={isPageFormOpen} handleClose={closePageForm} target={pageTarget} />
    </SidebarWrapper>
  );
});
