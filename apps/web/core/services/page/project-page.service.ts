/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// types
import { API_BASE_URL } from "@plane/constants";
import type { TDocumentPayload, TPage } from "@plane/types";
// helpers
// services
import { APIService } from "@/services/api.service";
import { FileUploadService } from "@/services/file-upload.service";
import type { TPageNodeType } from "./workspace-page.service";

/**
 * `?scope=all` 的行：在页面字段之上多一个 `node_type`（后端 `ProjectPageTreeSerializer`）。
 *
 * `TPage` **刻意不带** `parent` / `node_type`：`BasePage` 的实例是逐字段显式赋值构造的，
 * 而它被**项目页与 wiki 页共用**，层级没有理由去动它。所以层级只在这条线上被读一次
 * —— 读**原始响应**，随即落进 store 的旁挂索引，**不进页面模型**。
 * 与 wiki 侧的 `TPageWithParent`（`workspace-page.service.ts`）同一条设计。
 */
export type TProjectPageWithParent = TPage & { parent?: string | null; node_type?: TPageNodeType | null };

export class ProjectPageService extends APIService {
  private fileUploadService: FileUploadService;

  constructor() {
    super(API_BASE_URL);
    // upload service
    this.fileUploadService = new FileUploadService();
  }

  /**
   * 项目页的**整棵树**（页面 + 文件夹）。
   *
   * 必须带 `scope=all`：不带时后端只返回 `parent IS NULL` 的行（上游行为），
   * 子页与文件夹一个都拿不到，前端没法建树。
   *
   * 与 wiki 侧的 `fetchAllPages` 同一条路子 —— 那个端点是新增的，这个只是在既有
   * 端点上加一个 opt-in 参数（裁定 乙），所以**不带参数的那条路径一行未动**，
   * 详情页那颗平铺 switcher 依赖的正是它。
   */
  async fetchAll(workspaceSlug: string, projectId: string): Promise<TProjectPageWithParent[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/pages/`, {
      params: { scope: "all" },
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchById(workspaceSlug: string, projectId: string, pageId: string, trackVisit: boolean): Promise<TPage> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/pages/${pageId}/`, {
      params: {
        track_visit: trackVisit,
      },
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async create(workspaceSlug: string, projectId: string, data: Partial<TPage>): Promise<TPage> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/pages/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async update(workspaceSlug: string, projectId: string, pageId: string, data: Partial<TPage>): Promise<TPage> {
    return this.patch(`/api/workspaces/${workspaceSlug}/projects/${projectId}/pages/${pageId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateAccess(
    workspaceSlug: string,
    projectId: string,
    pageId: string,
    data: Pick<TPage, "access">
  ): Promise<void> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/pages/${pageId}/access/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async remove(workspaceSlug: string, projectId: string, pageId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/projects/${projectId}/pages/${pageId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchFavorites(workspaceSlug: string, projectId: string): Promise<TPage[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/favorite-pages/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async addToFavorites(workspaceSlug: string, projectId: string, pageId: string): Promise<void> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/favorite-pages/${pageId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async removeFromFavorites(workspaceSlug: string, projectId: string, pageId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/projects/${projectId}/favorite-pages/${pageId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchArchived(workspaceSlug: string, projectId: string): Promise<TPage[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/archived-pages/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async archive(
    workspaceSlug: string,
    projectId: string,
    pageId: string
  ): Promise<{
    archived_at: string;
  }> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/pages/${pageId}/archive/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async restore(workspaceSlug: string, projectId: string, pageId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/projects/${projectId}/pages/${pageId}/archive/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async lock(workspaceSlug: string, projectId: string, pageId: string): Promise<void> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/pages/${pageId}/lock/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async unlock(workspaceSlug: string, projectId: string, pageId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/projects/${projectId}/pages/${pageId}/lock/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchDescriptionBinary(workspaceSlug: string, projectId: string, pageId: string): Promise<any> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/pages/${pageId}/description/`, {
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

  async updateDescription(
    workspaceSlug: string,
    projectId: string,
    pageId: string,
    data: TDocumentPayload
  ): Promise<any> {
    return this.patch(`/api/workspaces/${workspaceSlug}/projects/${projectId}/pages/${pageId}/description/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error;
      });
  }

  async duplicate(workspaceSlug: string, projectId: string, pageId: string): Promise<TPage> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/pages/${pageId}/duplicate/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
