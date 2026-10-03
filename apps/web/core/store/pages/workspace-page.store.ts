/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { set, unset } from "lodash-es";
import { action, computed, makeObservable, observable, runInAction } from "mobx";
import { computedFn } from "mobx-utils";
// types
import type { TPage, TPageFilters } from "@plane/types";
// services
import type {
  TCollectionFilter,
  TPageCollection,
  TPageCollectionListResponse,
  TPageCreatePayload,
  TPageIncludeResponse,
  TPageNodeType,
  TPageWithParent,
  TPredefinedCollection,
  TWikiScopedPage,
} from "@/services/page";
import { PAGE_NODE_TYPE_DOC, WorkspacePageService } from "@/services/page";
// store
import type { CoreRootStore } from "../root.store";
import type { TWorkspacePage } from "./workspace-page";
import { WorkspacePage } from "./workspace-page";

type TLoader = "init-loader" | "mutation-loader" | undefined;

type TError = { title: string; description: string };

/**
 * 侧栏建树的一行。`treeRows` 的**顺序就是响应顺序**，也就是渲染顺序。
 *
 * 只存 id 与分区键，不存整个页面 —— 页面数据在 `data` 里（`getPageById` 取），
 * 存两份必然漂移。
 */
export type TWikiTreeRow = { pageId: string; collectionKey: string };

export interface IWorkspacePageStore {
  // observables
  loader: TLoader;
  data: Record<string, TWorkspacePage>; // pageId => Page
  collectionPageIds: Record<string, string[]>; // 分区 key 或集合 uuid => pageIds
  collections: TPageCollection[];
  predefined: TPredefinedCollection[];
  candidates: TPage[];
  /** `?scope=all` 的**有序**行 —— 侧栏按它建树（设计 B-5/B-6）。 */
  treeRows: TWikiTreeRow[];
  /** pageId → 父页 id。**旁挂索引**，不进 `TPage`/`BasePage`（理由见 service 层的 `TPageWithParent`）。 */
  pageParentIds: Record<string, string | null>;
  /**
   * 节点的类型判别符，按 pageId 旁挂（罗盘 Round D）。
   *
   * **与 `pageParentIds` 同一条设计**：`BasePage` 是逐字段显式赋值构造的、
   * 且被**项目页**共用，wiki 的类型没有理由去动它，所以类型也只在这条线上被读一次
   * —— 读**原始响应**，随即落进这个索引，**不进页面模型**。
   *
   * **只有两条响应带这个字段**：`?scope=all`（树）与详情。`fetchPagesList` /
   * `fetchFolderPages` 的响应里**没有**它，所以那两个 action **不许**写这个索引 ——
   * 写就是把树刚灌进来的 `"folder"` 冲成默认的 `"doc"`（执行期裁定 7）。
   */
  pageNodeTypes: Record<string, TPageNodeType>;
  error: TError | undefined;
  filters: TPageFilters;
  // computed
  isAnyPageAvailable: boolean;
  // helper actions
  getPageById: (pageId: string) => TWorkspacePage | undefined;
  getPageIdsByCollection: (key: string) => string[] | undefined;
  getFilteredPageIdsByCollection: (key: string) => string[] | undefined;
  /**
   * 某一行的类型。`undefined` = **还没取到**（首帧，树还没回来）——
   * 调用方要按"当作页面"处理（那正是今天的既有行为，不是新错）。
   */
  getPageNodeType: (pageId: string) => TPageNodeType | undefined;
  updateFilters: <T extends keyof TPageFilters>(filterKey: T, filterValue: TPageFilters[T]) => void;
  clearAllFilters: () => void;
  // actions
  fetchCollections: (workspaceSlug: string) => Promise<TPageCollectionListResponse | undefined>;
  createCollection: (workspaceSlug: string, name: string) => Promise<TPageCollection>;
  updateCollection: (
    workspaceSlug: string,
    collectionId: string,
    name: string
  ) => Promise<Omit<TPageCollection, "page_count">>;
  createPage: (workspaceSlug: string, payload: TPageCreatePayload) => Promise<TPage>;
  fetchPagesList: (workspaceSlug: string, collection: TCollectionFilter) => Promise<TPage[] | undefined>;
  fetchFolderPages: (workspaceSlug: string, folderId: string) => Promise<TPageWithParent[] | undefined>;
  fetchWikiTree: (workspaceSlug: string) => Promise<TWikiScopedPage[] | undefined>;
  fetchPageDetails: (workspaceSlug: string, pageId: string) => Promise<TPage | undefined>;
  fetchCandidates: (workspaceSlug: string) => Promise<TPage[]>;
  includePages: (
    workspaceSlug: string,
    pageIds: string[],
    collectionId: string | null
  ) => Promise<TPageIncludeResponse>;
  moveTo: (
    workspaceSlug: string,
    pageId: string,
    target: { collectionId: string | null; parentId: string | null }
  ) => Promise<void>;
  deleteFolder: (workspaceSlug: string, folderId: string) => Promise<void>;
  removeFromWiki: (workspaceSlug: string, pageId: string) => Promise<void>;
  removePage: (params: { pageId: string; shouldSync?: boolean }) => void;
}

