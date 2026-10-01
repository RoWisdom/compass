/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { useParams, useSearchParams } from "next/navigation";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
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
import { useUserPermissions } from "@/hooks/store/user";
// services
import { canIncludeIntoCollection } from "@/services/page";

export const WikiHeader = observer(function WikiHeader() {
  // router
  const { workspaceSlug } = useParams();
  const searchParams = useSearchParams();
  // 与侧栏、与 `wiki/page.tsx` 用同一条分界：**`?collection` 缺省 = 首页**。
  // `explicitCollection` 单独留一份，因为下面两处（面包屑、右上按钮）判的是
  // 「在不在首页」；`activeCollection` 则继续给分区语义用（预置键判定、能否收录），
  // 它在首页上兜底成 `general` 只是为了让类型与既有分支不变，**不再代表用户在哪儿**。
  const explicitCollection = searchParams.get("collection");
  const activeCollection = explicitCollection ?? "general";
  const isHome = explicitCollection === null;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { currentWorkspace } = useWorkspace();
  const { predefined, collections } = usePageStore(EPageStoreType.WORKSPACE);
  const { allowPermissions } = useUserPermissions();
  // 收录弹窗归 `WikiIncludeModalProvider` 所有（挂在 `wiki/layout.tsx`）。
  // 这个按钮是**常驻**入口：列表的空态 CTA 只在分区为空时出现，分区一有页面就永久消失，
  // 收录在 UI 上就再也够不着了。
  const { open: openIncludeModal } = useWikiIncludeModal();

  // 两层门控，都要过：
  //
  // 1. **分区能不能收** —— 只有 `general` 与自建集合能接收收录（`private`/`shared`/`archived`
  //    是派生分区，页面落在哪儿由 access/archived_at 决定，不由 collection_id 决定 —— 见
  //    `canIncludeIntoCollection`）。
  // 2. **工作区角色** —— 与空态 CTA 用的是**同一个谓词**（`wiki-list-main-content.tsx` 的
  //    `canIncludePages`）。写端点只给 ADMIN/MEMBER（`apps/api/plane/app/views/page/
  //    collection.py` 的 create/partial_update/destroy），这是**用户 2026-09-27 的裁定**，
  //    有意收窄设计与计划原文里的 `[ADMIN, MEMBER, GUEST]`。后端收窄了而前端不收，
  //    就会留下一个「看得见、点得动、一点就 403」的按钮。
  //
  // 两层都用**不渲染**表达，而不是渲染成 disabled：一个不解释原因的灰按钮和没有入口一样糟。
  const canIncludeHere = canIncludeIntoCollection(activeCollection);
  const canIncludePages = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
    EUserPermissionsLevel.WORKSPACE
  );

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
          label={`${currentWorkspace?.name ?? ""} / ${isHome ? t("wiki_home.title") : collectionLabel}`}
          href={`/${workspaceSlug}/wiki/`}
          icon={<PageIcon className="h-4 w-4 text-tertiary" />}
          isLast
        />
      </Header.LeftItem>

      <Header.RightItem>
        {/*
          **首页上不渲染这颗按钮。** 它做的事是「把已有页面收录进当前分区」，
          而首页**没有当前分区** —— `activeCollection` 在这里只是 `general` 的兜底，
          点下去会把页面收录进「常规」，与用户以为所在的「首页」不是一回事。
          不渲染（而不是 disabled）与下面 `canIncludeHere` 那条的既有理由一致：
          一个不解释原因的灰按钮和没有入口一样糟。
        */}
        {/* 文案复用 `header.add_page`（弹窗标题用的同一个键，本来就是给顶栏准备的） */}
        {!isHome && canIncludeHere && canIncludePages && (
          <Button variant="primary" size="lg" onClick={openIncludeModal}>
            {t("wiki_collections.header.add_page")}
          </Button>
        )}
      </Header.RightItem>
    </Header>
  );
});
