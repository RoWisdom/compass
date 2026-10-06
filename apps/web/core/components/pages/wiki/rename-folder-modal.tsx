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
import type { EPageStoreType } from "@/hooks/store";
import { usePageStore } from "@/hooks/store";

type Props = {
  isOpen: boolean;
  /** `null` 表示当前没有要改名的文件夹（弹窗只是被关着）。 */
  folderId: string | null;
  /** 由调用方给：这个文件夹属于哪一棵树。**必填**，理由见 `folder-row-actions.tsx`。 */
  storeType: EPageStoreType;
  /** 改名成功后重拉**当前视图**。分叉在调用方，理由同 `MoveToModal.onMoved`。 */
  onRenamed: () => void;
  handleClose: () => void;
};

/**
 * 255 是**集合**改名那套 UI 规则的上限（`page_collection.py:35` 的 `MAX_NAME_LENGTH`；
 * `collection-form-modal.tsx` 自带一份同值常量）。**它不是本端点的口径** —— wiki page 的
 * PATCH 端点对 `name` 既不设 `max_length`、又允许空（`page_collection.py:150`），同一个文件
 * 里还有明文论证「加限制没有规格依据」（`:220-223`）。这里跟集合改名取齐，图的是 UI 一致，
 * 不是后端对齐。空名那一条同此理，见下面 docblock。
 */
const MAX_NAME_LENGTH = 255;

/**
 * 给文件夹改名的弹窗（Confluence Cloud：内容树里文件夹的 `•••` 菜单，与 Delete 同一处）。
 *
 * **形状照 `delete-folder-modal.tsx` 逐字同构** —— 同目录、同 props 形状、同 toast 写法、
 * 同「成功路径移出 try」的纪律。区别只有一处：确认换成了输入。
 *
 * **预填当前名，且每次打开都重置** —— 与 `collection-form-modal.tsx` 同一个坑：
 * 「改名 A 后关掉、再打开 B」会带着 A 的残值。
 *
 * 依赖数组写 `[isOpen, folderId, getPageById]` 而**不**写当前名：`getPageById` 是
 * `computedFn`（`workspace-page.store.ts:175`），身份稳定，所以这个 effect 只在
 * 「打开」与「换目标」时跑。把它的**返回值**放进依赖，会让树一刷新就冲掉用户正在输入的字。
 *
 * **名字没改就不打请求**：后端对此会 200 而什么都不变（`partial_update` 只在
 * `page.name != old_name` 时才推镜像路径），但一次白跑的 PATCH 会白跑一趟路径推导。
 *
 * **空名禁用提交**：跟随 `collection-form-modal.tsx:77` 的 `isValid`。后端其实允许
 * `blank`（`WikiPageUpdateSerializer.name` 是 `allow_blank=True`），这里在 UI 层收紧 ——
 * **与集合改名同一套规则**，不给文件夹开第二套。
 */
export const RenameFolderModal = observer(function RenameFolderModal(props: Props) {
  const { isOpen, folderId, onRenamed, handleClose, storeType } = props;
  // router
  const { workspaceSlug } = useParams();
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { getPageById, renameFolder } = usePageStore(storeType);
  // states
  const [name, setName] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  // 每次打开都从目标对象重置（见 docblock）。
  useEffect(() => {
    if (isOpen) setName(folderId ? (getPageById(folderId)?.name ?? "") : "");
  }, [isOpen, folderId, getPageById]);

  const trimmedName = name.trim();
  const isValid = trimmedName.length > 0 && trimmedName.length <= MAX_NAME_LENGTH;

  const handleSubmit = async () => {
    if (!workspaceSlug || !folderId || !isValid || isSubmitting) return;
    // 名字没变：不打请求，直接关（见 docblock）。**在提交这一刻现读**，不用 render 期间的
    // 派生值 —— 那个值进不了这里，也就不可能与树上最新的名字不一致。
    if (trimmedName === (getPageById(folderId)?.name ?? "")) {
      handleClose();
      return;
    }
    setIsSubmitting(true);
    try {
      await renameFolder(workspaceSlug, folderId, trimmedName);
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("common.toast.error"),
        message: t("wiki_collections.toasts.rename_folder_error"),
      });
      setIsSubmitting(false);
      return;
    }
    setIsSubmitting(false);
    handleClose();
    onRenamed();
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={handleClose} width={EModalWidth.LG}>
      <div className="flex flex-col gap-4 p-5">
        {/* 标题复用菜单项那一个键 —— 照 `move-to-modal.tsx:141` 复用 `menu.move_to` 的先例。 */}
        <h3 className="text-16 font-medium">{t("wiki_collections.menu.rename")}</h3>
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
            {/* `common.update` 而不是 `common.save`：后者只存在于 `common.json` 的**根**对象，
                `t("common.save")` 会原样渲染出 `common.save` 这个键名 —— 见
                `collection-form-modal.tsx:139-144` 记下的同一个坑。 */}
            {t("common.update")}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
