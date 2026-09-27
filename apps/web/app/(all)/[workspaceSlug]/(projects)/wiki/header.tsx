/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { useParams, useSearchParams } from "next/navigation";
import { useTranslation } from "@plane/i18n";
import { PageIcon } from "@plane/propel/icons";
import { Header } from "@plane/ui";
// helpers
import { BreadcrumbLink } from "@/components/common/breadcrumb-link";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
import { useWorkspace } from "@/hooks/store/use-workspace";

export const WikiHeader = observer(function WikiHeader() {
  // router
  const { workspaceSlug } = useParams();
  const searchParams = useSearchParams();
  const activeCollection = searchParams.get("collection") ?? "general";
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { currentWorkspace } = useWorkspace();
  const { predefined, collections } = usePageStore(EPageStoreType.WORKSPACE);

  // 分区显示名：预置分区走 i18n；自定义集合走 store 里的 name；
  // 集合列表还没拉回来时退回 `fallback_name`（「集合」），不显示裸 uuid。
  // 这里**不重复发请求** —— 侧栏已经 fetchCollections，mobx 会让本组件跟着重渲染。
  const isPredefined = predefined.some((item) => item.key === activeCollection);
  const collectionLabel = isPredefined
    ? t(`wiki_collections.predefined.${activeCollection}`)
    : (collections.find((collection) => collection.id === activeCollection)?.name ??
      t("wiki_collections.fallback_name"));

  return (
    <Header>
      <Header.LeftItem>
        <BreadcrumbLink
          label={`${currentWorkspace?.name ?? ""} / ${collectionLabel}`}
          href={`/${workspaceSlug}/wiki/`}
          icon={<PageIcon className="h-4 w-4 text-tertiary" />}
          isLast
        />
      </Header.LeftItem>
    </Header>
  );
});
