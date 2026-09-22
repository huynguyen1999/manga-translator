import React from "react";
import { Icon } from "@iconify/react";
import { Tooltip } from "./Tooltip";

export type LabeledInputProps = {
  id: string;
  label: string;
  icon: string;
  title?: string;
  type?: React.InputHTMLAttributes<unknown>["type"];
  step?: number | string;
  min?: number | string;
  max?: number | string;
  placeholder?: string;
  value: number | string | undefined;
  onChange: (value: number) => void;
  tooltip?: string;
};

export const LabeledInput: React.FC<LabeledInputProps> = ({
  id,
  label,
  icon,
  title,
  type = "number",
  step,
  min,
  max,
  placeholder,
  value,
  onChange,
  tooltip,
}) => {
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
        <input
          id={id}
          title={title || label}
          type={type}
          step={step}
          min={min}
          max={max}
          placeholder={placeholder}
          value={value !== undefined && value !== null ? value : ""}
          onChange={(e) => {
            const val = parseFloat(e.target.value);
            onChange(isNaN(val) ? 0 : val);
          }}
          className="w-full rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800/90 pl-9 pr-3 py-2 text-sm text-zinc-900 dark:text-zinc-100 shadow-xs hover:border-zinc-300 dark:hover:border-zinc-600 focus:border-indigo-500 dark:focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 transition-colors"
        />
      </div>
    </div>
  );
};

