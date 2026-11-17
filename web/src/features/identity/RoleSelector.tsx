import { Field } from "@astryxdesign/core/Field";
import { SegmentedControl, SegmentedControlItem } from "@astryxdesign/core/SegmentedControl";
import { useId } from "react";

export function RoleSelector({ value, onChange, label, physician, admin }: {
  value: string; onChange: (value: string) => void; label: string; physician: string; admin: string;
}) {
  const id = useId();
  return (
    <Field className="xi-role-selector" label={label} inputID={id} labelID={`${id}-label`} isGroupLabel>
      <SegmentedControl id={id} aria-labelledby={`${id}-label`} label={label} value={value} onChange={onChange} layout="fill">
        <SegmentedControlItem value="type_a" label={physician} />
        <SegmentedControlItem value="type_b" label={admin} />
      </SegmentedControl>
    </Field>
  );
}
