/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { ReactNode } from "react";
import { observer } from "mobx-react";
import { useParams, useSearchParams } from "next/navigation";
import useSWR from "swr";
import { useTranslation } from "@plane/i18n";
import { PageIcon } from "@plane/propel/icons";
import { cn } from "@plane/utils";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
import { useAppRouter } from "@/hooks/use-app-router";

export const WikiSidebar = observer(function WikiSidebar() {
  // router
  const router = useAppRouter();
  const { workspaceSlug } = useParams();
  const searchParams = useSearchParams();
  const activeCollection = searchParams.get("collection") ?? "general";
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { predefined, collections, fetchCollections } = usePageStore(EPageStoreType.WORKSPACE);

  // 集合列表（含计数）
  useSWR(
    workspaceSlug ? `WIKI_COLLECTIONS_${workspaceSlug}` : null,
    workspaceSlug ? () => fetchCollections(workspaceSlug) : null
  );

  const goTo = (key: string) => router.push(`/${workspaceSlug}/wiki/?collection=${key}`);

  const renderRow = (key: string, label: string, count: number, icon: ReactNode) => (
    <button
      key={key}
      type="button"
      onClick={() => goTo(key)}
      className={cn(
        "flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-13",
        activeCollection === key ? "bg-layer-1 text-primary" : "text-secondary hover:bg-layer-1/50"
      )}
    >
      <span className="flex items-center gap-2 truncate">
        {icon}
        <span className="truncate">{label}</span>
      </span>
      <span className="text-11 text-tertiary">{count}</span>
    </button>
  );

  return (
    <div className="flex h-full w-full flex-col gap-1 overflow-y-auto p-2">
      {predefined
        .filter((item) => item.key !== "shared" || item.page_count > 0)
        .map((item) =>
          renderRow(
            item.key,
            t(`wiki_collections.predefined.${item.key}`),
            item.page_count,
            <PageIcon className="h-4 w-4 text-tertiary" />
          )
        )}
      {collections.map((collection) =>
        renderRow(collection.id, collection.name, collection.page_count, <PageIcon className="h-4 w-4 text-tertiary" />)
      )}
    </div>
  );
});