export class WorkspacePageStore implements IWorkspacePageStore {
  // observables
  loader: TLoader = "init-loader";
  data: Record<string, TWorkspacePage> = {}; // pageId => Page
  collectionPageIds: Record<string, string[]> = {};
  collections: TPageCollection[] = [];
  predefined: TPredefinedCollection[] = [];
  candidates: TPage[] = [];
  treeRows: TWikiTreeRow[] = [];
  pageParentIds: Record<string, string | null> = {};
  pageNodeTypes: Record<string, TPageNodeType> = {};
  error: TError | undefined = undefined;
  filters: TPageFilters = {
    searchQuery: "",
    sortKey: "updated_at",
    sortBy: "desc",
  };
  // service
  service: WorkspacePageService;
  rootStore: CoreRootStore;

  constructor(private store: CoreRootStore) {
    makeObservable(this, {
      loader: observable.ref,
      data: observable,
      collectionPageIds: observable,
      collections: observable,
      predefined: observable,
      candidates: observable,
      treeRows: observable,
      pageParentIds: observable,
      pageNodeTypes: observable,
      error: observable,
      filters: observable,
      isAnyPageAvailable: computed,
      updateFilters: action,
      clearAllFilters: action,
      fetchCollections: action,
      createCollection: action,
      updateCollection: action,
      createPage: action,
      fetchPagesList: action,
      fetchFolderPages: action,
      fetchWikiTree: action,
      fetchPageDetails: action,
      fetchCandidates: action,
      includePages: action,
      moveTo: action,
      deleteFolder: action,
      removeFromWiki: action,
      removePage: action,
    });
    this.rootStore = store;
    this.service = new WorkspacePageService();
    // 与 ProjectPageStore 不同，这里**没有** router.projectId 的 reaction ——
    // 工作区 store 只依赖 workspaceSlug，切换项目不应触发任何重置。
  }

  get isAnyPageAvailable() {
    if (this.loader) return true;
    return Object.keys(this.data).length > 0;
  }

  getPageById = computedFn((pageId: string) => this.data?.[pageId] || undefined);

  /**
   * 某一行的类型判别符。
   *
   * **它的数据来自侧栏那次 `fetchAllPages`（`scope=all`）** —— 那条覆盖是完整的
   * （工作区里所有可见且已收录的页面），是任何 `?collection=` / `?folder=` 列表的
   * **超集**。侧栏住在 `wiki/layout.tsx`，两个列表视图都是它的子节点，所以取数是
   * 先行的 —— 但它是**异步**的：首帧可能还没到，那时这里返回 `undefined`。
   *
   * 外层包 `computedFn`：`observer` 组件里读它就自动是 observable 依赖，值一变就重渲染。
   */
  getPageNodeType = computedFn((pageId: string) => this.pageNodeTypes?.[pageId] || undefined);

  /** 某个分区已加载的页面 id。分区名与后端 `partition_pages` 的返回值对齐。 */
  getPageIdsByCollection = computedFn((key: string) => this.collectionPageIds[key] ?? undefined);

