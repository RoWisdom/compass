/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { useParams } from "next/navigation";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { PageIcon } from "@plane/propel/icons";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { EModalWidth, ModalCore } from "@plane/ui";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";

type Props = {
  isOpen: boolean;
  /** `null` 表示当前没有要移动的页面（弹窗只是被关着）。 */
  pageId: string | null;
  /** 当前分区：动作落库后要重拉的就是它。 */
  collection: string;
  handleClose: () => void;
};

/**
 * 「移到集合」的目标选择器。
 *
 * 目标 = `general` + 用户自建集合，**不列** `private`/`shared`/`archived`：
 * 那三个是**派生**分区（页面落在哪儿由 `access`/`archived_at` 决定），`collection_id` 指不过去 ——
 * 把它们列出来，选中只会把 `collection_id` 变成 `null`、页面落回 general，而 UI 在骗用户。
 * 这正是 `canIncludeIntoCollection` 编码的规则；这里按「只列合法目标」的方式实现它：
 * `general` 是唯一合法的预置目标（对应 `collection_id = null`），其余合法目标只有自建集合，
 * 所以列表由构造保证不会出现派生分区。
 */
export const MoveToCollectionModal = observer(function MoveToCollectionModal(props: Props) {
  const { isOpen, pageId, collection, handleClose } = props;
  // router
  const { workspaceSlug } = useParams();
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { collections, moveToCollection, fetchPagesList } = usePageStore(EPageStoreType.WORKSPACE);

  const targets: { id: string | null; label: string }[] = [
    // `collection_id = null` 即「落回 general」，与 `add-existing-page-modal.tsx` 的映射同一个道理。
    { id: null, label: t("wiki_collections.predefined.general") },
    ...collections.map((item) => ({ id: item.id, label: item.name })),
  ];

  const handleMove = async (targetId: string | null) => {
    if (!workspaceSlug || !pageId) return;
    try {
      await moveToCollection(workspaceSlug, pageId, targetId);
    } catch {
      // 失败提示复用现成键（「无法移动页面。请重试。」）—— 语义正确，不新增文案。
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("common.toast.error"),
        message: t("wiki_collections.add_existing_page_modal.error_message"),
      });
      return;
    }
    handleClose();
    // 换集合会改变当前分区的成员。与收录弹窗同款：**不 await、吞掉错误** ——
    // 动作这时已经落库，让刷新失败把它报成错误是撒谎（store 自己会把失败记进 `this.error`）。
    fetchPagesList(workspaceSlug, collection).catch(() => {});
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={handleClose} width={EModalWidth.LG}>
      <div className="flex flex-col gap-4 p-5">
        <h3 className="text-16 font-medium">{t("wiki_collections.menu.move_to_collection")}</h3>
        <div className="flex max-h-80 flex-col gap-1 overflow-y-auto">
          {targets.map((target) => (
            <button
              key={target.id ?? "general"}
              type="button"
              onClick={() => handleMove(target.id)}
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-13 hover:bg-layer-1"
            >
              <PageIcon className="h-4 w-4 text-tertiary" />
              <span className="truncate">{target.label}</span>
            </button>
          ))}
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="lg" onClick={handleClose}>
            {t("common.cancel")}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
