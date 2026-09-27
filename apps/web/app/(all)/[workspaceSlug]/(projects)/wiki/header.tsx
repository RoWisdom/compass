/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { useParams } from "next/navigation";
import { useTranslation } from "@plane/i18n";
import { PageIcon } from "@plane/propel/icons";
import { Header } from "@plane/ui";
// helpers
import { BreadcrumbLink } from "@/components/common/breadcrumb-link";
// hooks
import { useWorkspace } from "@/hooks/store/use-workspace";

export const WikiHeader = observer(function WikiHeader() {
  // router
  const { workspaceSlug } = useParams();
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { currentWorkspace } = useWorkspace();

  return (
    <Header>
      <Header.LeftItem>
        <BreadcrumbLink
          // 工作区名作一级，Wiki 作末级。不做 switcher —— Phase 1 没有第二个工作区入口。
          label={`${currentWorkspace?.name ?? ""} / ${t("wiki_collections.predefined.general")}`}
          href={`/${workspaceSlug}/wiki/`}
          icon={<PageIcon className="h-4 w-4 text-tertiary" />}
          isLast
        />
      </Header.LeftItem>
    </Header>
  );
});
