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

/** 四个预置分区。名字由前端从 i18n 的 `wiki_collections.predefined.*` 取，后端只给 key 与计数。 */
export type TPredefinedCollectionKey = "general" | "private" | "shared" | "archived";

export type TPredefinedCollection = {
  key: TPredefinedCollectionKey;
  page_count: number;
};

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
