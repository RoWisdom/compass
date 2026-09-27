/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { useParams, useSearchParams } from "next/navigation";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { PageIcon } from "@plane/propel/icons";
import { Header } from "@plane/ui";
// helpers
import { BreadcrumbLink } from "@/components/common/breadcrumb-link";
import { useWikiIncludeModal } from "@/components/pages/wiki/wiki-include-modal-context";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
import { useWorkspace } from "@/hooks/store/use-workspace";
// services
import { canIncludeIntoCollection } from "@/services/page";

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
  // 收录弹窗归 `WikiIncludeModalProvider` 所有（挂在 `wiki/layout.tsx`）。
  // 这个按钮是**常驻**入口：列表的空态 CTA 只在分区为空时出现，分区一有页面就永久消失，
  // 收录在 UI 上就再也够不着了。
  const { open: openIncludeModal } = useWikiIncludeModal();

  // 唯一的门控：只有 `general` 与自建集合能接收收录（`private`/`shared`/`archived` 是派生分区，
  // 页面落在哪儿由 access/archived_at 决定，不由 collection_id 决定 —— 见
  // `canIncludeIntoCollection`）。这里**不渲染**按钮，而不是渲染成 disabled：
  // 一个不解释原因的灰按钮和没有入口一样糟。
  //
  // **刻意不加 ADMIN/MEMBER 门控**（空态 CTA 上的 `canIncludePages` 是它自己的门控，保持原样）：
  // 后端 `wiki-pages/` 的写端点允许 `[ADMIN, MEMBER, GUEST]`
  // （`apps/api/plane/app/views/page/collection.py` 的 create/partial_update/destroy），
  // 再叠一层前端门控会把 GUEST 本来做得了的动作藏起来。
  const canIncludeHere = canIncludeIntoCollection(activeCollection);

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

      <Header.RightItem>
        {/* 文案复用 `header.add_page`（弹窗标题用的同一个键，本来就是给顶栏准备的） */}
        {canIncludeHere && (
          <Button variant="primary" size="lg" onClick={openIncludeModal}>
            {t("wiki_collections.header.add_page")}
          </Button>
        )}
      </Header.RightItem>
    </Header>
  );
});
