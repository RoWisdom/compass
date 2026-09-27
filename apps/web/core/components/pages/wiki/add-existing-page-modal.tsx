/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
import useSWR from "swr";
// plane imports
import { useTranslation } from "@plane/i18n";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { Button } from "@plane/propel/button";
import { PageIcon } from "@plane/propel/icons";
import { EModalWidth, Input, ModalCore } from "@plane/ui";
import { getPageName } from "@plane/utils";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
// services
import { isPredefinedCollectionKey } from "@/services/page";

type Props = {
  isOpen: boolean;
  collection: string;
  handleClose: () => void;
};

export const AddExistingPageModal = observer(function AddExistingPageModal(props: Props) {
  const { isOpen, collection, handleClose } = props;
  // states
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedPageIds, setSelectedPageIds] = useState<string[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  // router
  const { workspaceSlug } = useParams();
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { candidates, includePages, fetchCandidates, fetchPagesList } = usePageStore(EPageStoreType.WORKSPACE);

  // 候选 = 本工作区**未收录**的页面。收录是唯一入口（设计决策 #1），
  // 所以这里只能从已有页面里挑，不能在 Wiki 里凭空造。
  const { isLoading, mutate: revalidateCandidates } = useSWR(
    isOpen && workspaceSlug ? `WIKI_CANDIDATES_${workspaceSlug}` : null,
    isOpen && workspaceSlug ? () => fetchCandidates(workspaceSlug) : null
  );

  // 预置分区键（"general" 等）**不是 uuid**，原样传给后端会被 `collection_id` 的 UUIDField
  // 拒掉、整个请求 400（`apps/api/plane/app/serializers/page_collection.py:61`：
  // `collection_id = serializers.UUIDField(required=False, allow_null=True)`）。
  // 传 null 即「落回 general」—— 正是 general 分区的语义。其余预置分区走不到这里
  // （父组件已关掉它们的收录入口）。
  // **这个映射对「四个预置键 + 真 uuid」是全的，对任意字符串不是**：`?collection=<既非预置键、
  // 又非 uuid 的串>` 会原样送出去、被同一个 UUIDField 400。从 UI 走不到这种取值
  // （侧栏的 key 只来自预置键与已取回的集合 id），所以不做额外校验 —— 但别把这段注释读成
  // 「任何输入都安全」。
  const collectionId = isPredefinedCollectionKey(collection) ? null : collection;

  const query = searchQuery.trim().toLowerCase();
  const filteredCandidates = candidates.filter((page) => (page.name ?? "").toLowerCase().includes(query));

  const toggle = (pageId: string) =>
    setSelectedPageIds((current) =>
      current.includes(pageId) ? current.filter((id) => id !== pageId) : [...current, pageId]
    );

  const handleSubmit = async () => {
    if (!workspaceSlug || selectedPageIds.length === 0) return;
    setIsSubmitting(true);
    try {
      const { included } = await includePages(workspaceSlug, selectedPageIds, collectionId);
      // 收录改的是页面的 `is_global`，而列表数据在 store 的
      // `data`/`collectionPageIds` 里（`workspace-page.store.ts:170-205`），列表页的 SWR key
      // 只含分区、收录后不变 —— 不重拉的话，刚收录的页面要手动刷新才出现。
      // `collection` 就是当前分区键，正是 `fetchPagesList` 要的那个参数。
      //
      // 这两下都是**尽力而为的刷新，不 await、失败也不报错**：收录这时已经落库了，
      // 让刷新失败把它变成「Error!」toast 是撒谎。store 自己会把失败记进 `this.error`。
      fetchPagesList(workspaceSlug, collection).catch(() => {});
      // 候选缓存同理：不失效的话，重开弹窗还能勾到刚收录过的页面，后端会静默跳过
      // （`included` 小于勾选数）。必须在 `handleClose()` **之前**调用 —— 关掉之后
      // SWR key 变成 null，mutate 就没东西可刷了。
      revalidateCandidates();
      setToast({
        type: TOAST_TYPE.SUCCESS,
        // `{count}` 必须传：这个键是 ICU 带复数的串，不传就渲染出原始 ICU 文本。
        title: t("wiki_collections.add_existing_page_modal.success_message", { count: included }),
      });
      setSelectedPageIds([]);
      handleClose();
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: "Error!",
        message: t("wiki_collections.add_existing_page_modal.error_message"),
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={handleClose} width={EModalWidth.LG}>
      <div className="flex flex-col gap-4 p-5">
        <h3 className="text-16 font-medium">{t("wiki_collections.header.add_page")}</h3>
        <Input
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder={t("wiki_collections.add_existing_page_modal.search_placeholder")}
        />
        {/* 候选列表 + 勾选 */}
        <div className="max-h-80 overflow-y-auto">
          {isLoading && <p className="px-2 text-13 text-tertiary">{t("common.loading")}</p>}
          {!isLoading && filteredCandidates.length === 0 && (
            <p className="px-2 text-13 text-tertiary">
              {query
                ? t("wiki_collections.add_existing_page_modal.no_pages_found")
                : t("wiki_collections.add_existing_page_modal.no_pages_available")}
            </p>
          )}
          {filteredCandidates.map((page) => (
            <label
              key={page.id}
              className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 hover:bg-layer-1"
            >
              <input
                type="checkbox"
                checked={selectedPageIds.includes(page.id ?? "")}
                onChange={() => page.id && toggle(page.id)}
              />
              <PageIcon className="h-4 w-4 text-tertiary" />
              <span className="truncate text-13">{getPageName(page.name)}</span>
            </label>
          ))}
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="lg" onClick={handleClose}>
            {t("common.cancel")}
          </Button>
          <Button
            variant="primary"
            size="lg"
            onClick={handleSubmit}
            loading={isSubmitting}
            disabled={selectedPageIds.length === 0}
          >
            {t("wiki_collections.add_existing_page_modal.submit")}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
