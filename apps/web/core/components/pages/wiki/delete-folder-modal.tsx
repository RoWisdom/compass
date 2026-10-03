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
  /** `null` 表示当前没有要删的文件夹（弹窗只是被关着）。 */
  folderId: string | null;
  /** 删除成功后重拉**当前视图**。分叉在调用方，理由同 `MoveToModal.onMoved`。 */
  onDeleted: () => void;
  handleClose: () => void;
};

/**
 * 删文件夹的二次确认（Confluence Cloud：`••• → Delete` 之后还要再确认一次）。
 *
 * **正文那句不是客套，是这个功能的地基**：它唯一反直觉的地方就是「删了文件夹，
 * 里面的东西却没删」—— 不说清楚，用户会以为自己在删整棵树。
 */
export const DeleteFolderModal = observer(function DeleteFolderModal(props: Props) {
  const { isOpen, folderId, onDeleted, handleClose } = props;
  // router
  const { workspaceSlug } = useParams();
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { deleteFolder } = usePageStore(EPageStoreType.WORKSPACE);
  // states
  const [isDeleting, setIsDeleting] = useState(false);

  const handleDelete = async () => {
    if (!workspaceSlug || !folderId || isDeleting) return;
    setIsDeleting(true);
    try {
      await deleteFolder(workspaceSlug, folderId);
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("common.toast.error"),
        message: t("wiki_collections.toasts.delete_folder_error"),
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
        <h3 className="text-16 font-medium">{t("wiki_collections.delete_folder_modal.title")}</h3>
        <p className="text-13 text-secondary">{t("wiki_collections.delete_folder_modal.description")}</p>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="lg" onClick={handleClose}>
            {t("common.cancel")}
          </Button>
          <Button variant="error-fill" size="lg" onClick={handleDelete} loading={isDeleting}>
            {t("common.delete")}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
