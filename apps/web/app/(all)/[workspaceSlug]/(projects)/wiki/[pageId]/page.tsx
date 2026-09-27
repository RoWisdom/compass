/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useEffect, useMemo } from "react";
import { observer } from "mobx-react";
import Link from "next/link";
import useSWR from "swr";
// plane types
import { getButtonStyling } from "@plane/propel/button";
import type { TSearchEntityRequestPayload, TWebhookConnectionQueryParams } from "@plane/types";
import { EFileAssetType } from "@plane/types";
// plane utils
import { cn } from "@plane/utils";
// components
import { LogoSpinner } from "@/components/common/logo-spinner";
import { PageHead } from "@/components/core/page-title";
import type { TPageRootConfig, TPageRootHandlers } from "@/components/pages/editor/page-root";
import { PageRoot } from "@/components/pages/editor/page-root";
// hooks
import { useEditorConfig } from "@/hooks/editor";
import { useEditorAsset } from "@/hooks/store/use-editor-asset";
import { useWorkspace } from "@/hooks/store/use-workspace";
import { useAppRouter } from "@/hooks/use-app-router";
// plane web hooks
import { EPageStoreType, usePage, usePageStore } from "@/hooks/store";
// plane web services
import { WorkspaceService } from "@/services/workspace.service";
// services
import { WorkspacePageService } from "@/services/page";
import type { Route } from "./+types/page";

const workspaceService = new WorkspaceService();
const workspacePageService = new WorkspacePageService();

const storeType = EPageStoreType.WORKSPACE;

