/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { useParams, useRouter } from "next/navigation";
// plane imports
import { EPageAccess } from "@plane/constants";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { TPage, TPageNavigationTabs } from "@plane/types";
// hooks
import { useProject } from "@/hooks/store/use-project";
import { EPageStoreType, usePageStore } from "@/hooks/store";

/**
 * 「建一个无标题页面，然后跳进它的编辑器」—— 项目 Pages 列表里**两个**入口共用的动作。
 *
 * 存在意义是**去掉第二份**：这段逻辑原先逐字写在两个地方
 * （`(list)/header.tsx` 的「Add page」按钮、`pages-list-main-content.tsx` 的空态 CTA），
 * 本轮又多了第三个调用方（`＋` 下拉的「新建页面」项）。三份逐字重复的
 * `payload` / 跳转 / toast 就是三次抄错的余地。
 *
 * **行为逐字照搬**（`access` 由 `pageType` 推、跳去新页的编辑器、失败用原来那句英文
 * toast）—— 这是纯抽取，不改行为。那句 `"Error!"` / `"Page could not be created.
 * Please try again."` 是**上游写死的英文字面量**，不是 i18n 键；本轮不顺手改它
 * （改成别的键会让这三处的文案一起变，属于另一个决定）。
 */
export const useProjectPageCreate = (pageType: TPageNavigationTabs) => {
  // router
  const router = useRouter();
  const { workspaceSlug } = useParams();
  // store hooks
  const { currentProjectDetails } = useProject();
  const { createPage } = usePageStore(EPageStoreType.PROJECT);
  // states
  const [isCreatingPage, setIsCreatingPage] = useState(false);

  const createUntitledPage = async () => {
    setIsCreatingPage(true);

    const payload: Partial<TPage> = {
      access: pageType === "private" ? EPageAccess.PRIVATE : EPageAccess.PUBLIC,
    };

    await createPage(payload)
      // oxlint-disable-next-line promise/always-return
      .then((res) => {
        const pageId = `/${workspaceSlug}/projects/${currentProjectDetails?.id}/pages/${res?.id}`;
        router.push(pageId);
      })
      .catch((err) => {
        setToast({
          type: TOAST_TYPE.ERROR,
          title: "Error!",
          message: err?.data?.error || "Page could not be created. Please try again.",
        });
      })
      .finally(() => setIsCreatingPage(false));
  };

  return { createUntitledPage, isCreatingPage };
};