  /** 在已加载的 id 之上套一层搜索过滤 + 排序。 */
  getFilteredPageIdsByCollection = computedFn((key: string) => {
    const ids = this.collectionPageIds[key];
    if (!ids) return undefined;

    const query = this.filters.searchQuery.trim().toLowerCase();
    let pages = ids.map((id) => this.getPageById(id)).filter((page): page is TWorkspacePage => !!page);

    if (query) pages = pages.filter((page) => (page.name ?? "").toLowerCase().includes(query));

    const key2 = this.filters.sortKey === "created_at" ? "created_at" : "updated_at";
    // oxlint-disable-next-line unicorn/no-array-sort
    pages = [...pages].sort((a, b) => {
      const left = new Date((a[key2] as Date | undefined) ?? 0).getTime();
      const right = new Date((b[key2] as Date | undefined) ?? 0).getTime();
      return this.filters.sortBy === "asc" ? left - right : right - left;
    });

    return pages.map((page) => page.id) as string[];
  });

  updateFilters = <T extends keyof TPageFilters>(filterKey: T, filterValue: TPageFilters[T]) => {
    runInAction(() => {
      set(this.filters, [filterKey], filterValue);
    });
  };

  clearAllFilters = () =>
    runInAction(() => {
      set(this.filters, ["searchQuery"], "");
    });

  fetchCollections = async (workspaceSlug: string) => {
    try {
      const response = await this.service.fetchCollections(workspaceSlug);
      runInAction(() => {
        this.collections = response.collections;
        this.predefined = response.predefined;
      });
      return response;
    } catch (error) {
      runInAction(() => {
        this.error = { title: "Failed", description: "Failed to fetch the collections, Please try again later." };
      });
      throw error;
    }
  };

  /**
   * 新建集合。
   *
   * **不设 `loader`** —— 与 `moveTo` / `removeFromWiki` 同一惯例：
   * `loader` 驱动的是整个主面板的加载骨架，而新建集合只是往侧栏多插一行；
   * 把整页打回骨架是过度反应。调用方（弹窗）自己有 submitting 态。
   *
   * 成功后按本文件既有惯例重拉集合（`includePages` / `moveTo` / `removeFromWiki` 三处都这么做）。
   * 返回值**透传 service 的新集合** —— 调用方要拿它的 id 跳转。
   */
  createCollection = async (workspaceSlug: string, name: string) => {
    let collection: TPageCollection;
    try {
      collection = await this.service.createCollection(workspaceSlug, name);
    } catch (error) {
      runInAction(() => {
        this.error = { title: "Failed", description: "Failed to create the collection, Please try again later." };
      });
      throw error;
    }

    // 重拉集合**移出写入的 try**：写入此刻已经落库，让刷新失败把它报成「创建失败」是撒谎。
    // 创建路径上这个谎还有第二个代价 —— 弹窗会留在原地诱导重试，而 `PageCollection.name`
    // 没有唯一约束，重试会真的建出第二个集合。失败由 `fetchCollections` 自己记进 `this.error`，
    // 与 `move-to-modal.tsx` 同一条规矩：刷新失败只记不报。
    await this.fetchCollections(workspaceSlug).catch(() => {});

    return collection;
  };

  /**
   * 重命名集合。
   *
   * **不得吞掉异常** —— 弹窗靠它决定 toast 是「已重命名」还是「重命名失败」。
   * 与 `createCollection` 同样不设 `loader`。
   */
  updateCollection = async (workspaceSlug: string, collectionId: string, name: string) => {
    let collection: Omit<TPageCollection, "page_count">;
    try {
      collection = await this.service.updateCollection(workspaceSlug, collectionId, name);
    } catch (error) {
      runInAction(() => {
        this.error = { title: "Failed", description: "Failed to rename the collection, Please try again later." };
      });
      throw error;
    }

    // 同 `createCollection`：重拉集合不在写入的 try 里。改名已经落库，刷新失败只能记进
    // `this.error`，不能冒泡成「重命名失败」—— 弹窗靠这个异常决定 toast 的成败，报错即撒谎。
    await this.fetchCollections(workspaceSlug).catch(() => {});

    return collection;
  };

