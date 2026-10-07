/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { action, makeObservable, observable, runInAction } from "mobx";
import { computedFn } from "mobx-utils";
// plane imports
import type { TAgentDefinition, TAgentDefinitionWrite, TAgentMember, TAgentRun } from "@/services/agent.service";
import { AgentService } from "@/services/agent.service";

export interface IAgentStore {
  // observables
  members: Record<string, TAgentMember>;
  definitions: Record<string, TAgentDefinition>;
  // computed
  getMembersByProject: (projectId: string) => TAgentMember[];
  getDefinitionList: () => TAgentDefinition[];
  // actions
  fetchDefinitions: (workspaceSlug: string) => Promise<void>;
  createDefinition: (workspaceSlug: string, payload: TAgentDefinitionWrite) => Promise<TAgentDefinition>;
  updateDefinition: (
    workspaceSlug: string,
    definitionId: string,
    payload: Partial<TAgentDefinitionWrite>
  ) => Promise<TAgentDefinition>;
  deleteDefinition: (workspaceSlug: string, definitionId: string) => Promise<void>;
  fetchMembers: (workspaceSlug: string, projectId: string) => Promise<void>;
  addMember: (workspaceSlug: string, projectId: string, definitionId: string) => Promise<TAgentMember>;
  updateMember: (
    workspaceSlug: string,
    projectId: string,
    memberId: string,
    payload: { is_active: boolean }
  ) => Promise<TAgentMember>;
  removeMember: (workspaceSlug: string, projectId: string, memberId: string) => Promise<void>;
  wake: (workspaceSlug: string, projectId: string, issueId: string, memberId: string) => Promise<TAgentRun>;
}

export class AgentStore implements IAgentStore {
  members: Record<string, TAgentMember> = {};
  // The post library is workspace-level: one slice for the whole workspace,
  // not keyed by project like `members`.
  definitions: Record<string, TAgentDefinition> = {};

  agentService;

  constructor() {
    makeObservable(this, {
      members: observable,
      definitions: observable,
      fetchDefinitions: action,
      createDefinition: action,
      updateDefinition: action,
      deleteDefinition: action,
      fetchMembers: action,
      addMember: action,
      updateMember: action,
      removeMember: action,
      wake: action,
    });
    this.agentService = new AgentService();
  }

  getMembersByProject = computedFn((projectId: string) =>
    Object.values(this.members).filter((member) => member.project_id === projectId)
  );

  getDefinitionList = computedFn(() => Object.values(this.definitions));

  fetchDefinitions = async (workspaceSlug: string) => {
    const response = await this.agentService.listDefinitions(workspaceSlug);
    runInAction(() => {
      // Workspace-level: the response is the whole library, so replace it wholesale.
      this.definitions = Object.fromEntries(response.map((definition) => [definition.id, definition]));
    });
  };

  createDefinition = async (workspaceSlug: string, payload: TAgentDefinitionWrite) => {
    const definition = await this.agentService.createDefinition(workspaceSlug, payload);
    runInAction(() => {
      this.definitions = { ...this.definitions, [definition.id]: definition };
    });
    return definition;
  };

  updateDefinition = async (workspaceSlug: string, definitionId: string, payload: Partial<TAgentDefinitionWrite>) => {
    const definition = await this.agentService.updateDefinition(workspaceSlug, definitionId, payload);
    runInAction(() => {
      this.definitions = { ...this.definitions, [definition.id]: definition };
    });
    return definition;
  };

  deleteDefinition = async (workspaceSlug: string, definitionId: string) => {
    await this.agentService.deleteDefinition(workspaceSlug, definitionId);
    runInAction(() => {
      this.definitions = Object.fromEntries(Object.entries(this.definitions).filter(([id]) => id !== definitionId));
    });
  };

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

  addMember = async (workspaceSlug: string, projectId: string, definitionId: string) => {
    const member = await this.agentService.addMember(workspaceSlug, projectId, definitionId);
    runInAction(() => {
      this.members = { ...this.members, [member.id]: member };
    });
    return member;
  };

  updateMember = async (
    workspaceSlug: string,
    projectId: string,
    memberId: string,
    payload: { is_active: boolean }
  ) => {
    const member = await this.agentService.updateMember(workspaceSlug, projectId, memberId, payload);
    runInAction(() => {
      this.members = { ...this.members, [member.id]: member };
    });
    return member;
  };

  removeMember = async (workspaceSlug: string, projectId: string, memberId: string) => {
    await this.agentService.removeMember(workspaceSlug, projectId, memberId);
    runInAction(() => {
      this.members = Object.fromEntries(Object.entries(this.members).filter(([id]) => id !== memberId));
    });
  };

  wake = async (workspaceSlug: string, projectId: string, issueId: string, memberId: string) => {
    // No store copy of runs in phase 1: the caller only needs the run it just
    // created (to show a toast / follow it), and the run list is not editable here.
    return this.agentService.createRun(workspaceSlug, projectId, { member_id: memberId, issue_id: issueId });
  };
}
