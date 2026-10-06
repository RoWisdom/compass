/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo } from "react";
import { observer } from "mobx-react";
import { useParams, useSearchParams } from "next/navigation";
// plane types
import { PageIcon } from "@plane/propel/icons";
// plane ui
import { Breadcrumbs, Header } from "@plane/ui";
import { getPageName } from "@plane/utils";
// helpers
import { BreadcrumbLink } from "@/components/common/breadcrumb-link";
// hooks
import { useProject } from "@/hooks/store/use-project";
// plane web imports
import { CommonProjectBreadcrumbs } from "@/components/breadcrumbs/common";
import { ProjectCreateMenu } from "@/components/pages/project-create-menu";
import { EPageStoreType, usePageStore } from "@/hooks/store";
// services
import { PAGE_NODE_TYPE_FOLDER } from "@/services/page";

export const PagesListHeader = observer(function PagesListHeader() {
  // router
  const { workspaceSlug, projectId } = useParams();
  const searchParams = useSearchParams();
  const pageType = searchParams.get("type");
  // store hooks
  const { currentProjectDetails, loader } = useProject();
  const { canCurrentUserCreatePage, pageParentIds, getPageById, getPageNodeType } = usePageStore(
    EPageStoreType.PROJECT
  );

  const folderId = searchParams.get("folder");
  const activeFolderId = folderId && getPageNodeType(folderId) === PAGE_NODE_TYPE_FOLDER ? folderId : null;

  /** 从当前文件夹往上走到根，得到 [最顶, …, 当前]。`depth` 封顶防环。 */
  const folderChain = useMemo(() => {
    if (!activeFolderId) return [] as string[];
    const chain: string[] = [];
    let cursor: string | null = activeFolderId;
    let depth = 0;
    while (cursor && depth < 32) {
      chain.unshift(cursor);
      cursor = pageParentIds[cursor] ?? null;
      depth += 1;
    }
    return chain;
  }, [activeFolderId, pageParentIds]);

  return (
    <Header>
      <Header.LeftItem>
        <Breadcrumbs isLoading={loader === "init-loader"}>
          <CommonProjectBreadcrumbs workspaceSlug={workspaceSlug?.toString()} projectId={projectId?.toString()} />
          <Breadcrumbs.Item
            component={
              <BreadcrumbLink
                label="Pages"
                href={`/${workspaceSlug}/projects/${currentProjectDetails?.id}/pages/`}
                icon={<PageIcon className="h-4 w-4 text-tertiary" />}
                isLast={!activeFolderId}
              />
            }
            isLast={!activeFolderId}
          />
          {folderChain.map((id, index) => {
            const isCurrent = index === folderChain.length - 1;
            return (
              <Breadcrumbs.Item
                key={id}
                component={
                  <BreadcrumbLink
                    label={getPageName(getPageById(id)?.name)}
                    href={`/${workspaceSlug}/projects/${currentProjectDetails?.id}/pages/?folder=${id}`}
                    isLast={isCurrent}
                  />
                }
                isLast={isCurrent}
              />
            );
          })}
        </Breadcrumbs>
      </Header.LeftItem>
      {canCurrentUserCreatePage && (
        <Header.RightItem>
          <ProjectCreateMenu pageType={pageType === "private" ? "private" : "public"} parentId={activeFolderId} />
        </Header.RightItem>
      )}
    </Header>
  );
});
