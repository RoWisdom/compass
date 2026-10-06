/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { unset, set } from "lodash-es";
import { makeObservable, observable, runInAction, action, reaction, computed } from "mobx";
import { computedFn } from "mobx-utils";
// types
import { EPageAccess, EUserPermissions } from "@plane/constants";
import type { TPage, TPageFilters, TPageNavigationTabs } from "@plane/types";
import { EUserProjectRoles } from "@plane/types";
// helpers
import { filterPagesByPageType, getPageName, orderPages, shouldFilterPage } from "@plane/utils";
// services
import type { TProjectPageWithParent } from "@/services/page";
import { PAGE_NODE_TYPE_DOC, PAGE_NODE_TYPE_FOLDER, ProjectPageService } from "@/services/page";
import type { TPageNodeType } from "@/services/page";
// store
import type { CoreRootStore } from "../root.store";
import type { TProjectPage } from "./project-page";
import { ProjectPage } from "./project-page";

type TLoader = "init-loader" | "mutation-loader" | undefined;

type TError = { title: string; description: string };

/**
 * 按 `pageParentIds` 算出一棵子树的全部 id —— **含根自己**。
 *
 * 为什么不能靠「按项目过滤」搞定：`data` 里装着这个项目的**所有**节点，
 * 删一个文件夹时它的兄弟与父级都在同一个项目里，按项目过滤会把它们一起误删。
 * 父子关系只有 `pageParentIds` 有。
 */
const collectSubtreeIds = (pageParentIds: Record<string, string | null> | undefined, rootId: string): string[] => {
  const childrenByParent = new Map<string, string[]>();
  for (const [id, parentId] of Object.entries(pageParentIds ?? {})) {
    if (!parentId || id === rootId) continue;
    const siblings = childrenByParent.get(parentId);
    if (siblings) siblings.push(id);
    else childrenByParent.set(parentId, [id]);
  }

  const subtree = [rootId];
  const seen = new Set(subtree);
  // 边遍历边增长 —— 广度优先，`subtree` 自己就是队列。
  for (let index = 0; index < subtree.length; index++) {
    for (const childId of childrenByParent.get(subtree[index]) ?? []) {
      if (seen.has(childId)) continue;
      seen.add(childId);
      subtree.push(childId);
    }
  }
  return subtree;
};

export const ROLE_PERMISSIONS_TO_CREATE_PAGE = [
  EUserPermissions.ADMIN,
  EUserPermissions.MEMBER,
  EUserProjectRoles.ADMIN,
  EUserProjectRoles.MEMBER,
];

export interface IProjectPageStore {
  // observables
  loader: TLoader;
  data: Record<string, TProjectPage>; // pageId => Page
  /** pageId → 父节点 id。**旁挂索引**，不进 `TPage`/`BasePage`（理由见 service 层的
   *  `TProjectPageWithParent`）。`null` = 顶层；**取不到按顶层处理**（首帧、树还没回来时
   *  那正是今天的既有行为，不是新错）。 */
  pageParentIds: Record<string, string | null>;
  /** pageId → 节点类型。同一条设计：只在这条线上被读一次，不进页面模型。
   *  **只有 `?scope=all` 这条响应带它**，所以只有 `syncTreeRows` 允许写这个索引。 */
  pageNodeTypes: Record<string, TPageNodeType>;
  error: TError | undefined;
  filters: TPageFilters;
  // computed
  isAnyPageAvailable: boolean;
  canCurrentUserCreatePage: boolean;
  // helper actions
  getCurrentProjectPageIdsByTab: (pageType: TPageNavigationTabs) => string[] | undefined;
  getCurrentProjectPageIds: (projectId: string) => string[];
  getCurrentProjectFilteredPageIdsByTab: (pageType: TPageNavigationTabs) => string[] | undefined;
  getPageById: (pageId: string) => TProjectPage | undefined;
  getPageNodeType: (pageId: string) => TPageNodeType | undefined;
  updateFilters: <T extends keyof TPageFilters>(filterKey: T, filterValue: TPageFilters[T]) => void;
  clearAllFilters: () => void;
  // actions
  fetchPagesList: (
    workspaceSlug: string,
    projectId: string,
    pageType?: TPageNavigationTabs
  ) => Promise<TProjectPageWithParent[] | undefined>;
  fetchPageDetails: (
    workspaceSlug: string,
    projectId: string,
    pageId: string,
    options?: { trackVisit?: boolean }
  ) => Promise<TPage | undefined>;
  createPage: (pageData: Partial<TPage>) => Promise<TPage | undefined>;
  removePage: (params: { pageId: string; shouldSync?: boolean }) => Promise<void>;
  fetchPagesTree: (workspaceSlug: string, projectId: string) => Promise<TProjectPageWithParent[] | undefined>;
  createFolder: (
    workspaceSlug: string,
    projectId: string,
    name: string,
    parentId: string | null,
    access: EPageAccess
  ) => Promise<TPage | undefined>;
  renameFolder: (workspaceSlug: string, folderId: string, name: string) => Promise<void>;
  deleteFolder: (workspaceSlug: string, folderId: string) => Promise<void>;
  moveTo: (
    workspaceSlug: string,
    projectId: string,
    pageId: string,
    target: { parentId: string | null }
  ) => Promise<void>;
}

