/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { Input, TextArea } from "@plane/ui";
// types
import type { TAgentGroup, TAgentGroupWrite } from "@/services/agent.service";
// hooks
import { useAgentStore } from "@/hooks/store/use-agent-store";
// local imports
import { DefinitionPicker } from "./definition-picker";

export type TGroupFormProps = {
  workspaceSlug: string;
  /** 传 undefined = 新建；传组 = 编辑。 */
  group?: TAgentGroup;
  onSuccess?: (group: TAgentGroup) => void;
  onCancel?: () => void;
};

type TFormState = {
  name: string;
  description: string;
  instructions: string;
  definitionIds: string[];
};

/**
 * 名册的初始值取服务端的**嵌套只读名册**（`group.definitions`），它只含**还活着的**岗位。
 *
 * 于是「组里留着一个已删岗位」（服务端会因此拒部署并 409 点名）在这份表单里看不见 ——
 * 而**保存一次就是那条 409 的修复路径**：送出去的是活岗位的完整集合，服务端拿「保存前的
 * through 行」求差，那个死岗位就被摘掉了。这是有意的，不是遗漏。
 */
const buildFormState = (group?: TAgentGroup): TFormState => ({
  name: group?.name ?? "",
  description: group?.description ?? "",
  instructions: group?.instructions ?? "",
  definitionIds: group?.definitions.map((definition) => definition.id) ?? [],
});

export const GroupForm = observer(function GroupForm(props: TGroupFormProps) {
  const { workspaceSlug, group, onSuccess, onCancel } = props;
  // store hooks
  const { createGroup, updateGroup, getDefinitionList } = useAgentStore();
  // translation
  const { t } = useTranslation();
  // states
  const [form, setForm] = useState<TFormState>(() => buildFormState(group));
  const [isSubmitting, setIsSubmitting] = useState(false);

  const isEditing = Boolean(group);
  const definitions = getDefinitionList();

  // 同一份组件实例会被换着组用（关掉一个再开另一个），所以目标组一变就重灌字段。
  useEffect(() => {
    setForm(buildFormState(group));
  }, [group]);

  const setField = <K extends keyof TFormState>(key: K, value: TFormState[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const toggleDefinition = (definitionId: string) =>
    setField(
      "definitionIds",
      form.definitionIds.includes(definitionId)
        ? form.definitionIds.filter((id) => id !== definitionId)
        : [...form.definitionIds, definitionId]
    );

  const handleSubmit = async () => {
    const name = form.name.trim();
    if (!name) return;

    setIsSubmitting(true);
    try {
      // ⚠️ `definition_ids` 送的是**完整的目标集合**，不是差量，而且**每次都送** ——
      //    字段缺席 = 名册纹丝不动，送空数组才是「清空」。两边都由服务端算增删。
      const payload: TAgentGroupWrite = {
        name,
        description: form.description,
        instructions: form.instructions,
        definition_ids: form.definitionIds,
      };
      const saved = group
        ? await updateGroup(workspaceSlug, group.id, payload)
        : await createGroup(workspaceSlug, payload);
      onSuccess?.(saved);
    } catch (error) {
      // 服务端的 409/400 已经点名了（重名、跨工作区的岗位），原样透出去比一句「失败」有用。
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("toast.error"),
        message: (error as { error?: string })?.error ?? t("toast.error"),
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-2">
        <h4 className="text-body-sm-medium text-tertiary">{t("ai_members.group_form.name")}</h4>
        <Input
          id="ai-group-name"
          name="ai-group-name"
          type="text"
          value={form.name}
          onChange={(e) => setField("name", e.target.value)}
          placeholder={t("ai_members.group_form.name_placeholder")}
          className="w-full rounded-md"
        />
      </div>

      <div className="flex flex-col gap-2">
        <h4 className="text-body-sm-medium text-tertiary">{t("ai_members.group_form.description_label")}</h4>
        <Input
          id="ai-group-description"
          name="ai-group-description"
          type="text"
          maxLength={280}
          value={form.description}
          onChange={(e) => setField("description", e.target.value)}
          placeholder={t("ai_members.group_form.description_placeholder")}
          className="w-full rounded-md"
        />
      </div>

      <div className="flex flex-col gap-2">
        <h4 className="text-body-sm-medium text-tertiary">{t("ai_members.group_form.instructions")}</h4>
        <TextArea
          id="ai-group-instructions"
          name="ai-group-instructions"
          value={form.instructions}
          onChange={(e) => setField("instructions", e.target.value)}
          textAreaSize="md"
          className="w-full"
        />
        <p className="text-caption-sm-regular text-tertiary">{t("ai_members.group_form.instructions_hint")}</p>
      </div>

      <div className="flex flex-col gap-2">
        <h4 className="text-body-sm-medium text-tertiary">{t("ai_members.group_form.roster")}</h4>
        <DefinitionPicker
          definitions={definitions}
          selectedIds={form.definitionIds}
          onToggle={toggleDefinition}
          emptyLabel={t("ai_members.group_form.roster_empty")}
        />
        <p className="text-caption-sm-regular text-tertiary">{t("ai_members.group_form.roster_hint")}</p>
      </div>

      <div className="flex items-center justify-end gap-2 pt-1">
        {onCancel && (
          <Button variant="secondary" size="lg" onClick={onCancel} disabled={isSubmitting}>
            {t("common.cancel")}
          </Button>
        )}
        <Button
          variant="primary"
          size="lg"
          loading={isSubmitting}
          disabled={!form.name.trim()}
          onClick={() => {
            void handleSubmit();
          }}
        >
          {isEditing ? t("common.save_changes") : t("ai_members.groups.create")}
        </Button>
      </div>
    </div>
  );
});
