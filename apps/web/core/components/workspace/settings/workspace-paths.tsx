/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { Controller, useForm } from "react-hook-form";
// Plane Imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { IWorkspace } from "@plane/types";
import { Input } from "@plane/ui";
import { cn } from "@plane/utils";
// hooks
import { useWorkspace } from "@/hooks/store/use-workspace";
import { useUserPermissions } from "@/hooks/store/user";

const defaultValues: Partial<IWorkspace> = {
  project_markdown_path: null,
  wiki_markdown_path: null,
};

export const WorkspacePaths = observer(function WorkspacePaths() {
  // states
  const [isLoading, setIsLoading] = useState(false);
  // store hooks
  const { currentWorkspace, updateWorkspace } = useWorkspace();
  const { allowPermissions } = useUserPermissions();
  const { t } = useTranslation();

  // form info
  const {
    handleSubmit,
    control,
    reset,
    formState: { errors },
  } = useForm<IWorkspace>({
    defaultValues: { ...defaultValues, ...currentWorkspace },
  });

  const onSubmit = async (formData: IWorkspace) => {
    if (!currentWorkspace) return;

    setIsLoading(true);

    // Only send the two fields this form owns — the PATCH is partial, and any
    // extra key would overwrite the current server-side value.
    const payload: Partial<IWorkspace> = {
      project_markdown_path: formData.project_markdown_path || null,
      wiki_markdown_path: formData.wiki_markdown_path || null,
    };

    try {
      await updateWorkspace(currentWorkspace.slug, payload);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("common.success"),
        message: t("workspace_settings.settings.paths.toast_updated"),
      });
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("common.error"),
        message: t("common.something_went_wrong"),
      });
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (currentWorkspace) reset({ ...currentWorkspace });
  }, [currentWorkspace, reset]);

  const isAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);

  if (!currentWorkspace) return null;

  return (
    <div className={cn("flex w-full flex-col gap-y-7", { "opacity-60": !isAdmin })}>
      <div className="grid-col grid w-full grid-cols-1 items-center justify-between gap-10 xl:grid-cols-2">
        <div className="flex flex-col gap-2">
          <h4 className="text-body-sm-medium text-tertiary">{t("workspace_settings.settings.paths.project_label")}</h4>
          <Controller
            control={control}
            name="project_markdown_path"
            render={({ field: { value, onChange, ref } }) => (
              <Input
                id="project_markdown_path"
                name="project_markdown_path"
                type="text"
                value={value ?? ""}
                onChange={onChange}
                ref={ref}
                hasError={Boolean(errors.project_markdown_path)}
                placeholder="~/projects"
                className="w-full rounded-md"
                disabled={!isAdmin}
              />
            )}
          />
        </div>
        <div className="flex flex-col gap-2">
          <h4 className="text-body-sm-medium text-tertiary">{t("workspace_settings.settings.paths.wiki_label")}</h4>
          <Controller
            control={control}
            name="wiki_markdown_path"
            render={({ field: { value, onChange, ref } }) => (
              <Input
                id="wiki_markdown_path"
                name="wiki_markdown_path"
                type="text"
                value={value ?? ""}
                onChange={onChange}
                ref={ref}
                hasError={Boolean(errors.wiki_markdown_path)}
                placeholder="~/wiki"
                className="w-full rounded-md"
                disabled={!isAdmin}
              />
            )}
          />
        </div>
      </div>
      <p className="text-caption-sm-regular text-tertiary">{t("workspace_settings.settings.paths.hint")}</p>
      {isAdmin && (
        <div className="flex items-center justify-between py-2">
          <Button
            variant="primary"
            size="lg"
            onClick={(e) => {
              void handleSubmit(onSubmit)(e);
            }}
            loading={isLoading}
          >
            {isLoading ? t("updating") : t("workspace_settings.settings.paths.title")}
          </Button>
        </div>
      )}
    </div>
  );
});