export class ProjectPageStore implements IProjectPageStore {
  // observables
  loader: TLoader = "init-loader";
  data: Record<string, TProjectPage> = {}; // pageId => Page
  pageParentIds: Record<string, string | null> = {};
  pageNodeTypes: Record<string, TPageNodeType> = {};
  error: TError | undefined = undefined;
  filters: TPageFilters = {
    searchQuery: "",
    sortKey: "updated_at",
    sortBy: "desc",
  };
  // service
  service: ProjectPageService;
  rootStore: CoreRootStore;

  constructor(private store: CoreRootStore) {
    makeObservable(this, {
      // observables
      loader: observable.ref,
      data: observable,
      pageParentIds: observable,
      pageNodeTypes: observable,
      error: observable,
      filters: observable,
      // computed
      isAnyPageAvailable: computed,
      canCurrentUserCreatePage: computed,
      // helper actions
      updateFilters: action,
      clearAllFilters: action,
      // actions
      fetchPagesList: action,
      fetchPageDetails: action,
      createPage: action,
      removePage: action,
      fetchPagesTree: action,
      createFolder: action,
      renameFolder: action,
      deleteFolder: action,
      moveTo: action,
    });
    this.rootStore = store;
    // service
    this.service = new ProjectPageService();
    // initialize display filters of the current project
    reaction(
      () => this.store.router.projectId,
      (projectId) => {
        if (!projectId) return;
        this.filters.searchQuery = "";
      }
    );
  }

  /**
   * @description check if any page is available
   */
  get isAnyPageAvailable() {
    if (this.loader) return true;
    return Object.keys(this.data).length > 0;
  }

  /**
   * @description returns true if the current logged in user can create a page
   */
  get canCurrentUserCreatePage() {
    const { workspaceSlug, projectId } = this.store.router;
    const currentUserProjectRole = this.store.user.permission.getProjectRoleByWorkspaceSlugAndProjectId(
      workspaceSlug?.toString() || "",
      projectId?.toString() || ""
    );
    return !!currentUserProjectRole && ROLE_PERMISSIONS_TO_CREATE_PAGE.includes(currentUserProjectRole);
  }

  /**
   * @description get the current project page ids based on the pageType
   * @param {TPageNavigationTabs} pageType
   */
  getCurrentProjectPageIdsByTab = computedFn((pageType: TPageNavigationTabs) => {
    const { projectId } = this.store.router;
    if (!projectId) return undefined;
    // helps to filter pages based on the pageType
    let pagesByType = filterPagesByPageType(pageType, Object.values(this?.data || {}));
    pagesByType = pagesByType.filter((p) => p.project_ids?.includes(projectId));

    const pages = (pagesByType.map((page) => page.id) as string[]) || undefined;

    return pages ?? undefined;
  });

  /**
   * 详情页顶栏那颗**平铺** switcher 的候选（`(detail)/header.tsx:44`）。
   *
   * 本轮加一条**只排除文件夹**的过滤：`data` 从这一刻起装着**整棵树**（`?scope=all`），
   * 文件夹也在里面 —— 列进来会让用户点进一个「文件夹的编辑器」。
   *
   * 页面**不论多深都留着**：那个 switcher 至今是平铺的（设计 §9 本轮不动它），
   * 语义是「跳到本项目任意一页」。收窄到只留根会让用户停在子页时，那一行在
   * `selectedItem` 匹配不到任何 option（`header.tsx:48` 只对**当前页**做了兜底）。
   */
  getCurrentProjectPageIds = computedFn((projectId: string) => {
    if (!projectId) return [];
    const pages = Object.values(this?.data || {}).filter(
      (page) =>
        page.project_ids?.includes(projectId) && page.id && this.pageNodeTypes[page.id] !== PAGE_NODE_TYPE_FOLDER
    );
    return pages.map((page) => page.id) as string[];
  });

