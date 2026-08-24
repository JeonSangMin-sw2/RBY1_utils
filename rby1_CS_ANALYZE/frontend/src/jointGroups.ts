export type JointGroupKey = "head" | "right_arm" | "left_arm" | "torso" | "wheel" | "other";

export type JointGroup = {
  key: JointGroupKey;
  label: string;
  joints: string[];
};

const JOINT_COLLATOR = new Intl.Collator("en", { numeric: true });

const GROUP_META: { key: JointGroupKey; label: string }[] = [
  { key: "head", label: "Head" },
  { key: "right_arm", label: "Right Arm" },
  { key: "left_arm", label: "Left Arm" },
  { key: "torso", label: "Torso" },
  { key: "wheel", label: "Wheel" },
  { key: "other", label: "Other" },
];

export function jointGroupKey(joint: string): JointGroupKey {
  if (joint.startsWith("head_")) return "head";
  if (joint.startsWith("right_arm_")) return "right_arm";
  if (joint.startsWith("left_arm_")) return "left_arm";
  if (joint.startsWith("torso_")) return "torso";
  if (joint === "left_wheel" || joint === "right_wheel" || joint.includes("wheel")) return "wheel";
  return "other";
}

export function groupJoints(joints: string[]): JointGroup[] {
  const grouped = new Map<JointGroupKey, string[]>();
  for (const joint of new Set(joints)) {
    const key = jointGroupKey(joint);
    grouped.set(key, [...(grouped.get(key) ?? []), joint]);
  }
  return GROUP_META.flatMap(({ key, label }) => {
    const values = grouped.get(key)?.sort(JOINT_COLLATOR.compare) ?? [];
    return values.length ? [{ key, label, joints: values }] : [];
  });
}

export function sortJoints(joints: string[]): string[] {
  return groupJoints(joints).flatMap((group) => group.joints);
}

export function isExactJointSelection(selection: string[], joints: string[]): boolean {
  if (selection.length !== joints.length) return false;
  const selected = new Set(selection);
  return joints.every((joint) => selected.has(joint));
}
