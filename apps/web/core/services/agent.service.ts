/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// helpers
import { API_BASE_URL } from "@plane/constants";
// services
import { APIService } from "@/services/api.service";

export type TAgentTier = "readonly" | "writer" | "ledger";

/** 岗位的展示面 —— 嵌在成员行里，只够画一行。 */
export type TAgentDefinitionLite = {
  id: string;
  name: string;
  description: string;
  tier: TAgentTier;
  color: string;
};

/** 岗位（工作区级）。说明书在这儿，不在成员行上。 */
export type TAgentDefinition = TAgentDefinitionLite & {
  workspace_id: string;
  instructions: string;
  skills: string[];
  model: string;
  profile: string;
  web_access: boolean;
  trusted_urls: string[];
  project_count: number;
  created_at: string;
  updated_at: string;
};

/** 一条可写的岗位字段集（新建与编辑共用）。 */
export type TAgentDefinitionWrite = Partial<
  Pick<
    TAgentDefinition,
    "name" | "description" | "instructions" | "skills" | "tier" | "model" | "profile" | "web_access" | "color"
  >
> & { name: string };

export type TAgentMember = {
  id: string;
  project_id: string;
  definition: TAgentDefinitionLite;
  is_active: boolean;
  /** 最近一次运行的**状态**。它是运行状态，不是在线状态（设计 §3）。 */
  last_run_status: "pending" | "awaiting_approval" | "running" | "succeeded" | "failed" | "cancelled" | null;
  last_run_at: string | null;
};

export type TAgentRun = {
  id: string;
  member_id: string;
  issue_id: string;
  status: "pending" | "awaiting_approval" | "running" | "succeeded" | "failed" | "cancelled";
  artifacts: string[];
  exit_code: number | null;
  error: string;
};

export class AgentService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  async listDefinitions(workspaceSlug: string): Promise<TAgentDefinition[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/agent-definitions/`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async createDefinition(workspaceSlug: string, payload: TAgentDefinitionWrite): Promise<TAgentDefinition> {
    return this.post(`/api/workspaces/${workspaceSlug}/agent-definitions/`, payload)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async updateDefinition(
    workspaceSlug: string,
    definitionId: string,
    payload: Partial<TAgentDefinitionWrite>
  ): Promise<TAgentDefinition> {
    return this.patch(`/api/workspaces/${workspaceSlug}/agent-definitions/${definitionId}/`, payload)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async deleteDefinition(workspaceSlug: string, definitionId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/agent-definitions/${definitionId}/`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async listMembers(workspaceSlug: string, projectId: string): Promise<TAgentMember[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/agent-members/`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async addMember(workspaceSlug: string, projectId: string, definitionId: string): Promise<TAgentMember> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/agent-members/`, {
      definition_id: definitionId,
    })
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async updateMember(
    workspaceSlug: string,
    projectId: string,
    memberId: string,
    payload: { is_active: boolean }
  ): Promise<TAgentMember> {
    return this.patch(`/api/workspaces/${workspaceSlug}/projects/${projectId}/agent-members/${memberId}/`, payload)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async removeMember(workspaceSlug: string, projectId: string, memberId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/projects/${projectId}/agent-members/${memberId}/`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async createRun(
    workspaceSlug: string,
    projectId: string,
    payload: { member_id: string; issue_id: string }
  ): Promise<TAgentRun> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/agent-runs/`, payload)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }
}
