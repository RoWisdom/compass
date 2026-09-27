/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { EModalWidth, Input, ModalCore } from "@plane/ui";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
// services
import type { TPageCollection } from "@/services/page";

/** 与后端 `apps/api/plane/app/serializers/page_collection.py` 的 `MAX_NAME_LENGTH` 同口径。 */
const MAX_NAME_LENGTH = 255;

type Props = {
  isOpen: boolean;
  /** `null` = 新建模式；有值 = 重命名这个集合。 */
  collection: TPageCollection | null;
  handleClose: () => void;
  /** 新建成功后的回调，参数是新集合 —— 侧栏靠它跳过去。 */
  onCreated?: (collection: TPageCollection) => void;
};

/**
 * 集合的新建 / 重命名弹窗。一个组件两种模式，按 `collection` 是否为 null 分叉。
 *
 * **归属：挂在 `wiki/sidebar.tsx` 自己身上，不要提到 layout 层。**
 * 这与 `wiki-include-modal-context.tsx` 的结论**不同，且理由不同**：
 * 那个弹窗必须提到 `wiki/layout.tsx`，是因为有**两个互不相邻的调用方**
 * （顶栏按钮 + 空状态的收录按钮）都要开它，需要一个共享的 provider；
 * 而本弹窗只有侧栏一个调用方（`＋` 与行内 `⋯` 都在侧栏里），侧栏又是单实例，
 * 一份本地 state 就够。**别照着那个文件的结论把这个也提上去。**
 */
export const CollectionFormModal = observer(function CollectionFormModal(props: Props) {
  const { isOpen, collection, handleClose: onClose, onCreated } = props;
  // router
  const { workspaceSlug } = useParams();
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { createCollection, updateCollection } = usePageStore(EPageStoreType.WORKSPACE);
  // derived values
  const isEdit = !!collection;
  // state
  const [name, setName] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  /**
   * 关闭弹窗的**唯一入口**，提交在飞时是 no-op。
   *
   * 不守这一下的后果是真的：一次网络往返足够用户按 Esc／点遮罩／点取消把弹窗关掉，
   * 而迟到的响应回来照样弹「已创建」，并把侧栏 `onCreated` 的 `router.push` 走掉 ——
   * 用户已经明确取消，却还是被导航到了新集合。
   *
   * 守卫必须落在这里、而不是只 `disabled` 取消按钮：`ModalCore` 接的也是这个 prop，
   * Esc 与点遮罩是同一扇门的另外两条路径。
   */
  const handleClose = () => {
    if (isSubmitting) return;
    onClose();
  };

  // 每次打开都从目标对象重置 —— 否则「改名 A 后关掉、再打开 B」会带着 A 的残值。
  useEffect(() => {
    if (isOpen) setName(collection?.name ?? "");
  }, [isOpen, collection]);

  const trimmedName = name.trim();
  const isValid = trimmedName.length > 0 && trimmedName.length <= MAX_NAME_LENGTH;

  const handleSubmit = async () => {
    if (!workspaceSlug || !isValid || isSubmitting) return;
    setIsSubmitting(true);
    let created: TPageCollection | undefined;
    try {
      if (isEdit) {
        await updateCollection(workspaceSlug, collection.id, trimmedName);
        setToast({ type: TOAST_TYPE.SUCCESS, title: t("wiki_collections.toasts.renamed") });
      } else {
        // 只在这里**捕获**新集合，`onCreated` 留到 try 之后调 —— 见下。
        created = await createCollection(workspaceSlug, trimmedName);
        setToast({ type: TOAST_TYPE.SUCCESS, title: t("wiki_collections.toasts.created") });
      }
    } catch {
      // store 里两个 action 都不吞异常，所以失败一定落到这里 —— toast 的成败由它分叉。
      setToast({
        type: TOAST_TYPE.ERROR,
        title: isEdit ? t("wiki_collections.toasts.rename_error") : t("wiki_collections.toasts.create_error"),
      });
      return;
    } finally {
      setIsSubmitting(false);
    }

    // 成功路径整个移出上面的 try。写入这时**已经落库**，下面两下都不是请求：
    // 侧栏的 `onCreated` 会 `router.push`，它抛错若落进上面的 catch，用户就会看到一个
    // 「创建失败」toast、外加一个已经建好的集合（`PageCollection.name` 无唯一约束，
    // 重试即重复建立）。
    //
    // `handleClose` 因此必须排在 `onCreated` **之前**：它是**点击那一帧**的闭包，
    // 那一帧 `isSubmitting` 为 false，所以不会被为 I-2 加的守卫挡住 —— 守卫拦的是
    // 「请求在飞时用户主动取消」，此刻请求早已结束。而若把关闭排在 `onCreated` 之后，
    // 一旦 `router.push` 抛错，`finally` 早已把 `isSubmitting` 复位，弹窗就会关不掉、
    // 停在原地且还能再次提交；先关再回调也让导航发生前弹窗就已消失，本就是更好的终态。
    // **不要**给 `onCreated` 套 try/catch —— 吞掉导航错误比让它暴露更糟。
    handleClose();
    if (created) onCreated?.(created);
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={handleClose} width={EModalWidth.LG}>
      <div className="flex flex-col gap-4 p-5">
        <h3 className="text-16 font-medium">
          {isEdit ? t("wiki_collections.edit_modal.title") : t("wiki_collections.create_modal.title")}
        </h3>
        <Input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder={
            isEdit
              ? t("wiki_collections.form.name_placeholder_edit")
              : t("wiki_collections.form.name_placeholder_create")
          }
          maxLength={MAX_NAME_LENGTH}
        />
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="lg" onClick={handleClose} disabled={isSubmitting}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" size="lg" onClick={handleSubmit} loading={isSubmitting} disabled={!isValid}>
            {isEdit ? t("common.save") : t("wiki_collections.create_modal.submit")}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
