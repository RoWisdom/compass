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

/**
 * `POST wiki-pages/` 的响应。
 *
 * `included` 是**匹配行数**（`QuerySet.update()` 的语义），不是**改变行数**：
 * 已经在 Wiki 里、且已经在目标集合里的页面也会被计入。所以
 * `included === page_ids.length` 不等于「全是新增」，反过来 `included < page_ids.length`
 * 也不等于「有页面被跳过了」。
 */
export type TPageIncludeResponse = {
  included: number;
};

/** 侧栏选中的分区：预置分区的 key，或某个集合的 uuid。 */
export type TCollectionFilter = TPredefinedCollectionKey | string;

/**
 * 工作区级 Wiki 页面的 HTTP 客户端。
 *
 * 所有方法的 `.catch` 都是 `throw error?.response?.data ?? error`，**不是**
 * `throw error?.response?.data`：**没有 HTTP 响应**时（离线 / DNS 失败 / CORS 被拦）
 * `error.response` 是 undefined，原来抛出去的就是 `undefined` —— 调用方那个
 * `catch (error)` 会把「undefined」当成「没出错」，用户停在 spinner 上永远转
 * （`wiki/[pageId]/page.tsx` 的 `pageDetailsError` 就是这么读的）。退回原始 error 对象，
 * 至少是个真值。
 */
export class WorkspacePageService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  /** 集合列表：4 个预置分区（带计数）+ 用户自建集合。 */
  async fetchCollections(workspaceSlug: string): Promise<TPageCollectionListResponse> {
    return this.get(`/api/workspaces/${workspaceSlug}/page-collections/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }

  /**
   * 新建集合。**返回新集合** —— 调用方（侧栏）要用响应里的 id 跳到新集合去，
   * 所以这里不能像别的写方法那样只返回 void。
   *
   * 后端返回的 body 与 `list` 的每一行同形（含 `page_count: 0`），
   * 所以调用方可以直接拿它塞进侧栏，不必为「新建」写兼容分支。
   */
  async createCollection(workspaceSlug: string, name: string): Promise<TPageCollection> {
    return this.post(`/api/workspaces/${workspaceSlug}/page-collections/`, { name })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }

  /**
   * 重命名集合。写契约只有 `name` —— 传别的键 DRF 会静默忽略（返回 200 而什么都没变）。
   *
   * 返回体**不带** `page_count`（后端只回裸集合）：那个计数是 `list` 现算给侧栏用的，
   * 这里的调用方只拿它判成功，随后由 store 重拉整个集合列表补齐计数。
   */
  async updateCollection(workspaceSlug: string, collectionId: string, name: string): Promise<TPageCollection> {
    return this.patch(`/api/workspaces/${workspaceSlug}/page-collections/${collectionId}/`, { name })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }

  /** 某个分区下的已收录页面。分区由**服务端**判定优先级，前端不重复一套规则。 */
  async fetchPages(workspaceSlug: string, collection: TCollectionFilter): Promise<TPage[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/wiki-pages/`, {
      params: { collection },
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }

  /** 本工作区**尚未收录**的页面 —— 「Add existing page」的候选。 */
  async fetchCandidates(workspaceSlug: string): Promise<TPage[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/wiki-pages/`, {
      params: { include_candidates: true },
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }

  /** 单个页面（含正文）。 */
  async fetchById(workspaceSlug: string, pageId: string): Promise<TPage> {
    return this.get(`/api/workspaces/${workspaceSlug}/wiki-pages/${pageId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
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
        throw error?.response?.data ?? error;
      });
  }

  /**
   * 换集合，以及（本阶段起）改标题。`collectionId = null` 表示移回 general。
   *
   * **不要用它改其它元数据**：`WikiPageUpdateSerializer` 只声明了
   * `collection_id` / `name` / 三个 `description_*`，传别的键 DRF 会静默忽略 ——
   * 返回 200 而什么都没变。
   *
   * （这条注释曾写着「不能改标题，因为序列化器没有 `name` 字段」。Phase 1B 给
   *   序列化器加上了 `name`，协同服务器的标题同步依赖它，所以那句话不再成立。）
   */
  async update(
    workspaceSlug: string,
    pageId: string,
    data: Partial<TPage> & { collection_id?: string | null }
  ): Promise<TPage> {
    return this.patch(`/api/workspaces/${workspaceSlug}/wiki-pages/${pageId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }

  /** 写正文。走**二进制正文**端点：只有它才写 vault 镜像（与项目页兄弟同一形状）。
   *  指回元数据路由会让镜像在 websocket 断线时静默不更新 —— 而那正是回退存在的唯一场景。 */
  async updateDescription(workspaceSlug: string, pageId: string, data: TDocumentPayload): Promise<any> {
    return this.patch(`/api/workspaces/${workspaceSlug}/wiki-pages/${pageId}/description/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }

  /** 拉正文的二进制（Yjs 快照）。端点由 Task 3 提供。 */
  async fetchDescriptionBinary(workspaceSlug: string, pageId: string): Promise<any> {
    return this.get(`/api/workspaces/${workspaceSlug}/wiki-pages/${pageId}/description/`, {
      headers: {
        "Content-Type": "application/octet-stream",
      },
      responseType: "arraybuffer",
    })
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
        throw error?.response?.data ?? error;
      });
  }
}