  /**
   * 在 Wiki 里新建页面。
   *
   * **不设 `loader`** —— 与 `createCollection` 同一条理由：`loader` 驱动的是整个主面板的
   * 加载骨架，而建页只是往当前分区多插一行，把整页打回骨架是过度反应。调用方（弹窗）
   * 自己有 submitting 态。
   *
   * **不得吞掉异常** —— 弹窗靠"action 是否 reject"决定 toast 成败，与 `createCollection`
   * 同一个契约。
   *
   * 返回值**透传 service 的新页面** —— 调用方要拿它的 id 跳转。
   */
  createPage = async (workspaceSlug: string, payload: TPageCreatePayload) => {
    let page: TPage;
    try {
      page = await this.service.createPage(workspaceSlug, payload);
    } catch (error) {
      runInAction(() => {
        this.error = { title: "Failed", description: "Failed to create the page, Please try again later." };
      });
      throw error;
    }

    // 重拉集合**移出写入的 try**：写入此刻已经落库，让刷新失败把它报成「创建失败」是撒谎。
    // 与 createCollection / updateCollection 同一条规矩（Round A 的 I-1a）。
    // 这一步不只是刷计数 —— 新页面此刻已经在库里，重拉之后它才会出现在侧栏那个分区。
    await this.fetchCollections(workspaceSlug).catch(() => {});
    await this.fetchWikiTree(workspaceSlug).catch(() => {});

    return page;
  };

  /**
   * @description 拉某个分区的页面。
   * 分区归属由**服务端**判定（apps/api/plane/utils/wiki_collections.py），
   * 前端不复制一套优先级规则，只按 key 记下 id 列表。
   */
  fetchPagesList = async (workspaceSlug: string, collection: TCollectionFilter) => {
    try {
      if (!workspaceSlug || !collection) return undefined;

      const existingIds = this.collectionPageIds[collection];
      runInAction(() => {
        this.loader = existingIds && existingIds.length > 0 ? "mutation-loader" : "init-loader";
        this.error = undefined;
      });

      const pages = await this.service.fetchPages(workspaceSlug, collection);
      runInAction(() => {
        for (const page of pages) {
          if (page?.id) {
            const existingPage = this.getPageById(page.id);
            if (existingPage) {
              const { name, ...otherFields } = page;
              existingPage.mutateProperties(otherFields, false);
            } else {
              set(this.data, [page.id], new WorkspacePage(this.store, page));
            }
            // 层级也记进旁挂索引：主列表的缩进要按它算（Task 7），而列表走的就是这条线。
            // 两个取数路径（这里与 `fetchWikiTree`）写的是同一个索引、同一份数据，
            // 谁先到都对。
            set(this.pageParentIds, [page.id], page.parent ?? null);
          }
        }
        set(
          this.collectionPageIds,
          [collection],
          pages.map((page) => page.id).filter((id): id is string => !!id)
        );
        this.loader = undefined;
      });

      return pages;
    } catch (error) {
      runInAction(() => {
        this.loader = undefined;
        this.error = { title: "Failed", description: "Failed to fetch the pages, Please try again later." };
      });
      throw error;
    }
  };

