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
  const { candidates, includePages, fetchCandidates } = usePageStore(EPageStoreType.WORKSPACE);

  // 候选 = 本工作区**未收录**的页面。收录是唯一入口（设计决策 #1），
  // 所以这里只能从已有页面里挑，不能在 Wiki 里凭空造。
  const { isLoading } = useSWR(
    isOpen && workspaceSlug ? `WIKI_CANDIDATES_${workspaceSlug}` : null,
    isOpen && workspaceSlug ? () => fetchCandidates(workspaceSlug) : null
  );

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
      const { included } = await includePages(workspaceSlug, selectedPageIds, collection);
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
