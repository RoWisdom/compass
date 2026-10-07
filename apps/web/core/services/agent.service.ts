/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// helpers
import { API_BASE_URL } from "@plane/constants";
// services
import { APIService } from "@/services/api.service";

export type TAgentMember = {
  id: string;
  name: string;
  color: string;
  tier: "readonly" | "writer" | "ledger";
  is_active: boolean;
  project_id: string;
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

  async listMembers(workspaceSlug: string, projectId: string): Promise<TAgentMember[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/agent-members/`)
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