  /**
   * @description get the current project filtered page ids based on the pageType
   * @param {TPageNavigationTabs} pageType
   */
  getCurrentProjectFilteredPageIdsByTab = computedFn((pageType: TPageNavigationTabs) => {
    const { projectId } = this.store.router;
    if (!projectId) return undefined;

    // helps to filter pages based on the pageType
    const pagesByType = filterPagesByPageType(pageType, Object.values(this?.data || {}));
    let filteredPages = pagesByType.filter(
      (p) =>
        p.project_ids?.includes(projectId) &&
        getPageName(p.name).toLowerCase().includes(this.filters.searchQuery.toLowerCase()) &&
        shouldFilterPage(p, this.filters.filters)
    );
    filteredPages = orderPages(filteredPages, this.filters.sortKey, this.filters.sortBy);

    const pages = (filteredPages.map((page) => page.id) as string[]) || undefined;

    return pages ?? undefined;
  });

  /**
   * @description get the page store by id
   * @param {string} pageId
   */
  getPageById = computedFn((pageId: string) => this.data?.[pageId] || undefined);

  /**
   * 某一行的类型。`undefined` = **还没取到**（首帧、树还没回来）——
   * 调用方要按「当作页面」处理（那正是今天的既有行为，不是新错）。
   */
  getPageNodeType = computedFn((pageId: string) => this.pageNodeTypes?.[pageId] || undefined);

  updateFilters = <T extends keyof TPageFilters>(filterKey: T, filterValue: TPageFilters[T]) => {
    runInAction(() => {
      set(this.filters, [filterKey], filterValue);
    });
  };

  /**
   * @description clear all the filters
   */
  clearAllFilters = () =>
    runInAction(() => {
      set(this.filters, ["filters"], {});
    });

  /**
   * 把一次 `?scope=all` 的行写进 `data` 与两个旁挂索引。
   *
   * **不动 `loader`** —— 两个调用方各自决定要不要打骨架：`fetchPagesList` 是视图进入时的
   * 取数（`loader` 驱动主面板骨架），`fetchPagesTree` 是动作落库后的重拉（不该把整个面板
   * 打回骨架）。与 wiki 侧「`fetchWikiTree` 不设 loader」同一条惯例。
   */
  private syncTreeRows = (rows: TProjectPageWithParent[]) => {
    runInAction(() => {
      for (const row of rows) {
        if (!row?.id) continue;
        const existingPage = this.getPageById(row.id);
        if (existingPage) {
          // `parent` 与 `node_type` 是**本端点独有**的字段，不能进 `mutateProperties` ——
          // 那是个盲写的 `set(this, key, value)`（`base-page.ts`），会把没人认识的属性
          // 写到页面实例上。它们只活在两个旁挂索引里。
          // `name` 一并摘掉：本仓的既定约定是「重拉不该冲掉用户正在输入的字」
          // （`base-page.ts` 的 `shouldUpdateName` 守卫），与 wiki 侧逐字同款。
          const { name, parent, node_type, ...otherFields } = row;
          existingPage.mutateProperties(otherFields, false);
        } else {
          set(this.data, [row.id], new ProjectPage(this.store, { ...row }));
        }
        set(this.pageParentIds, [row.id], row.parent ?? null);
        // 缺省当 `doc`：这条响应**总是**带 `node_type`（后端默认值是 `"doc"`），
        // 但一个 `undefined` 会让 `getPageNodeType` 把「还没取到」与「真的是页面」
        // 混在同一档 —— 这里落成常量。
        set(this.pageNodeTypes, [row.id], row.node_type ?? PAGE_NODE_TYPE_DOC);
      }
    });
  };

