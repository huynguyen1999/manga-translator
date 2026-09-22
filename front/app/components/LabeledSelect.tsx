import React from "react";
import { Icon } from "@iconify/react";
import { Tooltip } from "./Tooltip";

export type LabeledSelectOption = {
  label: string;
  value: string;
  hint?: string;
  group?: string;
};

export type LabeledSelectProps = {
  id: string;
  label: string;
  icon: string;
  title?: string;
  value: string;
  options: LabeledSelectOption[];
  onChange: (value: string) => void;
  tooltip?: string;
};

export const LabeledSelect: React.FC<LabeledSelectProps> = ({
  id,
  label,
  icon,
  title,
  value,
  options,
  onChange,
  tooltip,
}) => {
  const hasGroups = options.some((opt) => !!opt.group);

  // Preserve group ordering based on first appearance
  const groupedOptions: [string, LabeledSelectOption[]][] = React.useMemo(() => {
    if (!hasGroups) return [];
    const map = new Map<string, LabeledSelectOption[]>();
    for (const opt of options) {
      const g = opt.group || "";
      if (!map.has(g)) map.set(g, []);
      map.get(g)!.push(opt);
    }
    return Array.from(map.entries());
  }, [options, hasGroups]);

  return (
    <div className="flex flex-col space-y-1.5">
      <div className="flex items-center justify-between">
        <label
          htmlFor={id}
          className="text-xs font-semibold uppercase tracking-wider text-zinc-600 dark:text-zinc-300"
        >
          {label}
        </label>
        {tooltip && <Tooltip content={tooltip} />}
      </div>
      <div className="relative">
        <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-zinc-400 dark:text-zinc-500">
          <Icon icon={icon} className="h-4 w-4" />
        </div>
        <select
          id={id}
          title={title || label}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full appearance-none rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800/90 pl-9 pr-8 py-2 text-sm text-zinc-900 dark:text-zinc-100 shadow-xs hover:border-zinc-300 dark:hover:border-zinc-600 focus:border-indigo-500 dark:focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 transition-colors"
        >
          {hasGroups
            ? groupedOptions.map(([groupName, groupOpts]) =>
                groupName ? (
                  <optgroup
                    key={groupName}
                    label={groupName}
                    className="bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 font-semibold"
                  >
                    {groupOpts.map((opt) => (
                      <option
                        key={opt.value}
                        value={opt.value}
                        className="bg-white dark:bg-zinc-800 text-zinc-900 dark:text-zinc-100 font-normal py-1"
                      >
                        {opt.label}
                      </option>
                    ))}
                  </optgroup>
                ) : (
                  groupOpts.map((opt) => (
                    <option
                      key={opt.value}
                      value={opt.value}
                      className="bg-white dark:bg-zinc-800 text-zinc-900 dark:text-zinc-100 py-1"
                    >
                      {opt.label}
                    </option>
                  ))
                )
              )
            : options.map((opt) => (
                <option
                  key={opt.value}
                  value={opt.value}
                  className="bg-white dark:bg-zinc-800 text-zinc-900 dark:text-zinc-100 py-1"
                >
                  {opt.label}
                </option>
              ))}
        </select>
        <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center pr-2.5 text-zinc-400 dark:text-zinc-500">
          <Icon icon="carbon:chevron-down" className="h-4 w-4" />
        </div>
      </div>
    </div>
  );
};

