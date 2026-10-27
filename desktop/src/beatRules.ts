/**
 * The beat rules the UI applies before it sends an edit (spec §6): which objects a kind can take at a point of the
 * timeline, the targets a kind change or "+ Beat" starts from, and the order the worker stores targets in. The worker
 * checks every edit again; these rules only keep the UI from offering what it would refuse.
 */
import { BEAT_ACTIONS, FAMILIES, KINDS, NATURAL_ENTRANCE } from "./beatKinds";
import type { BeatKind, FamilyInfo, ObjectType, OnScreen, Role } from "./beatKinds";
import type { Beat, BeatAction, SceneKind, SceneObject } from "./types";

/** The object types drawn on axes, which name those axes in `params.axesId`. */
const AXES_CHILD_TYPES: readonly ObjectType[] = ["function_plot", "axes_point", "surface", "parametric_curve"];

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

/** The axes `object` is drawn on, or null for an object that is not drawn on axes. */
export function axesIdOf(object: SceneObject): string | null {
  const axesId = object.params.axesId;
  return AXES_CHILD_TYPES.includes(object.type) && typeof axesId === "string" ? axesId : null;
}

/**
 * The order the worker stores the targets of `action` in (spec §6, Protocol;
 * tests/fixtures/beats/canonical-order.json). The targets of a kind of arity "some" go in document order, then each
 * axes that comes after one of its own children moves to just before the first of them, so axes always come before
 * what is drawn on them. A pair keeps its role order. Ids that name no object keep their order at the end.
 */
export function canonicalTargets(
  objects: readonly SceneObject[],
  action: BeatAction,
  targets: readonly string[],
): string[] {
  if (KINDS[action].arity !== "some") {
    return [...targets];
  }
  const position = new Map(objects.map((object, index) => [object.id, index]));
  const rank = (id: string): number => position.get(id) ?? objects.length;
  // Array.prototype.sort is stable, so ids that name no object keep their order, as in the worker.
  const ordered = [...targets].sort((a, b) => rank(a) - rank(b));
  const axesOf = new Map(objects.map((object) => [object.id, axesIdOf(object)]));
  const drawnOn = new Set(axesOf.values());
  for (const axesId of ordered.filter((target) => drawnOn.has(target))) {
    const firstChild = ordered.findIndex((target) => axesOf.get(target) === axesId);
    const at = ordered.indexOf(axesId);
    if (firstChild !== -1 && firstChild < at) {
      ordered.splice(at, 1);
      ordered.splice(firstChild, 0, axesId);
    }
  }
  return ordered;
}

/** The kinds a scene of `sceneKind` offers, grouped by family in catalog order. */
export function kindGroups(sceneKind: SceneKind): { family: FamilyInfo; kinds: BeatKind[] }[] {
  const offered = BEAT_ACTIONS.map((action) => KINDS[action]).filter((kind) => kind.scenes.includes(sceneKind));
  return Object.values(FAMILIES)
    .map((family) => ({ family, kinds: offered.filter((kind) => kind.family === family.name) }))
    .filter((group) => group.kinds.length > 0);
}

/** Whether `object` has an accepted type and is in `state` where `on` is on screen. */
function fits(object: SceneObject, types: readonly ObjectType[], state: OnScreen, on: readonly string[]): boolean {
  return types.includes(object.type) && on.includes(object.id) === (state === "on");
}

/**
 * The objects a beat of `action`, a kind of arity "some", can take where `on` is on screen, in document order: those
 * of an accepted type that are off screen for an entrance, and on screen for every other family.
 */
export function candidates(action: BeatAction, objects: readonly SceneObject[], on: readonly string[]): SceneObject[] {
  const kind = KINDS[action];
  const state: OnScreen = kind.family === "entrance" ? "off" : "on";
  return objects.filter((object) => fits(object, kind.types, state, on));
}

/** The objects that can take `role` of a pair kind where `on` is on screen, in document order. */
export function roleCandidates(role: Role, objects: readonly SceneObject[], on: readonly string[]): SceneObject[] {
  return objects.filter((object) => fits(object, role.types, role.before, on));
}

/**
 * `targets` and the on-screen children of `axesId` that `action` accepts, in canonical order. With a kind that takes
 * children along (exits, the moves, scale, rotate, indicate, wiggle, wave), checking axes checks what is drawn on them.
 */
export function checkTarget(
  action: BeatAction,
  objects: readonly SceneObject[],
  on: readonly string[],
  targets: readonly string[],
  id: string,
): string[] {
  const children = KINDS[action].withChildren
    ? candidates(action, objects, on)
        .filter((object) => axesIdOf(object) === id)
        .map((object) => object.id)
    : [];
  const added = [id, ...children].filter((item) => !targets.includes(item));
  return canonicalTargets(objects, action, [...targets, ...added]);
}

/** Whether an exit takes `object` off screen with its axes, which are among `targets`, so its box follows theirs. */
export function leavesWithAxes(action: BeatAction, object: SceneObject, targets: readonly string[]): boolean {
  const axesId = axesIdOf(object);
  return KINDS[action].family === "exit" && axesId !== null && targets.includes(axesId);
}