  /**
   * @description 拉当前项目的**整棵树**（视图进入时的取数，`loader` 驱动骨架）
   */
  fetchPagesList = async (workspaceSlug: string, projectId: string, pageType?: TPageNavigationTabs) => {
    try {
      if (!workspaceSlug || !projectId) return undefined;

      const currentPageIds = pageType ? this.getCurrentProjectPageIdsByTab(pageType) : undefined;
      runInAction(() => {
        this.loader = currentPageIds && currentPageIds.length > 0 ? `mutation-loader` : `init-loader`;
        this.error = undefined;
      });

      const pages = await this.service.fetchAll(workspaceSlug, projectId);
      this.syncTreeRows(pages);
      runInAction(() => {
        this.loader = undefined;
      });

      return pages;
    } catch (error) {
      runInAction(() => {
        this.loader = undefined;
        this.error = {
          title: "Failed",
          description: "Failed to fetch the pages, Please try again later.",
        };
      });
      throw error;
    }
  };

  /**
   * 重拉整棵树 —— **不动 `loader`**。动作（建/改/删/移）落库后由调用方触发。
   *
   * 取数口径与 `fetchPagesList` **完全相同**（同一个端点、同一个 `syncTreeRows`），
   * 差别只有「要不要把主面板打回骨架」这一点。
   *
   * **不吞异常、也不设 `error`**：调用方在动作成功之后才调它，让刷新失败冒泡成
   * 「动作失败」是撒谎。与 wiki 侧的纪律同源 —— 那边是在调用点 `.catch(() => {})`。
   */
  fetchPagesTree = async (workspaceSlug: string, projectId: string) => {
    if (!workspaceSlug || !projectId) return undefined;
    const pages = await this.service.fetchAll(workspaceSlug, projectId);
    this.syncTreeRows(pages);
    return pages;
  };

  /**
   * @description fetch the details of a page
   * @param {string} pageId
   */
  fetchPageDetails = async (...args: Parameters<IProjectPageStore["fetchPageDetails"]>) => {
    const [workspaceSlug, projectId, pageId, options] = args;
    const { trackVisit } = options || {};
    try {
      if (!workspaceSlug || !projectId || !pageId) return undefined;

      const currentPageId = this.getPageById(pageId);
      runInAction(() => {
        this.loader = currentPageId ? `mutation-loader` : `init-loader`;
        this.error = undefined;
      });

      const page = await this.service.fetchById(workspaceSlug, projectId, pageId, trackVisit ?? true);

      runInAction(() => {
        if (page?.id) {
          const pageInstance = this.getPageById(page.id);
          if (pageInstance) {
            pageInstance.mutateProperties(page, false);
          } else {
            set(this.data, [page.id], new ProjectPage(this.store, page));
          }
        }
        this.loader = undefined;
      });

      return page;
    } catch (error) {
      runInAction(() => {
        this.loader = undefined;
        this.error = {
          title: "Failed",
          description: "Failed to fetch the page, Please try again later.",
        };
      });
      throw error;
    }
  };

  /**
   * @description create a page
   * @param {Partial<TPage>} pageData
   */
  createPage = async (pageData: Partial<TPage>) => {
    try {
      const { workspaceSlug, projectId } = this.store.router;
      if (!workspaceSlug || !projectId) return undefined;

      runInAction(() => {
        this.loader = "mutation-loader";
        this.error = undefined;
      });

      const page = await this.service.create(workspaceSlug, projectId, pageData);
      runInAction(() => {
        if (page?.id) set(this.data, [page.id], new ProjectPage(this.store, page));
        this.loader = undefined;
      });

      // 重拉整棵树（移出上面的 try）—— 让刷新失败冒泡成「创建失败」是撒谎。
      // 这一步不只是让新行出现在列表里：它还是**旁挂索引**的唯一来源
      // （`create` 的响应是 `PageDetailSerializer`，里面**没有** `node_type`）。
      await this.fetchPagesTree(workspaceSlug, projectId).catch(() => {});

      return page;
    } catch (error) {
      runInAction(() => {
        this.loader = undefined;
        this.error = {
          title: "Failed",
          description: "Failed to create a page, Please try again later.",
        };
      });
      throw error;
    }
  };