  /**
   * 某个文件夹的整棵子树 —— 「文件夹列表视图」的数据源（设计 §5.3）。
   *
   * **照 `fetchPagesList` 的形状**（同一个 `collectionPageIds` 键空间、同一套
   * `pageParentIds` 写入、同一个 loader 口径）：主面板那两个视图共用
   * `getFilteredPageIdsByCollection(collection)` 与 `WikiListRoot`，
   * 所以文件夹的键就用**文件夹 uuid**，两条路径在 store 这一层完全同形。
   *
   * **刻意不写 `pageNodeTypes`**（裁定 7）：这条响应来自 `WikiPageSerializer`，
   * 里面**没有** `node_type`；写了就等于把树灌进来的 `"folder"` 冲成 `"doc"`。
   */
  fetchFolderPages = async (workspaceSlug: string, folderId: string) => {
    try {
      if (!workspaceSlug || !folderId) return undefined;

      const existingIds = this.collectionPageIds[folderId];
      runInAction(() => {
        this.loader = existingIds && existingIds.length > 0 ? "mutation-loader" : "init-loader";
        this.error = undefined;
      });

      const pages = await this.service.fetchFolderPages(workspaceSlug, folderId);
      runInAction(() => {
        for (const page of pages) {
          if (page?.id) {
            const existingPage = this.getPageById(page.id);
            if (existingPage) {
              const { name, ...otherFields } = page;
              existingPage.mutateProperties(otherFields, false);
            } else {
              set(this.data, [page.id], new WorkspacePage(this.store, page));
            }
            // 层级照记：主列表的缩进要按它算，而这条路径**必须**记 —— 文件夹视图里
            // 父节点可能是另一个文件夹，而它也在 `pageParentIds` 里（由树那条写进来）。
            set(this.pageParentIds, [page.id], page.parent ?? null);
          }
        }
        set(
          this.collectionPageIds,
          [folderId],
          pages.map((page) => page.id).filter((id): id is string => !!id)
        );
        this.loader = undefined;
      });

      return pages;
    } catch (error) {
      runInAction(() => {
        this.loader = undefined;
        this.error = { title: "Failed", description: "Failed to fetch the folder, Please try again later." };
      });
      throw error;
    }
  };

  /**
   * 拉侧栏那棵树的数据 —— 全部已收录页 + 每行服务端算好的分区键（设计 B-5/B-6）。
   *
   * **不设 `loader`**：与 `createCollection` / `createPage` 同一条理由 —— `loader`
   * 驱动的是整个主面板的加载骨架，而建树只是给侧栏补上页面行，把整页打回骨架是过度反应。
   */
  fetchWikiTree = async (workspaceSlug: string) => {
    try {
      if (!workspaceSlug) return undefined;

      const rows = await this.service.fetchAllPages(workspaceSlug);

      runInAction(() => {
        for (const row of rows) {
          if (!row?.id) continue;
          const existingPage = this.getPageById(row.id);
          if (existingPage) {
            // `collection_key` 是**本端点独有**的字段，不能进 `mutateProperties` ——
            // 那会往页面实例上写一个没人认识的属性。它只活在 `treeRows` 里。
            // `node_type` 与 `collection_key` 一样是**本端点独有**的字段，不能进
            // `mutateProperties` —— 那是个盲写的 `set(this, key, value)`
            // （`base-page.ts:545-551`），会把没人认识的属性写到页面实例上。
            // 它只活在 `pageNodeTypes` 里。
            const { name, parent, node_type, collection_key: _collectionKey, ...otherFields } = row;
            existingPage.mutateProperties(otherFields, false);
          } else {
            set(this.data, [row.id], new WorkspacePage(this.store, { ...row }));
          }
          set(this.pageParentIds, [row.id], row.parent ?? null);
          // 缺省当 `doc`：**本轮之前建的页面没有这个字段**的可能性不存在（迁移给了默认值），
          // 但一个 `undefined` 会让 `getPageNodeType` 返回 undefined、把"还没到的类型"
          // 与"真的是页面"混在同一档 —— 树这条响应**总是**带这个字段，所以这里落成常量。
          set(this.pageNodeTypes, [row.id], row.node_type ?? PAGE_NODE_TYPE_DOC);
        }

        this.treeRows = rows
          .filter((row) => !!row.id)
          .map((row) => ({ pageId: row.id as string, collectionKey: row.collection_key }));
      });

      return rows;
    } catch (error) {
      runInAction(() => {
        this.error = { title: "Failed", description: "Failed to fetch the wiki tree, Please try again later." };
      });
      throw error;
    }
  };

