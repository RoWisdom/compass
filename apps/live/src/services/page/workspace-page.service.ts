/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { AppError } from "@/lib/errors";
import { PageService } from "./extended.service";

interface WorkspacePageServiceParams {
  workspaceSlug: string | null;
  cookie: string | null;
  [key: string]: unknown;
}

/**
 * Wiki (/wiki-pages/) pages.
 *
 * Two deliberate differences from `ProjectPageService`:
 *
 * 1. **No projectId.** Wiki pages are workspace-scoped: the wiki viewset
 *    filters on `is_global=True` and never on a project, and a page may have
 *    no ProjectPage row at all (`Page.projects` is an M2M through
 *    ProjectPage). ProjectPageService throws when projectId is missing, so
 *    reusing it here would reject connections the backend serves fine.
 *
 * 2. **No `pages/` segment.** The wiki routes are
 *    `/api/workspaces/<slug>/wiki-pages/<id>/`, so `pageUrl` overrides the
 *    project default (`${basePath}/pages/${pageId}`) and drops the segment.
 */
export class WorkspacePageService extends PageService {
  protected basePath: string;

  constructor(params: WorkspacePageServiceParams) {
    super();
    const { workspaceSlug } = params;
    if (!workspaceSlug) throw new AppError("Missing required fields.");
    // validate cookie
    if (!params.cookie) throw new AppError("Cookie is required.");
    // set cookie
    this.setHeader("Cookie", params.cookie);
    // set base path
    this.basePath = `/api/workspaces/${workspaceSlug}/wiki-pages`;
  }

  protected pageUrl(pageId: string): string {
    return `${this.basePath}/${pageId}`;
  }
}
