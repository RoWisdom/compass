/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { Fragment, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { observer } from "mobx-react";
import Link from "next/link";
import { useParams, usePathname, useSearchParams } from "next/navigation";
import useSWR from "swr";
import { Box, Folder, MoreHorizontal, Plus } from "lucide-react";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { IconButton } from "@plane/propel/icon-button";
import { ChevronRightIcon, HomeIcon, PageIcon, PlusIcon } from "@plane/propel/icons";
import { CustomMenu } from "@plane/ui";
import { cn, getPageName } from "@plane/utils";
// components
import { CollectionFormModal } from "@/components/pages/wiki/collection-form-modal";
import { DeleteCollectionModal } from "@/components/pages/wiki/delete-collection-modal";
import { FolderRowActions } from "@/components/pages/wiki/folder-row-actions";
import { PageFormModal } from "@/components/pages/wiki/page-form-modal";
import {
  WIKI_TREE_INDENT_CLASS,
  buildWikiTreeLines,
  groupPageIdsByPartition,
  isLineHiddenByExpansion,
} from "@/components/pages/wiki/wiki-tree";
import type { TWikiTreeLine } from "@/components/pages/wiki/wiki-tree";
import { SidebarNavItem } from "@/components/sidebar/sidebar-navigation";
import { SidebarWrapper } from "@/components/sidebar/sidebar-wrapper";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
import { useUserPermissions } from "@/hooks/store/user";
import { useAppRouter } from "@/hooks/use-app-router";
// services
import { PAGE_NODE_TYPE_DOC, PAGE_NODE_TYPE_FOLDER, isPredefinedCollectionKey } from "@/services/page";
import type { TPageCollection, TPageCreateTarget, TPageNodeType, TPredefinedCollectionKey } from "@/services/page";

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
 * 仍进得去（后果见 `containerTarget` 的注释）。想恢复只要把这个数组改回去。
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
 * 侧栏页面树怎么表达层级 —— **Notion 方案**（用户 2026-10-02 裁定）。
 *
 * 这一版**推翻了** 2026-10-01 的「竖虚线」方案（当时：层级靠一列虚线表达、
 * 页面行一个箭头都没有）。推翻的理由不是审美，是一个**功能缺口**：那一版为了让
 * 行本身兼任折叠开关，把「有子页 ⇒ 点击展开、不跳转」写在了行上，于是
 * **有子页的页面在侧栏里点不开**；再叠加「默认收起」，从主列表打开一个深层页时
 * 侧栏里连高亮都看不到（那一行压根没渲染）。用户：「这个问题还挺严重的，
 * 造成有些页面无法选中」，裁定换方案。
 *
 * 现在的四条，与 Notion 侧栏一致：
 *
 * 1. **行就是链接** —— 点哪一行都打开那一页，父页也一样（见 `renderPageRow`）；
 * 2. **行左那一格平时是页面图标，鼠标移到这一行时原地换成展开箭头**（用户
 *    2026-10-02 指出 Notion 就是这个交互），只有真有子页的行才换；
 *    点箭头**只**展开/收起、不跳转，点标题才打开这一页 —— 开关因此有自己的
 *    命中区，不再和「打开这一页」抢同一个点击；
 * 3. **打开深层页时祖先自动展开**（组件里那个 `useEffect`），那一页永远看得见、
 *    也永远是高亮的；
 * 4. **层级靠纯缩进**，整条侧栏没有任何连接线 —— 一格 12px
 *    （`TREE_INDENT_CELL_CLASS`）。
 *
 * **「集合」「已归档」两颗分组标题不动**（用户明说）：它们继续走 `renderGroupHeader`
 * 那套 `px-2 py-1.5 text-13 font-semibold` + **常显**折叠箭头。本文件里「页面行」
 * 与「分组标题」是**有意不同**的两套语言。
 *
 * 横向位置 = 8px 基准（行容器的 `px-2`，与分组标题文字左缘对齐）+ 12px × 格数。
 * 量的就是**页面图标**那一格 —— 箭头与图标共用它（见第 2 条），所以「层级」
 * 直接读作「图标缩进到哪儿」：
 *
 * | 行 | 格数 |
 * |---|---|
 * | 首页 / 「集合」/「已归档」标题 | 不走本表，各自有既定样式 |
 * | 集合行（常规、自建集合） | **0** —— 图标与分组标题文字**左对齐** |
 * | 页面 | `1 + 分区内层深`（层深封顶 3）|
 *
 * **集合行 0 格**（用户 2026-10-02 验收：「「集合」的子节点太靠中间或靠右了，
 * 和「集合」对齐」）：它的箭头正好落在「集合」两个字的左缘上，读作
 * 「这一组的条目从这里起」。上一版给集合行留了 1 格、页面从 2 起，整棵子树比现在
 * 再右移 12px —— 那正是被指出的问题。
 *
 * **页面从 1 起**：集合行占掉了第 0 格（它自己那格箭头），页面必须比它深一档，
 * 否则集合行和它自己的页面落在同一列上，看不出谁属于谁。
 * 「已归档」那一组没有集合行，它的页面同样从 1 起 —— 两组**有意**一致：
 * 同一份树不管挂在哪个组下，深度的读法应该一样。
 */
const COLLECTION_ROW_LEVEL = 0;
const PAGE_BASE_LEVEL = 1;

/**
 * 页面行的层深**封顶**，与 `wiki-tree.ts` 的缩进封顶同源 —— 那边那个数组有 4 档
 * （`pl-0/3/6/9`）就代表最深 3 层，这里换算成「最多再加几格」。硬写 3 就是第二个真相源。
 */
const TREE_DEPTH_CAP = WIKI_TREE_INDENT_CLASS.length - 1;

/**
 * 行容器的公共类。**底色画在容器上、不画在标题按钮上**：箭头那一格在按钮之外，
 * 底色若留在按钮里，鼠标划到箭头那一格时整行只会亮半截。
 *
 * `group` 是给行左那格的**图标→箭头**互换用的（`group-hover:opacity-0` /
 * `group-hover:opacity-100`），也管行右那颗 `＋` 的浮出。
 * `px-2` 的 8px 是基准缩进，与分组标题（`renderGroupHeader` 也是 `px-2`）的文字
 * 左缘对齐，第 0 格从这里起算。`rounded-md` 与全站侧栏的行一致。
 */
const TREE_ROW_BASE_CLASS = "group flex w-full items-center gap-1 rounded-md px-2";

/**
 * 一格缩进，12px。**纯留白、不画线** —— Notion 方案里层级只由缩进表达。
 *
 * 仍然一格一个 `<span>`、而不是给容器加一个 `pl-N`：缩进档数是变量（`2 + 层深`），
 * 而 `pl-*` 是固定刻度，按层深拼类名拼不出干净的档位表（12px 一档会落到 `pl-17`
 * 这种不存在的刻度上），写内联 `style` 又会绕开主题变量。一格一个 12px 的子元素
 * 没有这些问题，而且与 `wiki-tree.ts` 的 `WIKI_TREE_INDENT_CLASS`（同样 12px 一档）
 * 用的是同一个单位 —— 主列表与侧栏的缩进读法因此一致。
 */
const TREE_INDENT_CELL_CLASS = "w-3 shrink-0";

/**
 * 画 `level` 格缩进。`level <= 0` **什么都不画**（集合行就是这一档）——
 * 不能只留一个空的 `<div>`：宽度为 0 的块在 `renderLeadCell` 那个 flex 里虽然不占位，
 * 但留着它只会让人以为第 0 档也有东西。
 *
 * 外面**多套一层 `flex`**（`renderLeadCell`），格子不直接当行容器的子元素：
 * 行上有 `gap-1`，格子若平铺，档距会变成 12+4=16px、与「一格 12px」对不上。
 * 套一层之后格子彼此紧挨，且**与右边那格图标之间不留缝** —— 层深每加一档正好走 12px。
 */
const renderTreeIndent = (level: number) =>
  level > 0 ? (
    <div className="flex shrink-0">
      {Array.from({ length: level }, (_, index) => (
        <span key={index} className={TREE_INDENT_CELL_CLASS} />
      ))}
    </div>
  ) : null;

/**
 * 行左那一格：**平时放页面图标，鼠标移到这一行时整格换成展开箭头**
 * （用户 2026-10-02：「notion 的交互是默认显示图标，当鼠标移动到页面上时将图标换成箭头」）。
 *
 * 图标与箭头**叠在同一个 grid 格里**（两边都是 `col-start-1 row-start-1`），所以换的时候
 * 是**原地交叉淡入**、没有任何位移。这两件事是同一个决定的两半：只要它们不共处一格，
 * 「换」就会变成「推」。
 *
 * 没有子页的行放一个同类的 `<span>`（图标常驻、永不换）：格子必须一样宽，
 * 否则同一层的图标会一左一右错开。
 *
 * 格子固定 16px（`size-4`）——**这一格永远在**，所以悬停换内容时标题纹丝不动。
 * 箭头取 12px（`size-3`），比页面图标（14px）小一号：它是附属标记，不该和页面图标
 * 抢视觉重量，与 Notion 一致。
 */
const TREE_LEAD_SLOT_CLASS = "grid size-4 shrink-0 place-items-center";

/**
 * 行左的**缩进 + 图标格**整块。
 *
 * 两者必须**紧挨**：缩进格与图标格之间若被行容器的 `gap-1` 插进 4px，层深每加一档
 * 就多走 4px，「一层 12px」这个唯一口径就没了。
 * 右边那个 `mr-1` 是**图标与标题之间**的呼吸位（连同容器的 `gap-1` 共 8px）。
 */
const renderLeadCell = (level: number, content: ReactNode) => (
  <div className="mr-1 flex shrink-0 items-center">
    {renderTreeIndent(level)}
    {content}
  </div>
);

/**
 * 「在集合里就删掉、不在就加上」。**三处折叠状态共用这一个**（页面行的展开集、
 * 集合行的展开集、分组标题的折叠集）—— 三份都只是同一个取反，各写一遍迟早有一处写反。
 *
 * 返回的是 `setState` 的 updater 形状，所以调用处一律写成
 * `setXxx(toggleKey(key))`，不读闭包里的旧值。
 */
const toggleKey = (key: string) => (keys: string[]) =>
  keys.includes(key) ? keys.filter((current) => current !== key) : [...keys, key];

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
 * **这颗折叠箭头（`ChevronRightIcon`）要留着。** 2026-10-01「不要出现箭头」那一轮
 * **只针对页面行**（见 `renderPageRow`），分组标题这两颗是用户明确要求保留的 ——
 * 别顺手一起删。理由是它们同时承担两件事：与「项目」组保持一致，
 * 以及**唯一**的「这一组能收」指示（收起后整块内容消失，没有别的标志）。
 * 2026-10-02 换成 Notion 方案时用户又强调了一遍「这两颗不动」——
 * 页面行那边新长出来的箭头是**悬停才浮出**的，两颗标题箭头则**常显**，
 * 两处现在是**有意不同**的两套语言，别"统一"。
 *
 * **与参考实现的一处有意分歧**：`「项目」` 用 HeadlessUI `Disclosure` + `Transition`
 * 做淡入淡出，这里用**普通条件渲染**。理由是本文件的折叠状态统一放在组件本地的
 * `useState` 数组里（`collapsedGroupKeys`），不引入第二个折叠机制；代价是没有过渡动画。
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
   * URL 上**显式写出来**的文件夹。`null` = 没写。
   *
   * 与 `explicitCollection` **并列、互斥**：两者同时在 URL 里时，`wiki/page.tsx`
   * 让 `folder` 优先（裁定 1）。这里双双读出来，只为把"哪一行高亮"判对 ——
   * 集合行看 `explicitCollection`、文件夹行看 `explicitFolder`。
   */
  const explicitFolder = searchParams.get("folder");
  /**
   * 当前**在看**的集合。没显式指定时按 `general` 算 —— 与 `wiki/page.tsx:23`
   * 的 `?? "general"` 逐字一致，所以侧栏与主列表看的是同一份数据。
   *
   * 它决定「新建页面落到哪」（`containerTarget`）与「顶栏那颗 ＋ 出不出现」
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
  // 留着 store 本身不只是为了解构：`handleCollectionDeleted` 必须在**运行时**读
  // `pageStore.collections` / `pageStore.predefined`（理由见那个 handler）。
  const pageStore = usePageStore(EPageStoreType.WORKSPACE);
  const {
    predefined,
    collections,
    fetchCollections,
    fetchPagesList,
    fetchWikiTree,
    treeRows,
    pageParentIds,
    getPageById,
    getPageNodeType,
  } = pageStore;
  const { allowPermissions } = useUserPermissions();
  // state
  const [isFormOpen, setIsFormOpen] = useState(false);
  /** `null` = 新建模式；有值 = 重命名这个。弹窗的两种模式由它一个变量分叉。 */
  const [editingCollection, setEditingCollection] = useState<TPageCollection | null>(null);
  /** `null` = 没有要删的集合（弹窗只是被关着）。与 `editingCollection` 同一形状。 */
  const [deletingCollection, setDeletingCollection] = useState<TPageCollection | null>(null);
  const [isPageFormOpen, setIsPageFormOpen] = useState(false);
  /**
   * **展开着的页面 id。空集 ⇒ 整棵树默认收起**，只露顶层页面（用户 2026-10-01：
   * 「子页面默认收起」）。
   *
   * 谁来往里加：① 点行左那颗悬停才浮出的箭头（`renderPageRow` 的
   * `toggleExpanded`）—— 注意**不是**点标题，2026-10-02 起标题是纯链接；
   * ② 进到某一页时自动补开它的祖先链（组件下面那个 `useEffect`）。
   * 隐藏判据见 `isLineHiddenByExpansion`。
   *
   * 存**展开**集而不是折叠集：空集是天然、稳定的默认值。存折叠集的话默认值得是
   * 「全部页面 id」—— 树是异步来的、之后还会新增页面，那个默认值每加一页都要补一次，
   * 漏一处就成了「新页面默认展开」，与默认收起自相矛盾。
   *
   * 组件本地、不落库（同 `collapsedGroupKeys`）。
   */
  const [expandedPageIds, setExpandedPageIds] = useState<string[]>([]);
  /**
   * 折叠着的**分组**。键是 `"collections"`（那个集合组）或某个预置分区键（目前只有
   * `"archived"`）。组件本地、不落库。
   *
   * 与页面级的 `expandedPageIds` 是**两套**状态，不复用：值域会撞车（预置分区键是
   * 字符串、页面 id 是 uuid，混在一个数组里日后任何 `includes` 都读不出意图），
   * 而且**语义正好相反**（一个存展开、一个存折叠）—— 合在一起只会更容易写反。
   */
  const [collapsedGroupKeys, setCollapsedGroupKeys] = useState<string[]>([]);
  /**
   * **展开着的集合行**：`"general"` 或某个自建集合的 uuid。**空集 ⇒ 每个集合行都默认收起**
   * （用户 2026-10-02 二次验收：「「集合」下面的节点默认收起来，不要展开」）。组件本地、不落库。
   *
   * 同日一次验收「「常规」及平级节点不能收起子节点」修的是**能不能收**（那格箭头点得动）；
   * 这一条改的是**默认值**。两件事不冲突，别把箭头也一并去掉。
   *
   * 与 `collapsedGroupKeys` **刻意分开两套**，理由是**值域会撞车**：那边存的是
   * **UI 分组**的键（`"collections"` 这个组、`"archived"` 这个组标题），这边存的是
   * **分区**的键。`"collections"` 根本不是分区键，混进一个数组之后任何 `includes`
   * 都读不出一行，分不清说的是哪一层。
   *
   * 存**展开**集、空集 ⇒ 默认**收起** —— 与页面级那套（`expandedPageIds`）**同向**，
   * 两套都取「空集」这个天然、稳定的默认值：树是异步来的、之后还会新增集合，
   * 存折叠集的话默认值得是「全部分区键」，每加一个集合就要补一次，漏一处就成了
   * 「新集合默认展开」，与默认收起自相矛盾。
   *
   * 早先这里是**折叠**集、空集 ⇒ 默认展开，理由是「集合下面就是它全部的页面，
   * 默认收起来等于把侧栏掏空」；用户看过实际侧栏后改判为默认收起，故翻向。
   */
  const [expandedPartitionKeys, setExpandedPartitionKeys] = useState<string[]>([]);
  /**
   * 这一次「新建」的落点。`null` = 弹窗没开。
   *
   * **它取代了早先那个 `pageParentId: string | null`**：`＋` 从"一个动作"变成"两项下拉"
   * 之后，"建什么"也成了一个维度，而落点与类型是**一个整体的请求**，拆成两个 state
   * 就有"类型设了、落点没设"的中间态。整份 `TPageCreateTarget` 正好就是请求体，
   * 于是弹窗接到的 `target` 原样就是 `createPage` 的 payload（只差 `name`）。
   */
  const [pageCreateTarget, setPageCreateTarget] = useState<TPageCreateTarget | null>(null);

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
   * 一个**集合行 / 分区**的落点（设计 §3.2d + B-3）。推导只在这里做一次，
   * 弹窗只负责把结果发出去。
   *
   * **建子项时只给 `parent`** —— `access` / `collection_id` 由后端从父项继承。
   * 前端抄一遍父项的值就是第二个真相源。
   *
   * **原有的一支 `activeCollection === "private"` → `{ access: 1 }`，随侧栏入口
   * 一起摘掉了**（见 `PARTITION_ROWS`）。当时它存在的理由：「私有」是
   * `resolve_collection_key` 里优先级高于 `collection_id` 的**派生**分区，库里没有
   * 一行叫 private 的集合可传，要新建私有页只能显式给 `access: 1`。
   *
   * 摘掉后**唯一**的副作用：手敲 `?collection=private` 进来时，顶栏仍会渲染
   * `＋ New page`（`canCreateIn` 只排除 `archived`），此时新建的页面走这一支
   * 落进 `general`，不再进私密分区。没有 UI 路径能走到那个状态，所以没有为它加
   * 特判 —— 但它是**已知**的，不是没想到。
   *
   * `archived` 到不了这里（按钮已藏），`shared` 在 `resolve_collection_key` 里
   * **永不返回**。
   */
  const containerTarget = (key: string): TPageCreateTarget => ({
    // `general`（以及任何预置键）→ 不指定集合；自建集合 → 传它自己的 uuid。
    // 用现成的 `isPredefinedCollectionKey` 而不是手写 `=== "general"`：
    // 预置键的定义只有一处，加第五个分区时这里不用改。
    collection_id: isPredefinedCollectionKey(key) ? null : key,
    access: 0,
  });

  /**
   * 打开新建弹窗。**页面与文件夹共用一个入口**（Confluence F7：新建文件夹不是独立
   * 功能、没有独立按钮、没有独立对话框 —— 它就是同一个 `＋` 下拉里的一项）。
   *
   * `node_type` **只在文件夹时写进 target**：页面走后端的默认值（`"doc"`），
   * 前端不重复声明一遍默认值。于是 `target.node_type` 同时充当"这次建的是什么"的
   * 判据（弹窗据此藏项目选择器、据此决定建完跳哪儿）。
   */
  const openCreateDialog = (target: TPageCreateTarget, nodeType: TPageNodeType) => {
    setPageCreateTarget(nodeType === PAGE_NODE_TYPE_FOLDER ? { ...target, node_type: PAGE_NODE_TYPE_FOLDER } : target);
    setIsPageFormOpen(true);
  };

  /**
   * 行右那颗 `＋` —— 悬停浮出的下拉（设计 §5.3）。
   *
   * 两项**并列**：新建页面、新建文件夹。落点由调用方给（页面行/文件夹行给
   * `{ parent }`，集合行/`general` 行给 `containerTarget(key)`），所以同一个函数
   * 服务四种行 —— 这正是"一个 `＋`、两种节点"的形状。
   *
   * `aria-label` 用 `common.add_new`（19 个 locale 都有，"Add new" / 「添加新的」）：
   * 触发器的名字**不能**再叫 `create_new_page`（那现在是菜单里的一项，叫这个会指错），
   * 而为一个 aria-label 新增 i18n 键要再动 19 个语言文件，不值。零新增键是这里的取舍。
   */
  const renderCreateMenu = (target: TPageCreateTarget) => (
    <CustomMenu
      customButton={<IconButton icon={Plus} variant="ghost" size="sm" className="opacity-0 group-hover:opacity-100" />}
      ariaLabel={t("common.add_new")}
      closeOnSelect
    >
      <CustomMenu.MenuItem onClick={() => openCreateDialog(target, PAGE_NODE_TYPE_DOC)}>
        {t("wiki_collections.menu.create_new_page")}
      </CustomMenu.MenuItem>
      <CustomMenu.MenuItem onClick={() => openCreateDialog(target, PAGE_NODE_TYPE_FOLDER)}>
        {t("wiki_collections.menu.create_new_folder")}
      </CustomMenu.MenuItem>
    </CustomMenu>
  );

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

  const expandedSet = useMemo(() => new Set(expandedPageIds), [expandedPageIds]);

  /** 页面 id → 它那一行（带层深与祖先链）。`linesByPartition` 是按分区存的，这里拍平成一张表。 */
  const lineByPageId = useMemo(() => {
    const map = new Map<string, TWikiTreeLine>();
    for (const lines of Object.values(linesByPartition)) {
      for (const line of lines) map.set(line.pageId, line);
    }
    return map;
  }, [linesByPartition]);

  /**
   * 进到某一页时，把它的**祖先链**展开（Notion 方案第 3 条，用户 2026-10-02 裁定）。
   *
   * 没有这一步，「默认收起」+「行即链接」会留下一个洞：从主列表打开一个深层页时，
   * 侧栏里那一行根本没渲染（被 `isLineHiddenByExpansion` 滤掉了），于是
   * **看不到任何高亮**，看着像没选中 —— 这正是用户说「有些页面无法选中」的后半截。
   * 祖先链一开，当前页永远在树里可见。
   *
   * 只展开**祖先**、不展开当前页自己：用户要的是「我在哪儿」，不是「这一页下面有什么」；
   * 一进来就把它整棵子树铺开会比收起更吵。
   *
   * **同理放开这一页所属的集合行**（`expandedPartitionKeys`）：默认收起的「常规」同样会让
   * 里面每一页都消失 —— 同一个洞，只是高了一层。留着不补的话，用户从主列表点进
   * 「常规」下面任何一页，侧栏里都是空的。
   *
   * **分组标题的折叠（`collapsedGroupKeys`）不在此列，是刻意的**：分界是
   * 「收起来之后看不看得出来」。页面行与集合行的箭头**悬停才浮出**，收起后没有任何
   * 可见迹象，所以必须自动放开，否则用户根本找不到自己那一页；
   * 而分组标题那颗箭头**常显**，收起来时它是横躺的、一眼能看见也能点开 ——
   * 那是一个**看得见的**状态，程序不该替用户改掉。这条线比「统统自动展开」更好用。
   *
   * 依赖 `lineByPageId` 而不只是 `activePageId`：首屏树还没落地时这张表是空的，
   * 树一到这个 effect 会再跑一次，那时才拿得到祖先链。
   *
   * **不强行掰回来**：它只在页面切换 / 树变化时跑。用户手动收起某个祖先之后不再触发，
   * 那次收起因此是有效的 —— 否则那颗箭头会变成一个点了没反应的死控件。
   * 没有变化时返回 `current` 本身（而不是一个等值的新数组），免掉一次无谓的重渲染。
   */
  useEffect(() => {
    // **锚点**：正在看的那一页（`activePageId`）或正在看的那一个文件夹
    // （`explicitFolder`）。两者共用这段 —— 否则冷启动直接打开
    // `?folder=<深层文件夹>` 时，侧栏是"默认全部收起"，连高亮那一行都看不见，
    // 正是上一轮用户说的「有些页面无法选中」的同一个洞。
    const anchorId = activePageId ?? explicitFolder ?? undefined;
    if (!anchorId) return;
    // **只展开祖先，不展开锚点自己**：展开自己会把它下面的子项一次性摊开，
    // 那是用户没要求的动作（与页面那条既有口径一致）。
    const ancestors = lineByPageId.get(anchorId)?.ancestorIds ?? [];
    if (ancestors.length)
      setExpandedPageIds((current) => {
        const missing = ancestors.filter((id) => !current.includes(id));
        return missing.length ? [...current, ...missing] : current;
      });
    const partitionKey = partitionKeyByPageId.get(anchorId);
    if (!partitionKey) return;
    setExpandedPartitionKeys((current) => (current.includes(partitionKey) ? current : [...current, partitionKey]));
  }, [activePageId, explicitFolder, lineByPageId, partitionKeyByPageId]);

  const goTo = (key: string) => router.push(`/${workspaceSlug}/wiki/?collection=${key}`);

  const goToPage = (pageId: string) => router.push(`/${workspaceSlug}/wiki/${pageId}`);

  /** 进某个文件夹的列表视图。与 `goTo`（集合）**同一个落点形状**，只换了参数名。 */
  const goToFolder = (folderId: string) => router.push(`/${workspaceSlug}/wiki/?folder=${folderId}`);

  /** 预置分区的计数。列表还没回来时按 0 算 —— 先把结构渲染出来，计数随后补齐。 */
  const predefinedCount = (key: string) => predefined.find((item) => item.key === key)?.page_count ?? 0;

  /** 集合组标题里那颗 `＋` —— 打开 `CollectionFormModal`（建集合，不是建页面）。 */
  const openCreateCollection = () => {
    setEditingCollection(null);
    setIsFormOpen(true);
  };

  const closePageForm = () => {
    setIsPageFormOpen(false);
    setPageCreateTarget(null);
  };

  const toggleExpanded = (pageId: string) => setExpandedPageIds(toggleKey(pageId));

  const toggleGroupCollapsed = (groupKey: string) => setCollapsedGroupKeys(toggleKey(groupKey));

  /** 集合行那一格箭头 —— 收/放它自己分区下的整棵页面树。**默认收起**（见 `expandedPartitionKeys`）。 */
  const togglePartitionExpanded = (partitionKey: string) => setExpandedPartitionKeys(toggleKey(partitionKey));

  const openEdit = (collection: TPageCollection) => {
    setEditingCollection(collection);
    setIsFormOpen(true);
  };

  /**
   * 集合删掉之后的收尾。
   *
   * 只有一件事必须做：**删的是 URL 正指着的那一个**时，那一屏已经没有对应的集合了
   * （`?collection=<uuid>` 指向一个不存在的集合 ⇒ 列表恒空），跳回「常规」。
   * 判据用**重拉之后**的 store 状态：`deleteCollection` 内部已经重拉过集合列表，
   * 所以不必把「刚删了哪个」再传进来 —— 那会让弹窗多一个只为跳转存在的参数，
   * 而 `DeleteCollectionModal` 与 `DeleteFolderModal` 的 props 保持不变才有对照价值。
   *
   * 再有就是右侧列表：`fetchWikiTree` **不写** `collectionPageIds`（列表读的是
   * `fetchPagesList`）。用户正看着**常规**时去删另一个集合，被删集合的页面此刻正
   * 应该出现在那一屏里 —— 不补这一刀，那几行要等下一次取数才冒出来。
   * 侧栏这棵树不用管：`deleteCollection` 内部已经重拉（同 `FolderRowActions` 传空
   * 函数的理由，见 `renderPageRow` 里那段注释）。
   */
  const handleCollectionDeleted = () => {
    if (!workspaceSlug) return;
    // **必须读 `pageStore` 而不是上面解构出来的 `predefined` / `collections`**：那两个
    // 绑定是**这次渲染时的快照**。`DeleteCollectionModal` 的 `handleDelete` 先
    // `handleClose()` 再 `onDeleted()`，用的是点击那一刻捕获的闭包 —— 跑这个 handler 时
    // 组件早已重渲过，快照指向删除**前**的数组；而 `fetchCollections` 是**整体赋值**
    // （`workspace-page.store.ts` 的 `this.collections = response.collections`），快照
    // 永远不会变成新值。读快照则 `stillExists` 恒为 `true`，跳转分支永不触发。
    // 删除动作在 resolve 之前 `await` 过 `fetchCollections`，所以此刻读 store 拿到的
    // 就是删除后的状态，分支判断才是对的。
    const isPredefined = pageStore.predefined.some((item) => item.key === explicitCollection);
    const stillExists = pageStore.collections.some((collection) => collection.id === explicitCollection);
    if (explicitCollection && !isPredefined && !stillExists) {
      router.push(`/${workspaceSlug}/wiki/?collection=general`);
      return;
    }
    fetchPagesList(workspaceSlug, activeCollection).catch(() => {});
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
      onClick={() => openCreateDialog(containerTarget(activeCollection), PAGE_NODE_TYPE_DOC)}
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
   *
   * 行左那一格**收/放这个分区下的整棵页面树**（用户 2026-10-02 验收要求；
   * 之前只能展开不能收，只能靠折叠整个「集合」组把它一起盖掉）。**默认收起**
   * （同日二次验收：「「集合」下面的节点默认收起来」）—— 收起时只露集合行自己。
   * 手法与页面行**逐字相同**：那一格平时是图标、悬停换成箭头、`aria-expanded` 报状态。
   * 分区里一页都没有时（如 226 的「测试222」）那一格只有图标、永不换。
   *
   * 集合行自己**没有缩进格**（`COLLECTION_ROW_LEVEL` 是 0），所以它那格正好压在
   * 「集合」两个字的左缘上 —— 这正是用户要的「和「集合」对齐」。
   * 行右那一格（`⋯` 左边）是 Round D 新增的 `＋` 下拉 —— 收录进 Wiki 的**新建**
   * 入口之一，与页面行那颗同源（`renderCreateMenu`）。
   *
   * **行左那格的图标是 `Box`（lucide），不是页面行那颗 `PageIcon`** —— 用户
   * 2026-10-03 指出：集合行与页面行用的是同一颗图标，一眼看过去分不出谁是容器。
   * 现在三个层级各一颗、互不复用：**集合 `Box` / 文件夹 `Folder` / 页面 `PageIcon`**，
   * 层级读作「盒子 ⊃ 文件夹 ⊃ 页面」。
   *
   * 集合挑**立体**方块而不是平面图形，是**在 16px 上比过**的：平的那几颗在这个尺寸都塌掉 ——
   * `Library` 变成四根竖线（像条码）、`SquareStack` 像「复制」、`LayoutGrid` 撞 propel 的
   * `ModuleIcon`（四宫格套方框）。方块是这一列里唯一的立体图形，与上一行页面、下一行
   * 文件夹那两颗平面描边/填色图标**一眼可分**，而这正是这条意见要的。
   */
  const renderRow = (key: string, label: string, count: number, options?: ReactNode) => {
    // 判据与页面行同源（`linesByPartition`），所以「收起之后箭头还在」——树本身没变，
    // 变的只是渲不渲染。空分区（`linesByPartition[key]` 取不到）没有可收的东西。
    const hasChildren = (linesByPartition[key] ?? []).length > 0;
    const isExpanded = expandedPartitionKeys.includes(key);

    return (
      <div
        key={key}
        className={cn(
          TREE_ROW_BASE_CLASS,
          "py-1.5",
          explicitCollection === key
            ? "bg-layer-transparent-selected text-primary"
            : "text-secondary hover:bg-layer-transparent-hover"
        )}
      >
        {/* 缩进 0 格 + 图标格。**图标搬出了标题按钮**（原来在它里面）——
            Notion 交互要求图标与箭头在同一格里换，那就得让它先站在那一格里。 */}
        {renderLeadCell(
          COLLECTION_ROW_LEVEL,
          hasChildren ? (
            <button
              type="button"
              onClick={() => togglePartitionExpanded(key)}
              aria-expanded={isExpanded}
              // 与分组标题那颗箭头同一种写法：`aria-label` 就用这一行的名字。
              aria-label={label}
              className={cn(TREE_LEAD_SLOT_CLASS, "rounded-sm text-tertiary hover:text-secondary")}
            >
              {/* 两个图标**都不写颜色**：颜色由按钮那格给（静止 `text-tertiary`、
                  悬停 `text-secondary`），跟在上一版那颗箭头后面继承同一套，
                  免得两处各写一份迟早对不上。 */}
              <Box className="col-start-1 row-start-1 h-4 w-4 transition-opacity group-focus-within:opacity-0 group-hover:opacity-0" />
              <ChevronRightIcon
                className={cn(
                  "col-start-1 row-start-1 size-3 opacity-0 transition group-focus-within:opacity-100 group-hover:opacity-100",
                  { "rotate-90": isExpanded }
                )}
              />
            </button>
          ) : (
            <span className={TREE_LEAD_SLOT_CLASS}>
              <Box className="h-4 w-4 text-tertiary" />
            </span>
          )
        )}
        <button
          type="button"
          onClick={() => goTo(key)}
          className="flex min-w-0 flex-1 items-center justify-between gap-2 text-left text-13"
        >
          <span className="truncate">{label}</span>
          <span className="text-11 text-tertiary">{count}</span>
        </button>
        {/* 行右那颗 `＋` 下拉（F7）。建的是**这个分区的根级节点** —— `general` 行传
            `collection_id: null`，自建集合行传它自己的 uuid；两者都由 `containerTarget`
            推出来，不在这里各写一遍。
            排在 `{options}`（自建集合的 `⋯`）**之前**：动作在菜单之左，与页面行
            那颗 `＋` 的位置一致。 */}
        {canCreateIn(key) && renderCreateMenu(containerTarget(key))}
        {options}
      </div>
    );
  };

  /**
   * 一个分区下的一行（**Notion 方案 + Round D 的文件夹**，四条见文件上方那段总说明）。
   *
   * **点标题一定打开那一行自己**（`goToPage` / `goToFolder`）—— 这是这一版修掉的核心问题：
   * 上一版把「有子页 ⇒ 点击展开」写在行上，结果**有子页的页面在侧栏里点不开**（当时只能
   * 从主列表进）。开关挪回它自己那块命中区（行左那颗悬停才浮出的箭头）之后，
   * 行重新是纯链接，「一个点击既展开又打开」这个取舍也就不存在了。
   *
   * **Round D：打开的落点按类型分叉** —— 页面进编辑器（`goToPage`，`/wiki/<id>`），
   * 文件夹进**它自己的列表视图**（`goToFolder`，`?folder=<id>`）。这是设计 §7 第 7 条那处
   * 与 Confluence 的**有意偏差**：那边点文件夹只有展开/折叠，罗盘的文件夹有右侧列表。
   * **展开状态与页面行共用 `expandedPageIds`**：一棵树一套展开状态，不新开
   * `expandedFolderIds` —— 两套迟早漂。
   *
   * **静止态那格是这一行的类型图标**（页面 `PageIcon` / 文件夹 `Folder`），
   * 悬停才在原地换成箭头（用户 2026-10-02：这才是 Notion 的交互）。两者**叠在同一格里
   * 交叉淡入**，所以换的时候没有任何横移；静止态因此仍然一个箭头都没有
   * （用户 2026-10-01 的要求没被推翻），而「这一行有没有子项」的指示器
   * 又回来了 —— 上一版为了不留箭头，把那个指示器整个丢了。
   * 用 `opacity` 而不是 `invisible`：`opacity-0` 的元素**仍可聚焦**，键盘用户 Tab 得到；
   * `visibility: hidden` 会把它从 tab 序列里摘掉，那颗箭头就只剩鼠标够得着了。
   * （`group-focus-within` 与 `group-hover` 并列，就是给这条兜底。）
   *
   * `Folder` 取自 **`lucide-react`**（本文件第 13 行那个 import 里已经有 `MoreHorizontal`
   * 与 `Plus`，同一来源），**不是** `@plane/propel/icons` —— 那边只有
   * `FavoriteFolderIcon`（收藏夹形状），没有中性文件夹，语义不对。
   * 与 `PageIcon`（propel）混用是**有意**的：本文件本来就在混（`MoreHorizontal` 来自
   * lucide，`PageIcon` / `ChevronRightIcon` 来自 propel）。不要为「统一来源」去动任一边。
   *
   * **默认收起** —— `expandedPageIds` 空 ⇒ 只露顶层页面（用户 2026-10-01）。
   * 进到深层页时由上面那个 `useEffect` 自动补开祖先链（Round D 起锚点是
   * `activePageId ?? explicitFolder`，文件夹同理）。
   *
   * 层级**只**由行左的缩进格数表达：`PAGE_BASE_LEVEL + 层深`（封顶见 `TREE_DEPTH_CAP`）。
   * 收起的父行下不渲染子行，那一支的缩进也随之消失 —— 与树的结构一致。
   *
   * 标题用 `getPageName`，与主列表（`pages/list/block.tsx:63`）**同一处**取名字 ——
   * 空名页两边都渲染成硬编码英文 "Untitled"。这是「发现 ② 延后」的直接后果：
   * 修它要动共享的 `getPageName`（纯 util 包拿不到 `t()`），波及全站 Page 列表。
   * 这里**不**改用 `wiki_collections.list.untitled` —— 那会让侧栏与列表对同一个页面
   * 显示两个不同的名字，比英文更糟。**文件夹同样走 `getPageName`**，理由逐字相同。
   *
   * `partitionKey` 仍要收：它决定这一行渲不渲染那个 `＋`（**这是设计文档 Round C 的
   * 裁定 5，不是本计划执行期裁定里的第 5 条** —— 后者讲的是 `?folder=` 含子文件夹。
   * Round D 起那个 `＋` 是一个两项下拉，见 `renderCreateMenu`）。
   */
  const renderPageRow = (line: TWikiTreeLine, partitionKey: string) => {
    const page = getPageById(line.pageId);
    if (!page || isLineHiddenByExpansion(line, expandedSet)) return null;
    /**
     * 这一行是文件夹还是页面（罗盘 Round D）。类型来自 store 的旁挂索引
     * （`getPageNodeType`），由侧栏那条 `fetchWikiTree`（`?scope=all`）灌进来 ——
     * 与 `parent` 走同一条路，**不进 `BasePage`**。
     *
     * **取不到就当页面**（首帧会出现 `undefined`）：那正是今天的既有行为，不是新错；
     * 反过来默认成文件夹会让页面行先渲染成文件夹再跳回来。
     */
    const isFolder = getPageNodeType(line.pageId) === PAGE_NODE_TYPE_FOLDER;
    // 静止态那格用哪个图标 —— 折叠箭头那一支也要用，所以先取出来。
    // 两个组件都只吃 `className`、形状同构，可以直接二选一。
    const RowIcon = isFolder ? Folder : PageIcon;
    // 有子项 ⇒ 左边长箭头；没有 ⇒ 只占一格空位。判据按分区取（`linesByPartition`），
    // 与渲染用的是同一份数据 —— 收起的行也在里面，所以收起后这个判断依然成立。
    const hasChildren = (linesByPartition[partitionKey] ?? []).some((candidate) =>
      candidate.ancestorIds.includes(line.pageId)
    );
    const isExpanded = expandedSet.has(line.pageId);
    /**
     * 选中态。**文件夹与页面判的不是同一件事** —— 因为两者的标题落点不同：
     * 页面看 `activePageId`（路径段），文件夹看 `explicitFolder`（`?folder=`）。
     * 与集合行的 `explicitCollection === key` 同源。
     */
    const isActive = isFolder ? explicitFolder === line.pageId : line.pageId === activePageId;

    return (
      <div
        key={line.pageId}
        className={cn(
          TREE_ROW_BASE_CLASS,
          "py-1",
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
          //
          // 底色 2026-10-02 从标题按钮**搬到了容器**上 —— 箭头那一格在按钮之外，
          // 留按钮上的话鼠标划到箭头那里整行只亮半截。
          isActive ? "bg-layer-transparent-selected text-primary" : "text-secondary hover:bg-layer-transparent-hover"
        )}
      >
        {/* 这一行的层级就是缩进格数。封顶：侧栏本来就窄，无限缩进会把标题挤没。
            整块交给 `renderLeadCell`（缩进与图标格紧挨）。 */}
        {renderLeadCell(
          PAGE_BASE_LEVEL + Math.min(line.depth, TREE_DEPTH_CAP),
          hasChildren ? (
            <button
              type="button"
              onClick={() => toggleExpanded(line.pageId)}
              aria-expanded={isExpanded}
              // 与分组标题那颗箭头同一种写法：`aria-label` 就用这一行的名字。
              // 叶子行走下面的分支，**不**报 `aria-expanded`（那会永远是 false）。
              aria-label={getPageName(page.name)}
              className={cn(TREE_LEAD_SLOT_CLASS, "rounded-sm text-tertiary hover:text-secondary")}
            >
              {/* 颜色同上：两个图标都不写，继承按钮那格的 `text-tertiary` /
                  悬停 `text-secondary`。 */}
              <RowIcon className="col-start-1 row-start-1 h-3.5 w-3.5 transition-opacity group-focus-within:opacity-0 group-hover:opacity-0" />
              <ChevronRightIcon
                className={cn(
                  "col-start-1 row-start-1 size-3 opacity-0 transition group-focus-within:opacity-100 group-hover:opacity-100",
                  { "rotate-90": isExpanded }
                )}
              />
            </button>
          ) : (
            <span className={TREE_LEAD_SLOT_CLASS}>
              <RowIcon className="h-3.5 w-3.5 text-tertiary" />
            </span>
          )
        )}
        <button
          type="button"
          onClick={() => (isFolder ? goToFolder(line.pageId) : goToPage(line.pageId))}
          className="flex min-w-0 flex-1 items-center gap-2 text-left text-13"
        >
          <span className="truncate">{getPageName(page.name)}</span>
        </button>
        {/* 入口：**只在 ADMIN/MEMBER、且本行不在归档分区时**渲染。hover 才出现，
            平时不占视觉重量。落点是 `{ parent: 这一行 }` —— **页面行与文件夹行
            逐字相同**（在文件夹里建页面/子文件夹都靠它）。 */}
        {canCreateIn(partitionKey) && renderCreateMenu({ parent: line.pageId })}
        {/* 文件夹行的 `•••`（Round E）。页面行**不加** —— 侧栏的页面行今天就没有 `⋯`
            （它的两个动作在列表视图的行菜单上），本轮不扩大侧栏的动作面。

            权限判据是 `canManageCollections`（:397），**不是**上面那颗 `＋` 的
            `canCreateIn(partitionKey)`（:421）。两者只差 `partitionKey !== "archived"` 那半句，
            而那半句的理由是「新建的页面不可能是归档的」（:403-405）—— 只对**新建**成立。
            改名 / 移动 / 删除一个**已归档**的文件夹是正当操作，后端也放行（partial_update /
            destroy 只有 WORKSPACE 级 ADMIN/MEMBER，没有归档检查），所以这里不跟着排除。
            列表视图的同一个组件用的正是不含排除的谓词（`wiki-list-root.tsx` 的 `canWriteWiki`）——
            同一个对象在两处能做的动作必须一样。

            **悬停才浮出**（与**同一行**那颗 `＋` 一致，`renderCreateMenu` 的
            `className="opacity-0 group-hover:opacity-100"`）：`group` 在
            `TREE_ROW_BASE_CLASS`（:143）里，所以这一格直接可用。
            只差键盘那一路：这一格还带 `group-focus-within:opacity-100`，整行获得焦点即浮出
            （`＋` 没有这一条）。菜单里有可聚焦的子项，少了它键盘用户就永远打不开这个 `•••`。
            注意与**集合行**那颗 `•••`（:1011，`variant="ghost" size="sm"` 无 reveal）
            并不一致 —— 那是**刻意**的：集合行是分区标题、常驻显示；树行里的动作格
            要跟同一行的 `＋` 对齐，否则窄侧栏里每个文件夹行都会常亮一颗 `•••`。

            `onChanged` 传空函数是**有意的**：侧栏这棵树来自 `fetchWikiTree`，而
            `deleteFolder` / `moveTo` 内部**已经**调了它（见 store），树会自己刷新 ——
            这里再补一次请求就是第二次取数。

            **但它只管这棵树，不管右边的列表视图**：列表读的是 `collectionPageIds`，而
            store 的 `moveTo` 只把被移动的 id 从**所有**集合键里剔掉、不往目标键里补。所以
            从侧栏「移动」之后，正显示**目标集合**的列表要等它自己重拉才会出现那一行
            （移出方向没问题，本地过滤就把行去掉了）。列表视图自己的 `•••` 没这个问题 ——
            它把 `refreshList` 传了进去。要在侧栏这处修得让 store 广播一次刷新，是接口改动，
            **不在本轮**；先如实记在这里，别当成已解决。 */}
        {isFolder && canManageCollections && (
          <span className="opacity-0 group-focus-within:opacity-100 group-hover:opacity-100">
            <FolderRowActions folderId={line.pageId} onChanged={() => undefined} />
          </span>
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
          - `!explicitFolder` —— 同理：文件夹行的标题是**导航到它自己的列表视图**
            （`?folder=<id>`，见 `renderPageRow`），所以那个状态下亮的是文件夹行，
            「首页」必须一起让位。
        */}
        <Link href={`/${workspaceSlug}/wiki/`}>
          <SidebarNavItem isActive={!activePageId && !explicitCollection && !explicitFolder}>
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
                  就是靠这个相邻关系表达的，不是靠缩进。
                  树由**集合行自己那格箭头**收放（`expandedPartitionKeys`，**默认收起**），
                  与外面「集合」组标题的折叠是两层、互不干涉。 */}
              {renderRow("general", t("wiki_collections.predefined.general"), predefinedCount("general"))}
              {expandedPartitionKeys.includes("general") && renderTree("general")}
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
                        <CustomMenu.MenuItem onClick={() => setDeletingCollection(collection)}>
                          {t("wiki_collections.menu.delete_collection")}
                        </CustomMenu.MenuItem>
                      </CustomMenu>
                    ) : undefined
                  )}
                  {expandedPartitionKeys.includes(collection.id) && renderTree(collection.id)}
                </Fragment>
              ))}
            </>
          )}
        </div>

        {/* 标题，不是行 —— 见 `PARTITION_ROWS` 的注释（同级关系的由来与代价记账都在那）。
            这一组的折叠**仍然挂在标题自己身上**（`collapsedGroupKeys`）：它下面没有
            集合行，没有第二个能收放这棵树的地方，所以不必（也不该）为它再开一套
            `expandedPartitionKeys` —— 那会变成两个开关管同一棵树。 */}
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

      <DeleteCollectionModal
        isOpen={deletingCollection !== null}
        collectionId={deletingCollection?.id ?? null}
        onDeleted={handleCollectionDeleted}
        handleClose={() => setDeletingCollection(null)}
      />

      <PageFormModal
        isOpen={isPageFormOpen}
        handleClose={closePageForm}
        target={pageCreateTarget ?? containerTarget(activeCollection)}
      />
    </SidebarWrapper>
  );
});
