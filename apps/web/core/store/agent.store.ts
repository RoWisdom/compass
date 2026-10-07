/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { action, makeObservable, observable, runInAction } from "mobx";
// plane imports
import type { TAgentMember, TAgentRun } from "@/services/agent.service";
import { AgentService } from "@/services/agent.service";

export interface IAgentStore {
  // observables
  members: Record<string, TAgentMember>;
  // computed
  getMembersByProject: (projectId: string) => TAgentMember[];
  // actions
  fetchMembers: (workspaceSlug: string, projectId: string) => Promise<void>;
  wake: (workspaceSlug: string, projectId: string, issueId: string, memberId: string) => Promise<TAgentRun>;
}

export class AgentStore implements IAgentStore {
  members: Record<string, TAgentMember> = {};

  agentService;

  constructor() {
    makeObservable(this, {
      members: observable,
      getMembersByProject: action,
      fetchMembers: action,
      wake: action,
    });
    this.agentService = new AgentService();
  }

  getMembersByProject = (projectId: string) =>
    Object.values(this.members).filter((member) => member.project_id === projectId);

  fetchMembers = async (workspaceSlug: string, projectId: string) => {
    const response = await this.agentService.listMembers(workspaceSlug, projectId);
    runInAction(() => {
      // Replace this project's slice wholesale — the other projects' members stay.
      const next = Object.fromEntries(
        Object.entries(this.members).filter(([, member]) => member.project_id !== projectId)
      );
      response.forEach((member) => {
        next[member.id] = member;
      });
      this.members = next;
    });
  };

  wake = async (workspaceSlug: string, projectId: string, issueId: string, memberId: string) => {
    // No store copy of runs in phase 1: the caller only needs the run it just
    // created (to show a toast / follow it), and the run list is not editable here.
    return this.agentService.createRun(workspaceSlug, projectId, { member_id: memberId, issue_id: issueId });
  };
}