  /**
   * 在项目里新建一个**文件夹**（罗盘 Round J）。
   *
   * 与 `createPage` 的唯一差别是 `node_type`：后端据此跳过 vault 镜像与
   * `page_transaction`（文件夹没有正文，落盘会在用户的真实笔记库里凭空生出一个
   * `<文件夹名>.md`）。**必须显式发这个字段** —— 少了它，后端默认值是 `"doc"`，
   * 会安静地建出一个普通页面。
   *
   * 不设 `loader`：`loader` 驱动的是整个主面板的加载骨架，而建文件夹只是往树里多插
   * 一行。与 wiki 侧 `createPage` 同一条理由（调用方自己有 submitting 态）。
   *
   * **`access` 必须由调用方给**（罗盘 Round J 修复）：不传的话后端用模型默认值
   * （Public），而在 `?type=private` 分页下 `filterPagesByPageType("private")`
   * 只留 `access === 1`，新建的文件夹根本不显示 —— 用户以为「点了没反应」，
   * 空态 CTA 还在，连点会造出多个同名文件夹。取值照
   * `use-project-page-create.ts` 的同一处映射（`pageType === "private"` ⇒ PRIVATE）。
   *
   * 返回值**透传 service 的新节点** —— 调用方可能要拿它的 id。
   */
  createFolder = async (
    workspaceSlug: string,
    projectId: string,
    name: string,
    parentId: string | null,
    access: EPageAccess
  ) => {
    let page: TPage;
    try {
      const payload: Partial<TPage> & { node_type?: TPageNodeType; parent?: string } = {
        name,
        node_type: PAGE_NODE_TYPE_FOLDER,
        access,
      };
      // 展开而不是赋 `undefined`：语义写在纸面上，不依赖 `JSON.stringify` 丢掉
      // `undefined` 这个隐含行为（与 wiki 的 `page-form-modal.tsx` 同款）。
      if (parentId) payload.parent = parentId;

      page = await this.service.create(workspaceSlug, projectId, payload);
    } catch (error) {
      runInAction(() => {
        this.error = { title: "Failed", description: "Failed to create the folder, Please try again later." };
      });
      throw error;
    }

    await this.fetchPagesTree(workspaceSlug, projectId).catch(() => {});

    return page;
  };

  /**
   * 给文件夹改名。**走的是与「改页面标题」同一个 PATCH 端点** —— 后端对文件夹只拦
   * 正文三个键，`name` 是放行的。
   *
   * **签名与 `WorkspacePageStore.renameFolder` 逐字相同**（`(workspaceSlug, folderId, name)`）：
   * `rename-folder-modal.tsx` 被参数化成泛型 store 调用，两个 store 签名不一致的话
   * 联合类型不成立（设计 §4.8.1）。
   *
   * 三点纪律照 wiki 侧：不设 `loader`；**不得吞掉异常**（弹窗靠「action 是否 reject」
   * 决定 toast 成败）；**重拉不在写入的 try 里**（写入已落库，刷新失败不该报成「改名失败」）。
   */
  renameFolder = async (workspaceSlug: string, folderId: string, name: string) => {
    const { projectId } = this.store.router;
    try {
      if (!workspaceSlug || !projectId) return;

      await this.service.update(workspaceSlug, projectId, folderId, { name });

      // **必须自己把名字写回本地实例**：这条路径上没有任何东西会替我们写 ——
      // `syncTreeRows` 的 `mutateProperties(otherFields, false)` 刻意不更新 `name`
      // （见那里的注释），而列表行读的正是这个实例。
      const page = this.getPageById(folderId);
      if (page && page.name !== name) {
        page.updateTitle(name);
        // `updateTitle` 把 `oldName` 记成**旧**名，而 `BasePage` 的标题 reaction
        // （2s 防抖）稍后会拿新名再打一次 PATCH；万一那次冗余请求失败，它会把名字
        // 回滚成 `oldName`。服务端此刻已经是新名，所以回滚目标必须是新名。
        page.oldName = name;
      }
    } catch (error) {
      runInAction(() => {
        this.error = { title: "Failed", description: "Failed to rename the folder, Please try again later." };
      });
      throw error;
    }

    await this.fetchPagesTree(workspaceSlug, projectId).catch(() => {});
  };

