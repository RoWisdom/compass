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
  TPageIncludeResponse,
  TPredefinedCollection,
} from "@/services/page";
import { WorkspacePageService } from "@/services/page";
// store
import type { CoreRootStore } from "../root.store";
import type { TWorkspacePage } from "./workspace-page";
import { WorkspacePage } from "./workspace-page";

type TLoader = "init-loader" | "mutation-loader" | undefined;

type TError = { title: string; description: string };

export interface IWorkspacePageStore {
  // observables
  loader: TLoader;
  data: Record<string, TWorkspacePage>; // pageId => Page
  collectionPageIds: Record<string, string[]>; // 分区 key 或集合 uuid => pageIds
  collections: TPageCollection[];
  predefined: TPredefinedCollection[];
  candidates: TPage[];
  error: TError | undefined;
  filters: TPageFilters;
  // computed
  isAnyPageAvailable: boolean;
  // helper actions
  getPageById: (pageId: string) => TWorkspacePage | undefined;
  getPageIdsByCollection: (key: string) => string[] | undefined;
  getFilteredPageIdsByCollection: (key: string) => string[] | undefined;
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
  fetchPagesList: (workspaceSlug: string, collection: TCollectionFilter) => Promise<TPage[] | undefined>;
  fetchPageDetails: (workspaceSlug: string, pageId: string) => Promise<TPage | undefined>;
  fetchCandidates: (workspaceSlug: string) => Promise<TPage[]>;
  includePages: (
    workspaceSlug: string,
    pageIds: string[],
    collectionId: string | null
  ) => Promise<TPageIncludeResponse>;
  moveToCollection: (workspaceSlug: string, pageId: string, collectionId: string | null) => Promise<void>;
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
      error: observable,
      filters: observable,
      isAnyPageAvailable: computed,
      updateFilters: action,
      clearAllFilters: action,
      fetchCollections: action,
      createCollection: action,
      updateCollection: action,
      fetchPagesList: action,
      fetchPageDetails: action,
      fetchCandidates: action,
      includePages: action,
      moveToCollection: action,
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
   * **不设 `loader`** —— 与 `moveToCollection` / `removeFromWiki` 同一惯例：
   * `loader` 驱动的是整个主面板的加载骨架，而新建集合只是往侧栏多插一行；
   * 把整页打回骨架是过度反应。调用方（弹窗）自己有 submitting 态。
   *
   * 成功后按本文件既有惯例重拉集合（`includePages` / `moveToCollection` / `removeFromWiki` 三处都这么做）。
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
    // 与 `move-to-collection-modal.tsx:66-68` 同一条规矩：刷新失败只记不报。
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
          if (pageInstance) pageInstance.mutateProperties(page, false);
          else set(this.data, [page.id], new WorkspacePage(this.store, page));
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

  /** 换集合。同时把该页面从旧分区的 id 列表里摘掉，避免侧栏计数与实际不一致。 */
  moveToCollection = async (workspaceSlug: string, pageId: string, collectionId: string | null) => {
    try {
      const page = await this.service.update(workspaceSlug, pageId, { collection_id: collectionId });

      runInAction(() => {
        const instance = this.getPageById(pageId);
        if (instance) instance.mutateProperties(page, false);
        for (const key of Object.keys(this.collectionPageIds)) {
          this.collectionPageIds[key] = this.collectionPageIds[key].filter((id) => id !== pageId);
        }
      });

      await this.fetchCollections(workspaceSlug);
    } catch (error) {
      runInAction(() => {
        this.error = { title: "Failed", description: "Failed to move the page, Please try again later." };
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
        for (const key of Object.keys(this.collectionPageIds)) {
          this.collectionPageIds[key] = this.collectionPageIds[key].filter((id) => id !== pageId);
        }
      });

      await this.fetchCollections(workspaceSlug);
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
      for (const key of Object.keys(this.collectionPageIds)) {
        this.collectionPageIds[key] = this.collectionPageIds[key].filter((id) => id !== pageId);
      }
    });
  };
}
