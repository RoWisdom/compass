/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { computed, makeObservable } from "mobx";
import { computedFn } from "mobx-utils";
// constants
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import type { TPage } from "@plane/types";
// services
import { WorkspacePageService } from "@/services/page";
// store
import type { RootStore } from "@/store/root.store";
import { BasePage } from "./base-page";
import type { TPageInstance } from "./base-page";

const workspacePageService = new WorkspacePageService();

export type TWorkspacePage = TPageInstance;

/** Phase 1 里 Wiki 不支持的页面动作。写成显式抛错，避免静默调到一个不存在的端点。 */
const unsupported = (action: string) => async (): Promise<never> => {
  throw new Error(`${action} is not supported for wiki pages in Phase 1`);
};

export class WorkspacePage extends BasePage implements TWorkspacePage {
  constructor(store: RootStore, page: TPage) {
    // 工作区页面只需要 workspaceSlug + pageId，不需要 projectId
    const { workspaceSlug } = store.router;
    const pageId = page.id;

    super(store, page, {
      update: async (payload) => {
        if (!workspaceSlug || !pageId) throw new Error("Missing required fields.");
        return await workspacePageService.update(workspaceSlug, pageId, payload);
      },
      updateDescription: async (document) => {
        if (!workspaceSlug || !pageId) throw new Error("Missing required fields.");
        await workspacePageService.updateDescription(workspaceSlug, pageId, document);
      },
      // 以下六个在 Phase 1 没有对应端点。权限 getter 一律返回 false，
      // 所以 UI 不会渲染出触发它们的入口；这里是最后一道防线。
      updateAccess: unsupported("Changing access"),
      lock: unsupported("Locking"),
      unlock: unsupported("Unlocking"),
      archive: unsupported("Archiving"),
      restore: unsupported("Restoring"),
      duplicate: unsupported("Duplicating"),
    });

    makeObservable(this, {
      canCurrentUserAccessPage: computed,
      canCurrentUserEditPage: computed,
      canCurrentUserDuplicatePage: computed,
      canCurrentUserLockPage: computed,
      canCurrentUserChangeAccess: computed,
      canCurrentUserArchivePage: computed,
      canCurrentUserDeletePage: computed,
      canCurrentUserFavoritePage: computed,
      canCurrentUserMovePage: computed,
      isContentEditable: computed,
    });
  }

  /**
   * @description 工作区成员都能看见本工作区的 Wiki 页面
   *（列表端点本身已按 workspace 过滤，不会返回别家的页面）
   */
  get canCurrentUserAccessPage() {
    return true;
  }

  /**
   * 工作区 ADMIN/MEMBER 都能编辑 —— 与后端写端点同一个口径。
   *
   * 后端把 wiki 的 create/partial_update/destroy 都收在
   * `@allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")`
   * （`views/page/collection.py`），而这里原本只看 `isCurrentUserOwner`。
   * 前端比后端窄会造出第三种状态：看得见编辑器、点不动、API 其实允许。
   * Wiki 是**工作区级**的东西，ADMIN/MEMBER 本就该能编辑 —— 用户 2026-09-27 裁定。
   *
   * 谓词与 `wiki/header.tsx` 的 `canIncludePages` 是**同一个**
   * （`allowPermissions([ADMIN, MEMBER], WORKSPACE)`），不要再写第二套。
   */
  get canCurrentUserEditPage() {
    return this.rootStore.user.permission.allowPermissions(
      [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
      EUserPermissionsLevel.WORKSPACE
    );
  }

  // 以下 getter 在 Phase 1 一律 false —— 它们在 store 层就屏蔽掉页面动作菜单
  // 里对应的项（见 components/pages/dropdowns/actions.tsx 的 shouldRender）。
  get canCurrentUserDuplicatePage() {
    return false;
  }

  get canCurrentUserLockPage() {
    return false;
  }

  get canCurrentUserChangeAccess() {
    return false;
  }

  get canCurrentUserArchivePage() {
    return false;
  }

  get canCurrentUserDeletePage() {
    return false;
  }

  get canCurrentUserFavoritePage() {
    return false;
  }

  get canCurrentUserMovePage() {
    return false;
  }

  /**
   * 编辑器是否可写。除角色外还要求页面没归档、没锁定。
   *
   * 原来末项是 `isCurrentUserOwner`，与 `canCurrentUserEditPage` 一起改成了
   * 工作区角色口径 —— 两者必须一致，否则 `PageRoot` 会把
   * `restoreEnabled`/`isContentEditable` 与工具栏的可写状态拧成两种说法。
   * 归档与锁定两个条件保留：后端 `WikiPageDescriptionViewSet.partial_update`
   * 对这两种状态分别回 PAGE_ARCHIVED / PAGE_LOCKED（400）。
   */
  get isContentEditable() {
    const canEdit = this.rootStore.user.permission.allowPermissions(
      [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
      EUserPermissionsLevel.WORKSPACE
    );
    return !this.archived_at && !this.is_locked && canEdit;
  }

  /** 工作区页面在 Wiki 里，不在项目路径下 —— 这就是 §5.1 说详情页必须做的原因。 */
  getRedirectionLink = computedFn(() => {
    const { workspaceSlug } = this.rootStore.router;
    return `/${workspaceSlug}/wiki/${this.id}`;
  });
}
