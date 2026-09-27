/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import Link from "next/link";
import useSWR from "swr";
import { getButtonStyling } from "@plane/propel/button";
import { PageIcon } from "@plane/propel/icons";
import { cn, getPageName } from "@plane/utils";
// components
import { LogoSpinner } from "@/components/common/logo-spinner";
import { PageHead } from "@/components/core/page-title";
// hooks
import { EPageStoreType, usePage, usePageStore } from "@/hooks/store";
import type { Route } from "./+types/page";

const storeType = EPageStoreType.WORKSPACE;

function WikiPageDetail({ params }: Route.ComponentProps) {
  const { workspaceSlug, pageId } = params;
  // store hooks
  const { fetchPageDetails } = usePageStore(storeType);
  const page = usePage({ pageId, storeType });

  // 与项目详情页（`projects/(detail)/[projectId]/pages/(detail)/[pageId]/page.tsx:69`）同款：
  // 用 useSWR 而不是裸 useEffect。fetchPageDetails 失败时会 rethrow
  // （`workspace-page.store.ts:237`），裸 useEffect 里就是一个未捕获的 promise rejection，
  // 且页面永远停在 spinner。SWR 会接住它并给出 error。
  const { error: pageDetailsError } = useSWR(
    `WIKI_PAGE_DETAILS_${workspaceSlug}_${pageId}`,
    () => fetchPageDetails(workspaceSlug, pageId),
    { revalidateIfStale: true, revalidateOnFocus: true, revalidateOnReconnect: true }
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

  // 还没有页面（首次加载，详情还没回来）。这一支放在**上面那个分支之后**是有意的：
  // 反过来的话 TS 收窄不出下面 `page` 一定存在（`pageDetailsError && !page` 的否定是析取）。
  // 两者不可能同时成立（都要求 `!page`），所以换序不改变行为。
  if (!page)
    return (
      <div className="grid size-full place-items-center">
        <LogoSpinner />
      </div>
    );

  const { name, description_html } = page;
  const pageTitle = getPageName(name);

  return (
    <>
      <PageHead title={pageTitle} />
      <div className="relative flex h-full w-full flex-col overflow-hidden">
        <div className="flex items-center gap-2 border-b border-subtle px-6 py-4">
          <PageIcon className="h-4 w-4 text-tertiary" />
          <h3 className="text-16 font-semibold">{pageTitle}</h3>
        </div>
        <div className="h-full w-full overflow-y-auto px-6 py-5">
          {description_html && (
            // 服务端已消毒（nh3 / validate_html_content），此处只负责渲染
            <div className="prose-sm max-w-none prose" dangerouslySetInnerHTML={{ __html: description_html }} />
          )}
        </div>
      </div>
    </>
  );
}

export default observer(WikiPageDetail);
