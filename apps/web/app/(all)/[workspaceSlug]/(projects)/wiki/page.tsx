/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { useSearchParams } from "next/navigation";
import useSWR from "swr";
// components
import { PageHead } from "@/components/core/page-title";
import { WikiListMainContent } from "@/components/pages/wiki/wiki-list-main-content";
import { WikiListRoot } from "@/components/pages/wiki/wiki-list-root";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
import type { Route } from "./+types/page";

function WikiPage({ params }: Route.ComponentProps) {
  const { workspaceSlug } = params;
  const searchParams = useSearchParams();
  const collection = searchParams.get("collection") ?? "general";

  return (
    <>
      <PageHead title="Wiki" />
      <WikiListView workspaceSlug={workspaceSlug} collection={collection} />
    </>
  );
}

const WikiListView = observer(function WikiListView(props: { workspaceSlug: string; collection: string }) {
  const { workspaceSlug, collection } = props;
  // store hooks
  const { fetchPagesList } = usePageStore(EPageStoreType.WORKSPACE);

  useSWR(
    workspaceSlug && collection ? `WORKSPACE_PAGES_${workspaceSlug}_${collection}` : null,
    workspaceSlug && collection ? () => fetchPagesList(workspaceSlug, collection) : null
  );

  return (
    <div className="relative flex h-full w-full flex-col overflow-hidden">
      <WikiListMainContent collection={collection}>
        <WikiListRoot collection={collection} />
      </WikiListMainContent>
    </div>
  );
});

export default WikiPage;
