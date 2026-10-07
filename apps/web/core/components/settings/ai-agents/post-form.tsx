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
import { CloseIcon } from "@plane/propel/icons";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { Input, TextArea } from "@plane/ui";
import { cn } from "@plane/utils";
// types
import type { TAgentDefinition, TAgentTier, TAgentDefinitionWrite } from "@/services/agent.service";
// hooks
import { useAgentStore } from "@/hooks/store/use-agent-store";

export type TPostFormProps = {
  workspaceSlug: string;
  /** 传 undefined = 新建；传岗位 = 编辑。 */
  definition?: TAgentDefinition;
  onSuccess?: (definition: TAgentDefinition) => void;
  onCancel?: () => void;
  /** 用于嵌在弹窗里（去掉外框与标题）。 */
  embedded?: boolean;
};

const DEFAULT_PROFILE = "compass-ai";

const TIER_OPTIONS: { value: TAgentTier; label: string; hint: string }[] = [
  { value: "readonly", label: "ai_members.pool.tier_readonly", hint: "ai_members.pool.tier_readonly_hint" },
  { value: "writer", label: "ai_members.pool.tier_writer", hint: "ai_members.pool.tier_writer_hint" },
  { value: "ledger", label: "ai_members.pool.tier_ledger", hint: "ai_members.pool.tier_ledger_hint" },
];

type TFormState = {
  name: string;
  description: string;
  instructions: string;
  skills: string[];
  tier: TAgentTier;
  model: string;
  profile: string;
};

const buildFormState = (definition?: TAgentDefinition): TFormState => ({
  name: definition?.name ?? "",
  description: definition?.description ?? "",
  instructions: definition?.instructions ?? "",
  skills: definition?.skills ?? [],
  tier: definition?.tier ?? "readonly",
  model: definition?.model ?? "",
  profile: definition?.profile || DEFAULT_PROFILE,
});

