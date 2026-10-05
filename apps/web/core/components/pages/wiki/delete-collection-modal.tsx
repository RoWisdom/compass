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
 * 删集合的二次确认。形状与 `delete-folder-modal.tsx` 同源 —— 同一个交互问题
 * （「删了容器，里面的东西怎么办」），同一套解法。
 *
 * **正文那句不是客套，是这个功能的地基**：它必须把「连页面一起删掉、且没有后悔药」
 * 说清楚。Round I 起语义就是**连删**（设计 §4.2），所以复用 Round H 时就写好、
 * 却一直没用上的两把键 `delete_with_pages_title` / `delete_with_pages_description`
 * —— 19 个语言里都已经有译文，**零新翻译**。
 *
 * 「Permanently」/「永久」与后端其实是**软删**（`deleted_at`）看起来矛盾 —— 不是
 * 写错：Wiki 侧**没有任何恢复入口**（URL / view / service / store 里都没有 restore，
 * `ProjectPageService.restore` 是项目页归档用的另一回事）。对**用户**而言它确实
 * 无法撤销，文案就该说无法撤销。不要把它改成「可恢复」来「对齐实现」。
 *
 * 成功分支**不需要 toast**：侧栏刷新本身就是反馈（与删文件夹一致）；
 * 失败分支走 `toasts.delete_error`（19 语言的现成键）。
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
        <h3 className="text-16 font-medium">{t("wiki_collections.delete_modal.delete_with_pages_title")}</h3>
        <p className="text-13 text-secondary">{t("wiki_collections.delete_modal.delete_with_pages_description")}</p>
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
