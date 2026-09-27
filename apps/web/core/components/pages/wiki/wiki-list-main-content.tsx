/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { EmptyStateDetailed } from "@plane/propel/empty-state";
// components
import { PageLoader } from "@/components/pages/loaders/page-loader";
import { AddExistingPageModal } from "@/components/pages/wiki/add-existing-page-modal";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
import { useUserPermissions } from "@/hooks/store/user";

type Props = {
  collection: string;
  children: React.ReactNode;
};

export const WikiListMainContent = observer(function WikiListMainContent(props: Props) {
  const { collection, children } = props;
  // states
  const [isAddExistingModalOpen, setIsAddExistingModalOpen] = useState(false);
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { isAnyPageAvailable, getFilteredPageIdsByCollection, loader } = usePageStore(EPageStoreType.WORKSPACE);
  const { allowPermissions } = useUserPermissions();
  // derived values
  const filteredPageIds = getFilteredPageIdsByCollection(collection);
  const canIncludePages = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
    EUserPermissionsLevel.WORKSPACE
  );

  if (loader === "init-loader") return <PageLoader />;

  if (!isAnyPageAvailable)
    return (
      <>
        <EmptyStateDetailed
          assetKey="page"
          title={t("project_empty_state.pages.title")}
          description={t("project_empty_state.pages.description")}
          actions={[
            {
              label: t("wiki_collections.menu.add_existing_page"),
              onClick: () => setIsAddExistingModalOpen(true),
              variant: "primary",
              disabled: !canIncludePages,
            },
          ]}
        />
        <AddExistingPageModal
          isOpen={isAddExistingModalOpen}
          collection={collection}
          handleClose={() => setIsAddExistingModalOpen(false)}
        />
      </>
    );

  if (filteredPageIds?.length === 0)
    return (
      <EmptyStateDetailed
        assetKey="search"
        title={t("common_empty_state.search.title")}
        description={t("common_empty_state.search.description")}
      />
    );

  return <div className="h-full w-full overflow-hidden">{children}</div>;
});
