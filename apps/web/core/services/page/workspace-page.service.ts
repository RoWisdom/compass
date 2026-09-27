/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// types
import { API_BASE_URL } from "@plane/constants";
import type { TDocumentPayload, TPage } from "@plane/types";
// services
import { APIService } from "@/services/api.service";

/**
 * 四个预置分区键。名字由前端从 i18n 的 `wiki_collections.predefined.*` 取，后端只给 key 与计数。
 *
 * **与后端 `apps/api/plane/utils/wiki_collections.py` 的 `PREDEFINED_KEYS` 必须一致。**
 * 中间没有共享常量的通道，只能各写一份 —— 后端那边同样把 `PRIVATE_ACCESS` 手抄了一份并用
 * `tests/unit/utils/test_wiki_collections.py` 锁住，这里沿用同一套办法。
 * 类型由数组派生（而不是各写一遍），至少保证本文件内部不会漂移。
 */
export const PREDEFINED_COLLECTION_KEYS = ["general", "private", "shared", "archived"] as const;

export type TPredefinedCollectionKey = (typeof PREDEFINED_COLLECTION_KEYS)[number];

export type TPredefinedCollection = {
  key: TPredefinedCollectionKey;
  page_count: number;
};

/** `collection` 是预置分区键，还是用户自建集合的 uuid？ */
export const isPredefinedCollectionKey = (collection: string): collection is TPredefinedCollectionKey =>
  (PREDEFINED_COLLECTION_KEYS as readonly string[]).includes(collection);

/**
 * 这个分区能不能接收「收录」动作？只有两类可以：
 *
 * - `general` —— 预置分区里唯一归属明确的：`collection_id` 传 `null` 即落回 general；
 * - 用户自建集合 —— 传它自己的 uuid。
 *
 * `private` / `shared` / `archived` 是**派生**分区：页面落在哪里由 `access` / `archived_at`
 * 决定，不由 `collection_id` 决定（后端 `resolve_collection_key` 的优先级是
 * archived > private > 用户集合 > general）。在这三个分区里给收录入口，用户选中的页面会
 * 跑到别处去，所以干脆不给。
 */
export const canIncludeIntoCollection = (collection: string): boolean =>
  !isPredefinedCollectionKey(collection) || collection === "general";

/** 用户自建集合。 */
export type TPageCollection = {
  id: string;
  name: string;
  sort_order: number;
  created_at: string;
  page_count: number;
};

export type TPageCollectionListResponse = {
  predefined: TPredefinedCollection[];
  collections: TPageCollection[];
};

/** `POST wiki-pages/` 的响应：实际收录成功的页面数。 */
export type TPageIncludeResponse = {
  included: number;
};

/** 侧栏选中的分区：预置分区的 key，或某个集合的 uuid。 */
export type TCollectionFilter = TPredefinedCollectionKey | string;

export class WorkspacePageService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  /** 集合列表：4 个预置分区（带计数）+ 用户自建集合。 */
  async fetchCollections(workspaceSlug: string): Promise<TPageCollectionListResponse> {
    return this.get(`/api/workspaces/${workspaceSlug}/page-collections/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** 某个分区下的已收录页面。分区由**服务端**判定优先级，前端不重复一套规则。 */
  async fetchPages(workspaceSlug: string, collection: TCollectionFilter): Promise<TPage[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/wiki-pages/`, {
      params: { collection },
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** 本工作区**尚未收录**的页面 —— 「Add existing page」的候选。 */
  async fetchCandidates(workspaceSlug: string): Promise<TPage[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/wiki-pages/`, {
      params: { include_candidates: true },
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** 单个页面（含正文）。 */
  async fetchById(workspaceSlug: string, pageId: string): Promise<TPage> {
    return this.get(`/api/workspaces/${workspaceSlug}/wiki-pages/${pageId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * 收录已有页面进 Wiki。
   *
   * `collectionId` 必填：省略与传 `null` 在后端是同一种处理，都会把页面放回 general ——
   * 一篇已经在自定义集合里的页面会被静默移回 general，不报错。所以这里不给默认值，
   * 强制调用方表态（要放 general 就显式传 `null`）。
   *
   * 返回值是成功收录的**条数**，不是页面数组：后端会过滤掉不可见的页面
   * （别人的私有页、别的工作区的页），`included` 是调用方唯一能察觉被跳过的信号。
   */
  async includePages(
    workspaceSlug: string,
    pageIds: string[],
    collectionId: string | null
  ): Promise<TPageIncludeResponse> {
    return this.post(`/api/workspaces/${workspaceSlug}/wiki-pages/`, {
      page_ids: pageIds,
      collection_id: collectionId,
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** 换集合。`collectionId = null` 表示移回 general。也用于改标题等元数据。 */
  async update(
    workspaceSlug: string,
    pageId: string,
    data: Partial<TPage> & { collection_id?: string | null }
  ): Promise<TPage> {
    return this.patch(`/api/workspaces/${workspaceSlug}/wiki-pages/${pageId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** 写正文。走同一个 PATCH，只是载荷是 TDocumentPayload。 */
  async updateDescription(workspaceSlug: string, pageId: string, data: TDocumentPayload): Promise<TPage> {
    return this.patch(`/api/workspaces/${workspaceSlug}/wiki-pages/${pageId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** 移出 Wiki。只取消收录，**不删页面**。 */
  async removeFromWiki(workspaceSlug: string, pageId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/wiki-pages/${pageId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