  fetchPageDetails = async (workspaceSlug: string, pageId: string) => {
    try {
      if (!workspaceSlug || !pageId) return undefined;

      const currentPage = this.getPageById(pageId);
      runInAction(() => {
        this.loader = currentPage ? "mutation-loader" : "init-loader";
        this.error = undefined;
      });

      const page = await this.service.fetchById(workspaceSlug, pageId);
      runInAction(() => {
        if (page?.id) {
          const pageInstance = this.getPageById(page.id);
          if (pageInstance) {
            // 与 `fetchWikiTree` 同一条纪律：`node_type` 不能进 `mutateProperties`
            // （盲写）。它是第二条**可信来源** —— 点开 `/wiki/<uuid>` 时树可能还没回来，
            // 详情这条就是那时唯一的类型真相（`[pageId]/page.tsx` 靠它决定要不要
            // `replace` 到 `?folder=`）。
            const { node_type, ...otherFields } = page;
            pageInstance.mutateProperties(otherFields, false);
            set(this.pageNodeTypes, [page.id], node_type ?? PAGE_NODE_TYPE_DOC);
          } else {
            set(this.data, [page.id], new WorkspacePage(this.store, page));
            set(this.pageNodeTypes, [page.id], page.node_type ?? PAGE_NODE_TYPE_DOC);
          }
        }
        this.loader = undefined;
      });

      return page;
    } catch (error) {
      runInAction(() => {
        this.loader = undefined;
        this.error = { title: "Failed", description: "Failed to fetch the page, Please try again later." };
      });
      throw error;
    }
  };

  /** 收录弹窗的候选页面。刻意不写进 this.data —— 它们是未收录页面。 */
  fetchCandidates = async (workspaceSlug: string) => {
    const candidates = await this.service.fetchCandidates(workspaceSlug);
    runInAction(() => {
      this.candidates = candidates;
    });
    return candidates;
  };

  /**
   * 收录已有页面。收录后重新拉一次集合计数；当前分区的列表由调用方用 `fetchPagesList`
   * 重拉（见 `add-existing-page-modal.tsx`）—— 本方法不知道调用方在看哪个分区。
   *
   * `collectionId` 必填，理由同 service 层：省略会被后端当成「放回 general」，
   * 调用方必须自己表态。
   *
   * **返回 service 的 `{included}` 计数，不要丢掉它。** 后端 `includePages` 会经
   * `_visible_page_q` 滤掉他人私有页与跨工作区页（`collection.py:137`），被滤掉的页面
   * **静默跳过、不报错** —— 这个计数是调用方唯一能察觉「有几页没被收录」的信号，
   * 也是成功 toast 里 `{count}` 的唯一来源（`add_existing_page_modal.success_message`
   * 是 ICU 带复数的串，没有 `count` 就渲染原始 ICU 文本）。
   */
  includePages = async (workspaceSlug: string, pageIds: string[], collectionId: string | null) => {
    try {
      runInAction(() => {
        this.loader = "mutation-loader";
        this.error = undefined;
      });

      const response = await this.service.includePages(workspaceSlug, pageIds, collectionId);
      await this.fetchCollections(workspaceSlug);
      await this.fetchWikiTree(workspaceSlug).catch(() => {});

      runInAction(() => {
        this.loader = undefined;
      });

      return response;
    } catch (error) {
      runInAction(() => {
        this.loader = undefined;
        this.error = { title: "Failed", description: "Failed to add the pages, Please try again later." };
      });
      throw error;
    }
  };

  /**
   * 把页面或文件夹移到指定位置。
   *
   * 载荷按目标分**两种**，不并发两个键 —— 后端「非空 parent 覆盖 collection_id」那条
   * 规则是给直连 API 的调用方兜底的；UI 这条路走**明确**的那一种：
   *   · 目标是**集合顶层** ⇒ `{ collection_id, parent: null }`
   *   · 目标是**某一行**   ⇒ `{ parent }`（集合由后端从那一行推导）
   */
  moveTo = async (
    workspaceSlug: string,
    pageId: string,
    target: { collectionId: string | null; parentId: string | null }
  ) => {
    try {
      const payload =
        target.parentId !== null ? { parent: target.parentId } : { collection_id: target.collectionId, parent: null };

      const page = await this.service.update(workspaceSlug, pageId, payload);

      runInAction(() => {
        const instance = this.getPageById(pageId);
        if (instance) instance.mutateProperties(page, false);
        for (const key of Object.keys(this.collectionPageIds)) {
          this.collectionPageIds[key] = this.collectionPageIds[key].filter((id) => id !== pageId);
        }
      });

      await this.fetchCollections(workspaceSlug);
      // 整棵树都可能变了（子树的 parent 与集合都跟着走），所以重拉树，而不只是改本地索引。
      await this.fetchWikiTree(workspaceSlug).catch(() => {});
    } catch (error) {
      runInAction(() => {
        this.error = { title: "Failed", description: "Failed to move the page, Please try again later." };
      });
      throw error;
    }
  };

