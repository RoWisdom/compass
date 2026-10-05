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

/**
 * 新页面的归属 —— **由侧栏按当前上下文推导好**再交给弹窗（设计 §3.2d 的表 + B-3）。
 *
 * 三种形状，由侧栏决定用哪一种：
 *   - `{ parent }` —— 建**子页**。此时**不传** `access` / `collection_id`，
 *     让后端按父页继承（设计 B-3）。前端自己把父页的 access/collection 抄一遍
 *     就是第二个真相源，而且父页在同一瞬间被改动时两边立刻不一致。
 *   - `{ collection_id, access }` —— 建在某个集合 / 某个预置分区里（既有形状）。
 *   - 三者都省略 —— 不会发生，但类型上允许。
 *
 * 三个键都从「必填」放宽成「可选」是**刻意的**：继承的全部意义就是「不传」。
 * `undefined` 的键在 JSON 序列化时会被丢掉（axios 用 `JSON.stringify`），
 * 但弹窗里仍然写成显式分叉，不依赖这条隐含行为。
 *
 * `collection_id` 为 `null` 时页面落 general —— 与 `includePages` 的 `collectionId`
 * 是同一个语义（省略与传 `null` 在后端是同一种处理）。
 */
export type TPageCreateTarget = {
  collection_id?: string | null;
  /**
   * `1` = 私有。私有分区**只能**靠它表达 —— 优先级是
   * archived > private > collection_id > general，所以私有页的 `collection` 会被完全忽略，
   * 「建到 Private 视图」不可能是 `collection_id=private`。
   */
  access?: 0 | 1;
  /** 父页 id。给了就建在这页下面，并从它继承 `access` / `collection_id`。 */
  parent?: string;
  /**
   * 要建的是页面还是文件夹。**只有文件夹才给** —— 页面走后端的默认值（`"doc"`），
   * 前端不重复声明一遍默认值。
   */
  node_type?: TPageNodeType;
};

/**
 * `POST wiki-pages/create/` 的请求体。五个键**全部可选** —— 建一个页面不需要任何参数，
 * 空 `{}` 是合法载荷（后端会建出一个空名、公开、无项目、落 general 的页面）。
 *
 * - `project_id` —— 给了就挂到该项目下，并因此**获得一个 vault 落点**
 *   （`2-项目/<项目名>/`）；不给就是"无项目页"，**永远不会进 vault**。
 * - `collection_id` —— 只对"建到某个自建集合"有意义；`access=1` 时它会被忽略。
 * - `access` —— 0 公开 / 1 私有。省略即 0。
 * - `parent` —— 给了就建在这页下面，并从它继承 `access` / `collection_id`。
 */
export type TPageCreatePayload = {
  name?: string;
  project_id?: string | null;
  collection_id?: string | null;
  access?: 0 | 1;
  /** 父页 id。后端会做三条前置校验（本工作区 / 可见 / 已收录），不合规 404。 */
  parent?: string;
  /** 要建的是页面还是文件夹。省略即 `"doc"`（后端 `WikiPageCreateSerializer` 的默认值）。 */
  node_type?: TPageNodeType;
};

/**
 * `wiki-pages/` 列表响应里的**层级**字段。
 *
 * `TPage` 刻意**不带** `parent`：`BasePage` 的实例是**逐字段显式赋值**构造的
 * （`core/store/pages/base-page.ts` 的 :98 声明 / :136 构造 / :162 makeObservable /
 * :239 asJSON **四处**），加一个字段要同步改四处，而 `BasePage` 被**项目页**共用 ——
 * wiki 的层级没有理由去动它。所以层级只在这条线上被读一次：读**原始响应**，
 * 随即落进 store 的旁挂索引，**不进页面模型**。
 *
 * `node_type` 同一条待遇：**不进 `BasePage`**，只落 store 的旁挂索引
 * （`workspace-page.store.ts` 的 `pageNodeTypes`）。而它**不是**每条列表响应都有 ——
 * `fetchPages` / `fetchFolderPages` 走的是 `WikiPageSerializer`，那里**没有**这个字段
 * （默认列表路径的响应必须逐字不变）。只有 `?scope=all`（树）与详情两条带它。
 * 所以标记为可选是**如实的**，不是偷懒。
 */
export type TPageWithParent = TPage & { parent?: string | null; node_type?: TPageNodeType | null };

/**
 * `?scope=all` 的行：在页面字段之上多一个**服务端算好的**分区键（设计 B-6）。
 *
 * 前端**不**用 `archived_at` / `access` / `collection_id` 自己判分区 ——
 * 那是有优先级的业务规则（archived > private > 集合 > general），复刻一遍
 * 就有了第二个真相源。服务端算什么就是什么。
 */
export type TWikiScopedPage = TPageWithParent & { collection_key: string };

/** 侧栏选中的分区：预置分区的 key，或某个集合的 uuid。 */
export type TCollectionFilter = TPredefinedCollectionKey | string;

/**
 * Wiki 树节点的类型判别符（罗盘 Round D）。**与后端 `Page.NODE_TYPE_*` 必须一致** ——
 * 中间没有共享常量的通道，只能各写一份（`PREDEFINED_COLLECTION_KEYS` 也是这么手抄的，
 * 那条注释写了同样的理由）。
 *
 * `doc` 有正文、可编辑、写 vault 镜像；`folder` 都没有（Confluence F2/F4）。
 * **类型建时定死**（F16：文件夹不能变回页面）—— 前端只在**新建**时发它，
 * 别处一律只读。
 */