  /**
   * 删一个文件夹。**Round J 起它删的是一整棵子树**（裁定 D2）：文件夹自己、里面的页面、
   * 嵌套的子文件夹，后端全部软删。
   *
   * 所以本地不能只摘 `folderId` —— 那样会留下一堆指向已经不存在的行的 id。
   * 后代的来源是 `pageParentIds`（那棵树那份索引）。
   *
   * **签名与 `WorkspacePageStore.deleteFolder` 逐字相同**（`(workspaceSlug, folderId)`），
   * 理由同 `renameFolder`。
   */
  deleteFolder = async (workspaceSlug: string, folderId: string) => {
    const { projectId } = this.store.router;
    try {
      if (!workspaceSlug || !projectId) return;

      await this.service.remove(workspaceSlug, projectId, folderId);

      runInAction(() => {
        // 先算后代再动手 —— 索引一旦被下面这次重拉改写就晚了。
        const doomed = collectSubtreeIds(this.pageParentIds, folderId);
        // ⚠️ **必须逐个 id 调 `unset(o, [id])`，不能写成 `unset(o, doomed)`。**
        // lodash 的 `unset` 把数组当**深层路径**解：`unset(o, ['a','b'])` 删的是 `o.a.b`，
        // 于是多元素时整段清账**静默空转**（实测：对象一字不变）。这是 wiki 侧
        // `deleteFolder` 记下的同一个坑（Round H/T6 的翻版）。
        for (const id of doomed) {
          unset(this.data, [id]);
          unset(this.pageParentIds, [id]);
          unset(this.pageNodeTypes, [id]);
        }
      });
    } catch (error) {
      runInAction(() => {
        this.error = { title: "Failed", description: "Failed to delete the folder, Please try again later." };
      });
      throw error;
    }

    await this.fetchPagesTree(workspaceSlug, projectId).catch(() => {});
  };

  /**
   * 把一个节点（页面或文件夹）移到新的父节点下。`target.parentId === null` = 移到顶层。
   *
   * 与 wiki 的 `moveTo` 的关系：那个的 target 是 `{ collectionId, parentId }`，因为
   * wiki 有「集合」这一层；项目侧**没有集合**（`PageCollection` 是 workspace-only 的表），
   * 所以这里只有 `parentId`。两者**不共用**，也不该硬凑成同一个签名。
   */
  moveTo = async (workspaceSlug: string, projectId: string, pageId: string, target: { parentId: string | null }) => {
    try {
      // **不**顺手改本地索引，也不 `mutateProperties({ parent })`：
      //   · `parent` 不是 `BasePage` 的属性（它只活在 `pageParentIds`），
      //     盲写进去与 `syncTreeRows` 的既定纪律（见那里「`parent` 与 `node_type`
      //     是**本端点独有**的字段，不能进 `mutateProperties`」）自相矛盾，
      //     而且为了骗过类型要加一个 `as Partial<TPage>` —— 一个骗编译器的断言。
      //   · 移一个节点会改动**整棵子树**的父链，本地推演一遍本就是错的省事做法。
      // 下面那次 `fetchPagesTree` 是唯一的事实来源，它填 `pageParentIds` /
      // `pageNodeTypes` 并顺带刷新 `data`。
      const payload: Partial<TPage> & { parent?: string | null } = { parent: target.parentId };
      await this.service.update(workspaceSlug, projectId, pageId, payload);
    } catch (error) {
      runInAction(() => {
        this.error = { title: "Failed", description: "Failed to move the page, Please try again later." };
      });
      throw error;
    }

    // 整棵树都可能变了（子树的父链跟着走），所以重拉树，而不只是改本地索引。
    await this.fetchPagesTree(workspaceSlug, projectId).catch(() => {});
  };

  /**
   * @description delete a page
   * @param {string} pageId
   */
  removePage = async ({ pageId, shouldSync: _shouldSync = true }: { pageId: string; shouldSync?: boolean }) => {
    try {
      const { workspaceSlug, projectId } = this.store.router;
      if (!workspaceSlug || !projectId || !pageId) return undefined;

      await this.service.remove(workspaceSlug, projectId, pageId);
      runInAction(() => {
        unset(this.data, [pageId]);
        // **旁挂索引也要一并摘**（罗盘 Round J 修复）：删一行只摘 `data` 的话，
        // 它的 id 仍留在 `pageParentIds` / `pageNodeTypes` 里 —— `ProjectMoveToModal`
        // 的 `allIds` 正是 `Object.keys(pageParentIds)`，于是目标列表里会多出一行
        // **空名字、可点选**的幽灵目标（点了后端 400）。与 `deleteFolder` 同一条纪律。
        // 同样逐个 `unset(o, [id])`：lodash 把数组当深层路径解，不能写成 `unset(o, ids)`。
        unset(this.pageParentIds, [pageId]);
        unset(this.pageNodeTypes, [pageId]);
        if (this.rootStore.favorite.entityMap[pageId]) this.rootStore.favorite.removeFavoriteFromStore(pageId);
      });
    } catch (error) {
      runInAction(() => {
        this.loader = undefined;
        this.error = {
          title: "Failed",
          description: "Failed to delete a page, Please try again later.",
        };
      });
      throw error;
    }
  };
}
