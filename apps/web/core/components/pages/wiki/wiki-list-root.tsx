/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// components
import { ListLayout } from "@/components/core/list";
import { PageListBlock } from "@/components/pages/list/block";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";

type Props = {
  collection: string;
};

export const WikiListRoot = observer(function WikiListRoot(props: Props) {
  const { collection } = props;
  // store hooks
  const { getFilteredPageIdsByCollection } = usePageStore(EPageStoreType.WORKSPACE);
  // derived values
  const filteredPageIds = getFilteredPageIdsByCollection(collection);

  if (!filteredPageIds) return <></>;
  return (
    <ListLayout>
      {filteredPageIds.map((pageId) => (
        <PageListBlock key={pageId} pageId={pageId} storeType={EPageStoreType.WORKSPACE} />
      ))}
    </ListLayout>
  );
});