export const PAGE_NODE_TYPE_DOC = "doc";
export const PAGE_NODE_TYPE_FOLDER = "folder";
export type TPageNodeType = typeof PAGE_NODE_TYPE_DOC | typeof PAGE_NODE_TYPE_FOLDER;

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
   *
   * 返回类型省掉 `page_count`，正是因为服务端也省掉了它 —— 这不是笔误，别改回
   * `TPageCollection`；需要计数的调用方，请在 store 重拉集合列表之后从 `store.collections` 读。
   */
  async updateCollection(
    workspaceSlug: string,
    collectionId: string,
    name: string
  ): Promise<Omit<TPageCollection, "page_count">> {
    return this.patch(`/api/workspaces/${workspaceSlug}/page-collections/${collectionId}/`, { name })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }

  /**
   * 删除集合。**不返回体** —— 后端是 `204 No Content`（与删页面的 `removeFromWiki` 同形）。
   *
   * 集合里的页面与文件夹**会一并被删**（Round I 起）：后端软删整个集合的内容，
   * vault 里那些页面的镜像文件也随级联收走。所以调用方之后要重拉的**不只是**
   * 集合列表 —— 见 store 里同名方法的注释。
   */
  async deleteCollection(workspaceSlug: string, collectionId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/page-collections/${collectionId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }

  /**
   * 在 Wiki 里**新建**一个页面。
   *
   * 与 `includePages` 的分界：那个是"把已有页面收录进来"，这个是"从零建一个"。
   *
   * **返回新页面** —— 调用方（弹窗）要用它的 `id` 跳过去，所以这里不能像别的写方法
   * 那样只返回 void。
   */
  async createPage(workspaceSlug: string, payload: TPageCreatePayload): Promise<TPage> {
    return this.post(`/api/workspaces/${workspaceSlug}/wiki-pages/create/`, payload)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }

  /** 某个分区下的已收录页面。分区由**服务端**判定优先级，前端不重复一套规则。 */
  async fetchPages(workspaceSlug: string, collection: TCollectionFilter): Promise<TPageWithParent[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/wiki-pages/`, {
      params: { collection },
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }

  /**
   * **全部**分区的已收录页面 —— 侧栏那棵树的数据源（设计 B-5）。
   *
   * 与 `fetchPages` 的分界：那个按**单个**分区取（主列表在用），这个一次取回全部，
   * 每行带一个**服务端算好的**分区键（设计 B-6）。
   *
   * 走独立参数 `scope=all`，**不复用** `collection=all`：`collection` 的值域是
   * 「预置键或 uuid」，往里塞哨兵值等于在一个已有的值域里开洞。
   */
  async fetchAllPages(workspaceSlug: string): Promise<TWikiScopedPage[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/wiki-pages/`, {
      params: { scope: "all" },
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }

  /**
   * 某个文件夹下的**整棵子树** —— 主面板的「文件夹列表视图」的数据源（设计 §5.3）。
   *
   * 与 `fetchPages` 的分界：那个按**分区**取（`?collection=`），这个按**文件夹**取
   * （`?folder=`）。**不复用 `collection`**：后者的值域是「预置键或集合 uuid」，
   * 塞一个文件夹 uuid 进去会落进自建集合那条分支、按 `collection_id` 过滤，
   * **静默**返回空列表。
   *
   * 响应**含子文件夹**（后端裁定 5），且**不含文件夹自己**（裁定 4）。行里**没有**
   * `node_type` —— 类型从 store 的 `pageNodeTypes` 拿，那份由 `fetchAllPages`
   * （侧栏的 `scope=all`）灌满。见 `WikiPageSerializer` 那段的说明。
   */
  async fetchFolderPages(workspaceSlug: string, folderId: string): Promise<TPageWithParent[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/wiki-pages/`, {
      params: { folder: folderId },
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
  async fetchById(workspaceSlug: string, pageId: string): Promise<TPageWithParent> {
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
   * 改**位置**（`collection_id` 与/或 `parent`），以及改标题。
   *
   * `parent` 的三种取值就是 PATCH 的三种语义，别混：
   *   · `undefined`（不传这个键）—— 位置不动，只改标题；
   *   · `null`               —— 移到**集合顶层**，落到 `collectionId` 指示的那个集合；
   *   · uuid                 —— 挂到那一行下面，**集合由后端从目标推导**（位置决定集合）。
   *
   * **不要用它改别的元数据**：`WikiPageUpdateSerializer` 只声明了
   * `collection_id` / `parent` / `name` / 三个 `description_*`，传别的键 DRF 会**静默忽略** ——
   * 返回 200 而什么都没变。
   */
  async update(
    workspaceSlug: string,
    pageId: string,
    data: Partial<TPage> & { collection_id?: string | null; parent?: string | null }
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

  /**
   * 移出 Wiki。只取消收录，**不删页面**。
   *
   * 同一个端点也承载**删文件夹**（`WikiPageViewSet.destroy` 按 `node_type` 分叉）：
   * 页面走「移出 Wiki」，文件夹走「连同整棵子树一并删除」（Round I 起，设计 §4.1）。
   * 前端不需要第二个 service 方法 —— 差别在**语义**不在**请求**（设计 §4.3）。
   */
  async removeFromWiki(workspaceSlug: string, pageId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/wiki-pages/${pageId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }
}