function PageDetailsPage({ params }: Route.ComponentProps) {
  // router
  const router = useAppRouter();
  const { workspaceSlug, pageId } = params;
  // store hooks
  const { fetchPageDetails } = usePageStore(storeType);
  const page = usePage({ pageId, storeType });
  const { getWorkspaceBySlug } = useWorkspace();
  const { uploadEditorAsset, duplicateEditorAsset } = useEditorAsset();
  // derived values
  const workspaceId = workspaceSlug ? (getWorkspaceBySlug(workspaceSlug)?.id ?? "") : "";
  const { id, name, updateDescription } = page ?? {};

  // entity search handler —— 与项目页那版同形，只去掉 project_id：
  // wiki 页面是工作区级的，提及搜索不该被任何单个项目收窄。
  const fetchEntityCallback = useCallback(
    async (payload: TSearchEntityRequestPayload) => await workspaceService.searchEntity(workspaceSlug, payload),
    [workspaceSlug]
  );

  // editor config
  const { getEditorFileHandlers } = useEditorConfig();

  // 与项目详情页（`projects/(detail)/[projectId]/pages/(detail)/[pageId]/page.tsx:69`）同款：
  // 用 useSWR 而不是裸 useEffect。fetchPageDetails 失败时会 rethrow
  // （`workspace-page.store.ts`），裸 useEffect 里就是一个未捕获的 promise rejection，
  // 且页面永远停在 spinner。SWR 会接住它并给出 error。
  const { error: pageDetailsError } = useSWR(
    `WIKI_PAGE_DETAILS_${workspaceSlug}_${pageId}`,
    () => fetchPageDetails(workspaceSlug, pageId),
    { revalidateIfStale: true, revalidateOnFocus: true, revalidateOnReconnect: true }
  );

  // page root handlers
  const pageRootHandlers: TPageRootHandlers = useMemo(
    () => ({
      // `PageRoot` 不调用它，只是类型必填（page-root.tsx 只用
      // fetchDescriptionBinary / updateDescription / fetchEntity /
      // getRedirectionLink / 三个版本 handler）。留空实现而不是接一个真端点。
      create: async () => undefined,
      // 版本历史本阶段不攒（设计 §4）：三个 handler 传空实现 ⇒ 版本面板打开是空的，
      // 不报错。服务端也不写 PageVersion。
      fetchAllVersions: async () => undefined,
      fetchVersionDetails: async () => undefined,
      restoreVersion: async () => undefined,
      fetchDescriptionBinary: async () => {
        if (!id || !workspaceSlug) throw new Error("Missing required fields.");
        return await workspacePageService.fetchDescriptionBinary(workspaceSlug, id);
      },
      fetchEntity: fetchEntityCallback,
      getRedirectionLink: (targetPageId) =>
        targetPageId ? `/${workspaceSlug}/wiki/${targetPageId}` : `/${workspaceSlug}/wiki/`,
      updateDescription: updateDescription ?? (async () => {}),
    }),
    [fetchEntityCallback, id, updateDescription, workspaceSlug]
  );

  // page root config
  //
  // `projectId` 传 `undefined` 是有意的：资源上传在 `asset.store.ts:1728-1746`
  // 按 `if (projectId)` 分支，假值走 `uploadWorkspaceAsset` —— wiki 的资源属于
  // 工作区而不是任何项目。`getEditorFileHandlers` 的 `projectId` 本就是可选的。
  const pageRootConfig: TPageRootConfig = useMemo(
    () => ({
      fileHandler: getEditorFileHandlers({
        projectId: undefined,
        uploadFile: async (blockId, file) => {
          const { asset_id } = await uploadEditorAsset({
            blockId,
            data: {
              entity_identifier: id ?? "",
              entity_type: EFileAssetType.PAGE_DESCRIPTION,
            },
            file,
            projectId: undefined,
            workspaceSlug,
          });
          return asset_id;
        },
        duplicateFile: async (assetId: string) => {
          const { asset_id } = await duplicateEditorAsset({
            assetId,
            entityId: id,
            entityType: EFileAssetType.PAGE_DESCRIPTION,
            projectId: undefined,
            workspaceSlug,
          });
          return asset_id;
        },
        workspaceId,
        workspaceSlug,
      }),
    }),
    [getEditorFileHandlers, workspaceId, workspaceSlug, uploadEditorAsset, id, duplicateEditorAsset]
  );

  // 文档类型决定协同服务器的分派（`apps/live/src/services/page/handler.ts`）。
  // 这里**不放 `projectId`** —— wiki 页面不属于任何单个项目。
  const webhookConnectionParams: TWebhookConnectionQueryParams = useMemo(
    () => ({
      documentType: "workspace_page",
      workspaceSlug,
    }),
    [workspaceSlug]
  );

  useEffect(() => {
    if (page?.deleted_at && page?.id) {
      router.push(pageRootHandlers.getRedirectionLink());
    }
  }, [page?.deleted_at, page?.id, router, pageRootHandlers]);

  if ((!page || !id) && !pageDetailsError)
    return (
      <div className="grid size-full place-items-center">
        <LogoSpinner />
      </div>
    );

  // **此处有意不同于项目详情页先例**：那边把 error 与「不行」用 `||` 连
  // （`projects/(detail)/[projectId]/pages/(detail)/[pageId]/page.tsx:161` 的
  // `pageDetailsError || !canCurrentUserAccessPage`）。这里必须是 `&&`：
  // SWR **同时**保留 `error` 与 `data`，所以一次**载入成功后**的 focus / reconnect
  // 重验证失败，会把仍在 store 里、渲染得好好的页面翻成 "Page not found"。
  // 只有「err 且确实没有页面」才是真的 not-found。别把它「修回去」。
  if (pageDetailsError && !page)
    return (
      <div className="flex h-full w-full flex-col items-center justify-center">
        {/* 硬编码英文与项目详情页同款 —— 这一层（route 的 page.tsx）全仓库都不走 i18n
            （先例：`drafts/page.tsx:14` 的 "Workspace Draft"）。 */}
        <h3 className="text-center text-16 font-semibold">Page not found</h3>
        <p className="mt-3 text-center text-13 text-secondary">
          The page you are trying to access doesn{"'"}t exist or you don{"'"}t have permission to view it.
        </p>
        <Link href={`/${workspaceSlug}/wiki/`} className={cn(getButtonStyling("secondary", "base"), "mt-5")}>
          Back to Wiki
        </Link>
      </div>
    );

  if (!page) return null;

  return (
    <>
      <PageHead title={name} />
      <div className="flex h-full flex-col justify-between">
        <div className="relative flex h-full w-full flex-shrink-0 flex-col overflow-hidden">
          <PageRoot
            config={pageRootConfig}
            handlers={pageRootHandlers}
            storeType={storeType}
            page={page}
            webhookConnectionParams={webhookConnectionParams}
            workspaceSlug={workspaceSlug}
          />
        </div>
      </div>
    </>
  );
}

export default observer(PageDetailsPage);
