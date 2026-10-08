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
  /** 这个成员是从哪个岗位组部署来的。**只读** —— 换组只能靠重新部署那个组（设计 §2）。 */
  group: TAgentGroupLite | null;
  is_active: boolean;
  /** 最近一次运行的**状态**。它是运行状态，不是在线状态（设计 §3）。 */
  last_run_status: "pending" | "awaiting_approval" | "running" | "succeeded" | "failed" | "cancelled" | null;
  last_run_at: string | null;
};

/** 岗位组的展示面 —— 嵌在成员行里，只够画一个标签。 */
export type TAgentGroupLite = {
  id: string;
  name: string;
};

/**
 * 岗位组（工作区级）：一组岗位 + 一段**组级正文**，一次部署进项目。
 *
 * ``definitions`` 是服务端给的**只读**嵌套名册，只含**还活着的**岗位 —— 编辑表单的勾选
 * 列表读它。名册里可能还留着一个已删岗位（服务端会因此拒部署并点名），那种状态在这里
 * 看不见，也不需要看见：修复路径是「改组、把名册重送一遍」。
 */
export type TAgentGroup = {
  id: string;
  workspace_id: string;
  name: string;
  description: string;
  instructions: string;
  definitions: TAgentDefinitionLite[];
  project_count: number;
  created_at: string;
  updated_at: string;
};

/** 一条可写的岗位组字段集（新建与编辑共用）。 */
export type TAgentGroupWrite = Partial<Pick<TAgentGroup, "name" | "description" | "instructions">> & {
  name: string;
  /**
   * 名册 —— **必须送完整的目标集合**，不是差量：服务端拿「保存之前的 through 行」与它
   * 求差，再按 buzz 的规则只动该动的成员。
   *
   * 两件事分得很清：**字段缺席 ⇒ 名册纹丝不动**（只改名字的 PATCH 不会顺手清空名册、
   * 更不会顺手把成员解绑）；**送 ``[]`` ⇒ 清空名册**。
   */
  definition_ids?: string[];
};

/** 一次部署里每个岗位落进了哪一格。 */
export type TAgentGroupDeployRow = {
  definition_id: string;
  name: string;
};

/**
 * 一次部署的回执。**部分成功是一等公民**（buzz：``Deployed N agents. M failed.``）——
 * 别把它折叠成一句「失败」。
 *
 * 四格：``created`` 新建了成员、``bound`` 把已有但没绑组的成员挂上了本组、
 * ``unchanged`` 本来就绑着本组（幂等）、``conflicts`` 已属**别组**（**绝不抢绑定**）。
 */
export type TAgentGroupDeployResult = {
  created: TAgentGroupDeployRow[];
  bound: TAgentGroupDeployRow[];
  unchanged: TAgentGroupDeployRow[];
  conflicts: TAgentGroupDeployRow[];
  failures: TAgentGroupDeployRow[];
  group: TAgentGroupLite;
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

  async listGroups(workspaceSlug: string): Promise<TAgentGroup[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/agent-groups/`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async createGroup(workspaceSlug: string, payload: TAgentGroupWrite): Promise<TAgentGroup> {
    return this.post(`/api/workspaces/${workspaceSlug}/agent-groups/`, payload)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async updateGroup(workspaceSlug: string, groupId: string, payload: Partial<TAgentGroupWrite>): Promise<TAgentGroup> {
    return this.patch(`/api/workspaces/${workspaceSlug}/agent-groups/${groupId}/`, payload)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async deleteGroup(workspaceSlug: string, groupId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/agent-groups/${groupId}/`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async deployGroup(workspaceSlug: string, projectId: string, groupId: string): Promise<TAgentGroupDeployResult> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/agent-groups/${groupId}/deploy/`, {})
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
