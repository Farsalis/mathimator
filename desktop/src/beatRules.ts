import { KINDS } from "./beatKinds";
import type { Beat, SceneObject } from "./types";

/**
 * What the timeline shows for a beat: its kind and the names of its targets, such as `Fade out · Axes, Sine`,
 * `Transform · Circle → Rectangle` or `Move along a path · Dot along Sine`. A target that is not an object shows its id.
 */
export function beatLabel(beat: Beat, objects: readonly SceneObject[]): string {
  const kind = KINDS[beat.action];
  const names = beat.targets.map((id) => objects.find((object) => object.id === id)?.name ?? id);
  const [first, second] = names;
  if (kind.arity === "pair" && first !== undefined && second !== undefined) {
    return `${kind.label} · ${first} ${kind.family === "morph" ? "→" : "along"} ${second}`;
  }
  return names.length === 0 ? kind.label : `${kind.label} · ${names.join(", ")}`;
}
