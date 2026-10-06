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

/** 与 `rename-folder-modal.tsx` 同值、同出处：UI 一致，不是后端口径。 */
const MAX_NAME_LENGTH = 255;

type Props = {
  isOpen: boolean;
  /** 建在谁下面。`null` = 顶层。 */
  parentId: string | null;
  handleClose: () => void;
};

/**
 * 在项目里新建一个文件夹（罗盘 Round J）。形状照 `rename-folder-modal.tsx` 同构，
 * 差别只有「初始值为空」和「调 `createFolder` 而不是 `renameFolder`」。
 *
 * **没有 `onCreated` 回调** —— 这一点**刻意**与 wiki 的两个弹窗不同。那边的弹窗必须
 * 让调用方重拉自己那个视图（`?collection=` 还是 `?folder=` 只有调用方知道）；
 * 而项目侧只有**一套**取数（`fetchPagesTree`），`ProjectPageStore.createFolder`
 * 自己已经 await 过它了。再多一层回调等于让调用方重复一次网络请求。
 *
 * 三条与 wiki 两个弹窗逐字同形的纪律：
 *   1. **异常不得吞掉** —— 弹窗靠「action 是否 reject」决定 toast 成败；
 *   2. **成功路径在 `try` 之外** —— 见 `delete-folder-modal.tsx` 的同款写法；
 *   3. **空名/超长禁用提交** —— 与 `rename-folder-modal.tsx` 的 `isValid` 同一套规则，
 *      不给文件夹开第二套。
 */
export const ProjectCreateFolderModal = observer(function ProjectCreateFolderModal(props: Props) {
  const { isOpen, parentId, handleClose } = props;
  // router
  const { workspaceSlug, projectId } = useParams();
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { createFolder } = usePageStore(EPageStoreType.PROJECT);
  // states
  const [name, setName] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  // 每次打开都清空（与 rename 那个「每次打开都重置」同一个坑的对偶：
  // 「建了 A、关掉、再打开」不该带着 A 的名字）。
  useEffect(() => {
    if (isOpen) setName("");
  }, [isOpen]);

  const trimmedName = name.trim();
  const isValid = trimmedName.length > 0 && trimmedName.length <= MAX_NAME_LENGTH;

  const handleSubmit = async () => {
    if (!workspaceSlug || !projectId || !isValid || isSubmitting) return;
    setIsSubmitting(true);
    try {
      await createFolder(workspaceSlug, projectId, trimmedName, parentId);
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("toast.error"),
        message: t("wiki_collections.toasts.create_folder_error"),
      });
      setIsSubmitting(false);
      return;
    }
    setIsSubmitting(false);
    handleClose();
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={handleClose} width={EModalWidth.LG}>
      <div className="flex flex-col gap-4 p-5">
        <h3 className="text-16 font-medium">{t("new_folder")}</h3>
        <Input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder={t("wiki_collections.rename_folder_modal.name_placeholder")}
          maxLength={MAX_NAME_LENGTH}
        />
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="lg" onClick={handleClose} disabled={isSubmitting}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" size="lg" onClick={handleSubmit} loading={isSubmitting} disabled={!isValid}>
            {t("create_folder")}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