export const PostForm = observer(function PostForm(props: TPostFormProps) {
  const { workspaceSlug, definition, onSuccess, onCancel, embedded = false } = props;
  // store hooks
  const { createDefinition, updateDefinition } = useAgentStore();
  // translation
  const { t } = useTranslation();
  // states
  const [form, setForm] = useState<TFormState>(() => buildFormState(definition));
  const [skillInput, setSkillInput] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const isEditing = Boolean(definition);

  // The same instance can be reused for a different post (Task 11's picker), so
  // re-seed the fields whenever the target post changes.
  useEffect(() => {
    setForm(buildFormState(definition));
    setSkillInput("");
  }, [definition]);

  const setField = <K extends keyof TFormState>(key: K, value: TFormState[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const addSkill = () => {
    const skill = skillInput.trim();
    if (!skill) return;
    // Names only, and the same name twice is the same skill.
    if (!form.skills.includes(skill)) setField("skills", [...form.skills, skill]);
    setSkillInput("");
  };

  const removeSkill = (skill: string) =>
    setField(
      "skills",
      form.skills.filter((s) => s !== skill)
    );

  const handleSubmit = async () => {
    const name = form.name.trim();
    if (!name) return;

    setIsSubmitting(true);
    try {
      const payload: TAgentDefinitionWrite = {
        name,
        description: form.description,
        instructions: form.instructions,
        skills: form.skills,
        tier: form.tier,
        model: form.model,
        profile: form.profile || DEFAULT_PROFILE,
      };
      const saved = definition
        ? await updateDefinition(workspaceSlug, definition.id, payload)
        : await createDefinition(workspaceSlug, payload);
      onSuccess?.(saved);
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("toast.error"),
        message: (error as { error?: string })?.error ?? t("toast.error"),
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  const body = (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-2">
        <h4 className="text-body-sm-medium text-tertiary">{t("ai_members.pool.name")}</h4>
        <Input
          id="ai-post-name"
          name="ai-post-name"
          type="text"
          value={form.name}
          onChange={(e) => setField("name", e.target.value)}
          placeholder={t("ai_members.pool.name_placeholder")}
          className="w-full rounded-md"
        />
      </div>

      <div className="flex flex-col gap-2">
        <h4 className="text-body-sm-medium text-tertiary">{t("ai_members.pool.description_label")}</h4>
        <Input
          id="ai-post-description"
          name="ai-post-description"
          type="text"
          maxLength={280}
          value={form.description}
          onChange={(e) => setField("description", e.target.value)}
          placeholder={t("ai_members.pool.description_placeholder")}
          className="w-full rounded-md"
        />
      </div>

      <div className="flex flex-col gap-2">
        <h4 className="text-body-sm-medium text-tertiary">{t("ai_members.pool.instructions")}</h4>
        <TextArea
          id="ai-post-instructions"
          name="ai-post-instructions"
          value={form.instructions}
          onChange={(e) => setField("instructions", e.target.value)}
          textAreaSize="md"
          className="w-full"
        />
        <p className="text-caption-sm-regular text-tertiary">{t("ai_members.pool.instructions_hint")}</p>
      </div>

      <div className="flex flex-col gap-2">
        <h4 className="text-body-sm-medium text-tertiary">{t("ai_members.pool.skills")}</h4>
        {form.skills.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {form.skills.map((skill) => (
              <span
                key={skill}
                className="flex items-center gap-1 rounded-full border border-subtle bg-layer-2 px-2 py-0.5 text-11"
              >
                {skill}
                <button
                  type="button"
                  onClick={() => removeSkill(skill)}
                  aria-label={`${t("common.delete")} ${skill}`}
                  className="grid place-items-center text-tertiary hover:text-primary"
                >
                  <CloseIcon className="size-3" />
                </button>
              </span>
            ))}
          </div>
        )}
        <Input
          id="ai-post-skill"
          name="ai-post-skill"
          type="text"
          value={skillInput}
          onChange={(e) => setSkillInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addSkill();
            }
          }}
          placeholder={t("ai_members.pool.skills_add")}
          className="w-full rounded-md"
        />
        <p className="text-caption-sm-regular text-tertiary">{t("ai_members.pool.skills_hint")}</p>
      </div>

      <div className="flex flex-col gap-2">
        <h4 className="text-body-sm-medium text-tertiary">{t("ai_members.pool.tier")}</h4>
        <div className="flex flex-col gap-2">
          {TIER_OPTIONS.map((option) => (
            // The label wraps the radio plus its copy — same shape as the two
            // pre-existing disables in customize-navigation-dialog.tsx.
            // oxlint-disable-next-line jsx_a11y/label-has-associated-control
            <label
              key={option.value}
              className={cn(
                "flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2 transition-colors",
                form.tier === option.value ? "border-accent-strong bg-layer-2" : "border-subtle"
              )}
            >
              <input
                type="radio"
                name="ai-post-tier"
                className="mt-1 size-4 text-accent-primary focus:ring-accent-strong"
                checked={form.tier === option.value}
                onChange={() => setField("tier", option.value)}
              />
              <span className="flex flex-col gap-0.5">
                <span className="text-body-sm-medium text-primary">{t(option.label)}</span>
                <span className="text-caption-sm-regular text-tertiary">{t(option.hint)}</span>
              </span>
            </label>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
        <div className="flex flex-col gap-2">
          <h4 className="text-body-sm-medium text-tertiary">{t("ai_members.pool.model")}</h4>
          <Input
            id="ai-post-model"
            name="ai-post-model"
            type="text"
            value={form.model}
            onChange={(e) => setField("model", e.target.value)}
            placeholder={t("ai_members.pool.model_placeholder")}
            className="w-full rounded-md"
          />
        </div>
        <div className="flex flex-col gap-2">
          <h4 className="text-body-sm-medium text-tertiary">{t("ai_members.pool.profile")}</h4>
          <Input
            id="ai-post-profile"
            name="ai-post-profile"
            type="text"
            value={form.profile}
            onChange={(e) => setField("profile", e.target.value)}
            placeholder={DEFAULT_PROFILE}
            className="w-full rounded-md"
          />
        </div>
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
          {isEditing ? t("common.save_changes") : t("ai_members.pool.create")}
        </Button>
      </div>
    </div>
  );

  if (embedded) return body;

  return (
    <div className="flex w-full flex-col gap-5 rounded-lg border border-subtle bg-surface-1 p-5">
      <h3 className="text-h5-medium text-primary">
        {isEditing ? t("ai_members.pool.edit_title") : t("ai_members.pool.create_title")}
      </h3>
      {body}
    </div>
  );
});