/** One box of the target checklist of a kind of arity "some". */
export interface TargetRow {
  object: SceneObject;
  checked: boolean;
  /** The object can be a target here; a current target that cannot is shown with a warning. */
  fits: boolean;
  /** Checked with its axes in an exit, so it cannot be unchecked on its own. */
  locked: boolean;
}

/** The target checklist of a beat of `action`: its candidates where `on` is on screen and its targets, in canonical order. */
export function targetRows(
  action: BeatAction,
  objects: readonly SceneObject[],
  on: readonly string[],
  targets: readonly string[],
): TargetRow[] {
  const fitting = candidates(action, objects, on).map((object) => object.id);
  const listed = canonicalTargets(objects, action, [...fitting, ...targets.filter((id) => !fitting.includes(id))]);
  return listed.flatMap((id) => {
    const object = objects.find((item) => item.id === id);
    if (!object) {
      return [];
    }
    const checked = targets.includes(id);
    return [
      { object, checked, fits: fitting.includes(id), locked: checked && leavesWithAxes(action, object, targets) },
    ];
  });
}

/**
 * The targets a beat at a point where `on` is on screen takes when its kind becomes `action`, or null when no object
 * could be its target there, which disables that kind in the kind select (spec §6, Beat editor). For a kind of arity
 * "some": the current targets that fit (an exit adds what is drawn on axes among them), else the selected object if
 * it fits, else the first object that fits, checked as in the checklist. For a pair, each role in order: an object
 * entering here keeps its role (a morph's destination), and an on-screen role starts from the selected object.
 */
export function targetsForKind(
  action: BeatAction,
  current: readonly string[],
  selectedId: string | null,
  objects: readonly SceneObject[],
  on: readonly string[],
): string[] | null {
  const kind = KINDS[action];
  switch (kind.arity) {
    case "none":
      return [];
    case "some": {
      const fitting = candidates(action, objects, on).map((object) => object.id);
      const kept = current.filter((id) => fitting.includes(id));
      if (kept.length > 0) {
        // An exit cannot leave what is drawn on axes behind, so it takes their children along.
        return kind.family === "exit"
          ? kept.reduce<string[]>((targets, id) => checkTarget(action, objects, on, targets, id), [])
          : canonicalTargets(objects, action, kept);
      }
      const first = selectedId !== null && fitting.includes(selectedId) ? selectedId : fitting[0];
      return first === undefined ? null : checkTarget(action, objects, on, [], first);
    }
    case "pair": {
      const picked: string[] = [];
      for (const role of kind.roles) {
        const fitting = roleCandidates(role, objects, on)
          .map((object) => object.id)
          .filter((id) => !picked.includes(id));
        const preferred = role.before === "off" ? current : selectedId === null ? [] : [selectedId];
        const id = preferred.find((item) => fitting.includes(item)) ?? fitting[0];
        if (id === undefined) {
          return null;
        }
        picked.push(id);
      }
      return picked;
    }
  }
}

/** Where a beat inserted after `beatId` lands: just after it, or at the end when no beat is named. */
export function insertionIndex(beats: readonly Beat[], beatId: string | null): number {
  const index = beats.findIndex((beat) => beat.id === beatId);
  return index === -1 ? beats.length : index + 1;
}

/**
 * The kind and targets of the beat "+ Beat" adds where `on` is on screen (spec §6, + Beat): with no object selected, a
 * wait, or a camera move in a 3D scene; for a selected object off screen, its natural entrance; for one on screen,
 * indicate, which takes axes with what is drawn on them, so a graph is emphasised as one.
 */
export function plusBeat(
  sceneKind: SceneKind,
  objects: readonly SceneObject[],
  on: readonly string[],
  selectedId: string | null,
): { action: BeatAction; targets: string[] } {
  const object = objects.find((item) => item.id === selectedId);
  if (!object) {
    return { action: sceneKind === "three_d_scene" ? "move_camera" : "wait", targets: [] };
  }
  if (!on.includes(object.id)) {
    return { action: NATURAL_ENTRANCE[object.type], targets: [object.id] };
  }
  return { action: "indicate", targets: checkTarget("indicate", objects, on, [], object.id) };
}

/**
 * The axes Add puts a point on, where `on` is on screen (spec §6, Shapes): the selected object if it is axes, else the
 * axes of the selected plot or point, else the first axes on screen there, else the first axes in the document; null
 * when the document holds no 2D axes.
 */
export function pointAxesId(
  objects: readonly SceneObject[],
  on: readonly string[],
  selectedId: string | null,
): string | null {
  const axes = objects.filter((object) => object.type === "axes").map((object) => object.id);
  const selected = objects.find((object) => object.id === selectedId);
  const preferred = [selected?.id, selected ? axesIdOf(selected) : null, ...on];
  return preferred.find((id) => typeof id === "string" && axes.includes(id)) ?? axes[0] ?? null;
}
