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
  const { isOpen, collection, handleClose, onCreated } = props;
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

  // 每次打开都从目标对象重置 —— 否则「改名 A 后关掉、再打开 B」会带着 A 的残值。
  useEffect(() => {
    if (isOpen) setName(collection?.name ?? "");
  }, [isOpen, collection]);

  const trimmedName = name.trim();
  const isValid = trimmedName.length > 0 && trimmedName.length <= MAX_NAME_LENGTH;

  const handleSubmit = async () => {
    if (!workspaceSlug || !isValid || isSubmitting) return;
    setIsSubmitting(true);
    try {
      if (isEdit) {
        await updateCollection(workspaceSlug, collection.id, trimmedName);
        setToast({ type: TOAST_TYPE.SUCCESS, title: t("wiki_collections.toasts.renamed") });
      } else {
        const created = await createCollection(workspaceSlug, trimmedName);
        setToast({ type: TOAST_TYPE.SUCCESS, title: t("wiki_collections.toasts.created") });
        onCreated?.(created);
      }
      handleClose();
    } catch {
      // store 里两个 action 都不吞异常，所以失败一定落到这里 —— toast 的成败由它分叉。
      setToast({
        type: TOAST_TYPE.ERROR,
        title: isEdit ? t("wiki_collections.toasts.rename_error") : t("wiki_collections.toasts.create_error"),
      });
    } finally {
      setIsSubmitting(false);
    }
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
          <Button variant="secondary" size="lg" onClick={handleClose}>
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
