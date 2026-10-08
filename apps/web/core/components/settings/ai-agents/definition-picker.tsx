/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { EPillSize, EPillVariant, Pill } from "@plane/propel/pill";
import { Checkbox } from "@plane/ui";
// types
import type { TAgentDefinition } from "@/services/agent.service";
// local imports
import { TIER_LABEL } from "./constants";

export type TDefinitionPickerProps = {
  /** 可以勾的岗位。**由调用方筛** —— 名册那边要滤掉已经加进项目的，组这边全列。 */
  definitions: TAgentDefinition[];
  selectedIds: string[];
  onToggle: (definitionId: string) => void;
  /** 一个候选都不剩时的空态文案：两边措辞不同，所以是入参不是常量。 */
  emptyLabel: string;
};

/**
 * 岗位的多选列表（一行一个：名字 + 档位药丸 + 一句说明）。
 *
 * 抽出来是因为它已经有两处**逐字相同**的用法（往项目里加成员、编岗位组的名册），
 * 而这两处的差别只在「候选集怎么来」和「空态说什么」—— 那正好是入参。
 */
export const DefinitionPicker = observer(function DefinitionPicker(props: TDefinitionPickerProps) {
  const { definitions, selectedIds, onToggle, emptyLabel } = props;
  // translation
  const { t } = useTranslation();

  if (definitions.length === 0) {
    return (
      <div className="grid w-full place-items-center rounded-lg border border-dashed border-subtle px-4 py-10">
        <p className="text-caption-md-regular text-tertiary">{emptyLabel}</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {definitions.map((definition) => (
        // The label wraps the checkbox plus its copy — same shape (and the same
        // lint exception) as post-form's tier radios.
        // oxlint-disable-next-line jsx_a11y/label-has-associated-control
        <label
          key={definition.id}
          className="flex cursor-pointer items-start gap-3 rounded-lg border border-subtle px-3 py-2"
        >
          <Checkbox
            checked={selectedIds.includes(definition.id)}
            onChange={() => onToggle(definition.id)}
            containerClassName="mt-0.5"
          />
          <span className="flex min-w-0 flex-col gap-0.5">
            <span className="flex items-center gap-2">
              <span className="text-body-sm-medium text-primary">{definition.name}</span>
              <Pill variant={EPillVariant.DEFAULT} size={EPillSize.SM} className="border-none">
                {t(TIER_LABEL[definition.tier])}
              </Pill>
            </span>
            {definition.description && (
              <span className="text-caption-md-regular text-tertiary">{definition.description}</span>
            )}
          </span>
        </label>
      ))}
    </div>
  );
});
