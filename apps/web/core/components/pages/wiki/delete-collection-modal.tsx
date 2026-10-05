/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { EModalWidth, ModalCore } from "@plane/ui";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";

type Props = {
  isOpen: boolean;
  /** `null` 表示当前没有要删的集合（弹窗只是被关着）。 */
  collectionId: string | null;
  /** 删除成功后收尾。分叉在调用方，理由同 `DeleteFolderModal.onDeleted`。 */
  onDeleted: () => void;
  handleClose: () => void;
};

/**
 * 删集合的二次确认。形状逐字照抄 `delete-folder-modal.tsx` —— 同一个交互问题
 * （「删了容器，里面的东西却没删」），同一套解法。
 *
 * **正文那句不是客套，是这个功能的地基**：这个操作唯一反直觉的地方就是
 * 「集合没了，里面的页面和文件夹却都在」—— 不说清楚，用户会以为自己在删整棵树。
 * 所以它必须说**移到哪**（常规），而不只是说「不会被删除」。
 *
 * 成功分支**不需要 toast**：侧栏刷新本身就是反馈（与删文件夹一致）；
 * 失败分支走 `toasts.delete_error`（19 语言的现成键，上游 SaaS 词汇表里本来就有）。
 */
export const DeleteCollectionModal = observer(function DeleteCollectionModal(props: Props) {
  const { isOpen, collectionId, onDeleted, handleClose } = props;
  // router
  const { workspaceSlug } = useParams();
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { deleteCollection } = usePageStore(EPageStoreType.WORKSPACE);
  // states
  const [isDeleting, setIsDeleting] = useState(false);

  const handleDelete = async () => {
    if (!workspaceSlug || !collectionId || isDeleting) return;
    setIsDeleting(true);
    try {
      await deleteCollection(workspaceSlug, collectionId);
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("common.toast.error"),
        message: t("wiki_collections.toasts.delete_error"),
      });
      setIsDeleting(false);
      return;
    }
    setIsDeleting(false);
    handleClose();
    onDeleted();
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={handleClose} width={EModalWidth.LG}>
      <div className="flex flex-col gap-4 p-5">
        <h3 className="text-16 font-medium">{t("wiki_collections.delete_modal.title")}</h3>
        <p className="text-13 text-secondary">{t("wiki_collections.delete_modal.float_description")}</p>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="lg" onClick={handleClose}>
            {t("common.cancel")}
          </Button>
          <Button variant="error-fill" size="lg" onClick={handleDelete} loading={isDeleting}>
            {t("wiki_collections.delete_modal.submit")}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
