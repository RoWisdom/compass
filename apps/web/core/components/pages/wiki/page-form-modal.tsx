/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
import { X } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { TPage } from "@plane/types";
import { EModalWidth, Input, ModalCore } from "@plane/ui";
// components
import { ProjectDropdown } from "@/components/dropdowns/project/dropdown";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
import { useAppRouter } from "@/hooks/use-app-router";
// services
import type { TPageCreateTarget } from "@/services/page";

type Props = {
  isOpen: boolean;
  handleClose: () => void;
  /**
   * 新页面落到哪个分区 —— **由侧栏按当前分区推导好传进来**（设计 §3.2d 的表）。
   * 弹窗不自己推一遍：推导规则只有一份，就在 `sidebar.tsx` 里。
   */
  target: TPageCreateTarget;
};

/**
 * 在 Wiki 里新建页面的弹窗。**名称与项目都可选** —— 空名下建出来的是「未命名」页
 * （`wiki_collections.list.untitled` 是现成文案），不选项目就是"无项目页"。
 *
 * **归属**：与 `CollectionFormModal` 一样挂在 `wiki/sidebar.tsx` 自己身上，不提到
 * layout 层 —— 只有侧栏一个调用方，侧栏又是单实例，一份本地 state 就够。
 */
export const PageFormModal = observer(function PageFormModal(props: Props) {
  const { isOpen, handleClose: onClose, target } = props;
  // router
  const router = useAppRouter();
  const { workspaceSlug } = useParams();
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { createPage } = usePageStore(EPageStoreType.WORKSPACE);
  // state
  const [name, setName] = useState("");
  const [projectId, setProjectId] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  /**
   * 关闭弹窗的**唯一入口**，提交在飞时是 no-op —— 与 `collection-form-modal.tsx`
   * 同一形状、同一理由：一次网络往返足够用户按 Esc／点遮罩／点取消把弹窗关掉，
   * 而迟到的响应回来照样会把 `router.push` 走掉，把已经明确取消的用户导航到新页面。
   *
   * 守卫必须落在这里、而不是只 `disabled` 取消按钮：`ModalCore` 接的也是这个 prop，
   * Esc 与点遮罩是同一扇门的另外两条路径。
   */
  const handleClose = () => {
    if (isSubmitting) return;
    onClose();
  };

  // 每次打开都清空 —— 否则"建了一个后关掉、再打开"会带着上一次的名字与项目。
  useEffect(() => {
    if (isOpen) {
      setName("");
      setProjectId(null);
    }
  }, [isOpen]);

  const handleSubmit = async () => {
    if (!workspaceSlug || isSubmitting) return;
    setIsSubmitting(true);

    let created: TPage;
    try {
      created = await createPage(workspaceSlug, {
        // 空名字**照原样传**：后端允许空串，空名页在官方语义里是合法的「未命名」页。
        // 所以这里没有 `isValid` 门禁 —— 提交按钮永远可点。
        name: name.trim(),
        project_id: projectId,
        collection_id: target.collection_id,
        access: target.access,
      });
    } catch {
      // store 的 `createPage` 不吞异常，所以失败一定落到这里 —— toast 的成败由它分叉。
      // 文案分叉与官方那两个键的语义一致：落集合时失败，要说清「页面或集合归属」都可能没成。
      setToast({
        type: TOAST_TYPE.ERROR,
        title: target.collection_id
          ? t("wiki_collections.toasts.create_page_in_collection_error")
          : t("wiki_collections.toasts.create_page_error"),
      });
      return;
    } finally {
      setIsSubmitting(false);
    }

    // 成功路径整个移出上面的 try。建页这时**已经落库**，下面两下都不是请求：
    // `router.push` 若抛错落进上面的 catch，用户就会看到一个「创建失败」toast、
    // 外加一个已经建好的页面，弹窗也留在原地不关。
    //
    // `handleClose` 必须排在导航**之前**：它是**点击那一帧**的闭包，那一帧
    // `isSubmitting` 为 false，不会被为守卫挡住；而若排在导航之后，一旦 `router.push`
    // 抛错，`finally` 早已把 `isSubmitting` 复位，弹窗就会关不掉、停在原地且还能再次提交
    // （Round A 的 M7-2）。
    //
    // **不弹成功 toast**：`wiki_collections.toasts.*` 下没有「页面已创建」的键 ——
    // 官方的规格是建页成功就**打开它**。照着做既省一个键，也少一次打扰。
    handleClose();
    router.push(`/${workspaceSlug}/wiki/${created.id}`);
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={handleClose} width={EModalWidth.LG}>
      <div className="flex flex-col gap-4 p-5">
        <h3 className="text-16 font-medium">{t("wiki_collections.menu.create_new_page")}</h3>

        <Input value={name} onChange={(event) => setName(event.target.value)} placeholder={t("common.name")} />

        {/*
          项目可选。给了就挂到该项目下，并因此获得一个 vault 落点。

          清除控件是**必需的，不是装饰**：`ProjectDropdown` 的单选变体
          `onChange: (val: string) => void` **回不出 `null`**（`dropdowns/project/base.tsx:40-42`），
          而 `value` 的类型是 `string | null` —— 它能**显示**「未选」，但选过一次之后就
          再也回不到未选。不处理的话「可选项目」会退化成「一旦选了就改不掉」。
          所以 ✕ 自己把本地 state 置回 `null`；`value` 收 `null` 时会回落到占位符
          （`getDisplayName`，`base.tsx:142-147`），这条路走得通。
          **不动共享组件** —— 它被项目页用着。

          `placeholder` 显式传 `common.project`：共享组件的默认值是硬编码英文
          `"Project"`（`base.tsx:67`），那是个波及项目页的既有 i18n 缺口，本轮只在本弹窗里绕开。
        */}
        <div className="flex items-center gap-2">
          <ProjectDropdown
            value={projectId}
            onChange={(val: string) => setProjectId(val)}
            multiple={false}
            buttonVariant="border-with-text"
            placeholder={t("common.project")}
          />
          {projectId && (
            <button
              type="button"
              onClick={() => setProjectId(null)}
              aria-label={t("common.clear")}
              className="rounded-sm p-0.5 text-tertiary hover:bg-layer-1 hover:text-secondary"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>

        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="lg" onClick={handleClose} disabled={isSubmitting}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" size="lg" onClick={handleSubmit} loading={isSubmitting}>
            {t("wiki_collections.menu.create_new_page")}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