  /**
   * 删一个文件夹。**走的是与「移出 Wiki」同一个 HTTP 端点** —— 后端按 `node_type`
   * 分叉（设计 §4.3），所以这里只是给它一个诚实的名字与一句诚实的错误提示。
   *
   * 与 `removeFromWiki` 的差别在**本地状态**：删文件夹会让它的直接子节点换父，那些
   * `pageParentIds` 只有重拉树才修得对；而 `removeFromWiki` 的页面没有子节点要照顾
   * （它不动子页，见后端 `destroy` 的 docstring）。
   */
  deleteFolder = async (workspaceSlug: string, folderId: string) => {
    try {
      await this.service.removeFromWiki(workspaceSlug, folderId);

      runInAction(() => {
        unset(this.data, [folderId]);
        unset(this.pageNodeTypes, [folderId]);
        for (const key of Object.keys(this.collectionPageIds)) {
          this.collectionPageIds[key] = this.collectionPageIds[key].filter((id) => id !== folderId);
        }
      });

      await this.fetchCollections(workspaceSlug);
      await this.fetchWikiTree(workspaceSlug).catch(() => {});
    } catch (error) {
      runInAction(() => {
        this.error = { title: "Failed", description: "Failed to delete the folder, Please try again later." };
      });
      throw error;
    }
  };

  /** 移出 Wiki。**不删页面** —— 只是取消收录。 */
  removeFromWiki = async (workspaceSlug: string, pageId: string) => {
    try {
      await this.service.removeFromWiki(workspaceSlug, pageId);

      runInAction(() => {
        unset(this.data, [pageId]);
        unset(this.pageNodeTypes, [pageId]);
        for (const key of Object.keys(this.collectionPageIds)) {
          this.collectionPageIds[key] = this.collectionPageIds[key].filter((id) => id !== pageId);
        }
      });

      await this.fetchCollections(workspaceSlug);
      await this.fetchWikiTree(workspaceSlug).catch(() => {});
    } catch (error) {
      runInAction(() => {
        this.error = { title: "Failed", description: "Failed to remove the page, Please try again later." };
      });
      throw error;
    }
  };

  /**
   * 从本地状态摘除一个页面 —— **不发请求**。
   *
   * 调用方是 realtime 的 `deleted` 事件（`use-realtime-page-events.tsx:51,118`）：
   * 服务端已经把页面删了，这里再打一次 DELETE 是错的。这正是它与上面
   * `removeFromWiki` 的分界 —— 那个是「取消收录 + 调 DELETE 端点」，**页面还在**。
   * 两者语义不同，不能互相顶替。
   *
   * 补这个方法是因为 `useRealtimePageEvents` 无条件解构 `removePage`，而工作区
   * store 一直没有它：解构得到 `undefined`，`deleted` 事件一到就 TypeError。
   * 协同服务器是活的，这个事件真会到达 —— 不是假想。
   *
   * 顺带说明：`delete-page-modal.tsx:34` 也解构 `removePage`，是同一形状的第二处。
   * wiki 路径目前够不到它（`dropdowns/actions.tsx:187` 那条
   * `storeType === EPageStoreType.PROJECT &&` 把项目专属项挡住了），补上只是消除隐患，
   * **不代表** wiki 的删除流程被验收过。
   */
  removePage = ({ pageId }: { pageId: string; shouldSync?: boolean }) => {
    runInAction(() => {
      unset(this.data, [pageId]);
      unset(this.pageParentIds, [pageId]);
      unset(this.pageNodeTypes, [pageId]);
      this.treeRows = this.treeRows.filter((row) => row.pageId !== pageId);
      for (const key of Object.keys(this.collectionPageIds)) {
        this.collectionPageIds[key] = this.collectionPageIds[key].filter((id) => id !== pageId);
      }
    });
  };
}
