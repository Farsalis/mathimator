/**
 * The beat catalog, the easings and the shapes: the UI's typed copy of the worker's table (`beats.py`).
 * tests/fixtures/protocol/beat-kinds.json records that table, and beatKinds.test.ts checks this copy against it, since
 * the app cannot import from tests/.
 */
import type { SceneKind } from "./types";

/** Every object type, in the order the catalog lists them. */
export const OBJECT_TYPES = [
  "axes",
  "function_plot",
  "math_formula",
  "text",
  "dot",
  "line",
  "arrow",
  "circle",
  "rectangle",
  "axes_point",
  "three_d_axes",
  "surface",
  "parametric_curve",
] as const;
export type ObjectType = (typeof OBJECT_TYPES)[number];

/** The object types the Add menu's Shapes group adds, each with its entrance beat. */
export const SHAPE_TYPES = ["dot", "line", "arrow", "circle", "rectangle", "axes_point"] as const;
export type ShapeType = (typeof SHAPE_TYPES)[number];

/** Every beat action, in catalog order. */
export const BEAT_ACTIONS = [
  "wait",
  "create",
  "fade_in",
  "write",
  "grow",
  "grow_arrow",
  "draw_border_then_fill",
  "spin_in",
  "fade_out",
  "uncreate",
  "unwrite",
  "shrink",
  "move",
  "move_to",
  "scale",
  "rotate",
  "set_opacity",
  "set_color",
  "move_along",
  "indicate",
  "circumscribe",
  "flash",
  "wiggle",
  "focus",
  "wave",
  "transform",
  "transform_formula",
  "move_camera",
] as const;
export type BeatAction = (typeof BEAT_ACTIONS)[number];

/** The Manim rate functions a beat may name as its `easing`. */
export const EASING_NAMES = [
  "smooth",
  "linear",
  "rush_into",
  "rush_from",
  "slow_into",
  "double_smooth",
  "lingering",
  "there_and_back",
  "there_and_back_with_pause",
] as const;
export type Easing = (typeof EASING_NAMES)[number];

/** What a beat does to the on-screen state of each target. */
export type Family = "hold" | "entrance" | "exit" | "change" | "emphasis" | "morph" | "camera";
/** How many targets a kind takes: none, one or more, or exactly two in the order of its roles. */
export type Arity = "none" | "some" | "pair";
export type ParamType = "number" | "vector" | "color" | "id";
export type OnScreen = "on" | "off";

/** An `[x, y]` pair: a shift, a point, a centre, the start or end of a line. */
export type Vector = [number, number];
/** The value of a beat or shape setting: a number, a vector, a `#RRGGBB` colour or an object id. */
export type ParamValue = number | Vector | string;
/** The settings of a beat or a shape, by name. */
export type Params = Record<string, ParamValue>;

export interface EasingInfo {
  name: Easing;
  label: string;
  /** Where the easing leaves the animation: 1 for one that arrives, 0 for one that comes back. */
  endsAt: 0 | 1;
}

export interface FamilyInfo {
  name: Family;
  /** The heading of its group in the kind select. */
  label: string;
  order: number;
}

/** One setting of a beat kind or a shape. */
export interface ParamSpec {
  name: string;
  label: string;
  type: ParamType;
  /** What an edit stores when it names no value; null when the worker computes it. */
  default: ParamValue | null;
  /** Inclusive bounds of a number or of each component of a vector, judged at three decimals. */
  low: number | null;
  high: number | null;
  /** Greater than 0 at three decimals. */
  positive: boolean;
}

/** One of the two targets of a pair kind. */
export interface Role {
  name: string;
  label: string;
  types: readonly ObjectType[];
  before: OnScreen;
  after: OnScreen;
}

export interface BeatKind {
  action: BeatAction;
  family: Family;
  label: string;
  arity: Arity;
  /** The object types a kind of arity "some" accepts. */
  types: readonly ObjectType[];
  roles: readonly Role[];
  params: readonly ParamSpec[];
  /** The easings a beat of this kind may name; none for wait. */
  easings: readonly Easing[];
  /** What an absent easing means. */
  defaultEasing: Easing | null;
  /** Checking axes as a target also checks their on-screen children. */
  withChildren: boolean;
  scenes: readonly SceneKind[];
}

export interface Shape {
  type: ShapeType;
  /** The Add menu label. */
  label: string;
  idPrefix: string;
  /** The action of the entrance beat Add inserts with it. */
  entrance: BeatAction;
  params: readonly ParamSpec[];
}

export const EASINGS: Record<Easing, EasingInfo> = {
  smooth: { name: "smooth", label: "Smooth", endsAt: 1 },
  linear: { name: "linear", label: "Linear", endsAt: 1 },
  rush_into: { name: "rush_into", label: "Rush in", endsAt: 1 },
  rush_from: { name: "rush_from", label: "Rush out", endsAt: 1 },
  slow_into: { name: "slow_into", label: "Slow into", endsAt: 1 },
  double_smooth: { name: "double_smooth", label: "Double smooth", endsAt: 1 },
  lingering: { name: "lingering", label: "Lingering (arrives early, holds)", endsAt: 1 },
  there_and_back: { name: "there_and_back", label: "There and back", endsAt: 0 },
  there_and_back_with_pause: { name: "there_and_back_with_pause", label: "There and back, with a pause", endsAt: 0 },
};

export const FAMILIES: Record<Family, FamilyInfo> = {
  hold: { name: "hold", label: "Wait", order: 0 },
  entrance: { name: "entrance", label: "Entrances", order: 1 },
  exit: { name: "exit", label: "Exits", order: 2 },
  change: { name: "change", label: "Changes", order: 3 },
  emphasis: { name: "emphasis", label: "Emphasis", order: 4 },
  morph: { name: "morph", label: "Morphs", order: 5 },
  camera: { name: "camera", label: "Camera", order: 6 },
};

/** The easings that arrive, and those that come back to where they started. */
const PATH = EASING_NAMES.filter((name) => EASINGS[name].endsAt === 1);
const RETURN = EASING_NAMES.filter((name) => EASINGS[name].endsAt === 0);
const ALL_EASINGS = [...PATH, ...RETURN];
/** Uncreate and unwrite run their easing backwards, so they offer only those whose mirror image is an easing. */
const MIRRORED: readonly Easing[] = ["smooth", "linear", "rush_into", "rush_from", "double_smooth"];

/** The object types among `types`, in catalog order. */
function only(...types: ObjectType[]): ObjectType[] {
  return OBJECT_TYPES.filter((type) => types.includes(type));
}

/** Every object type but `types`, in catalog order. */
function except(...types: ObjectType[]): ObjectType[] {
  return OBJECT_TYPES.filter((type) => !types.includes(type));
}

const ALL = except();
const THREE_D = only("three_d_axes", "surface", "parametric_curve");
const TWO_D = except(...THREE_D);
const WRITTEN = only("math_formula", "text");
const DRAWN = except(...WRITTEN);
const FILLED = only("math_formula", "text", "dot", "axes_point", "circle", "rectangle");
const PATHS = only("function_plot", "line", "arrow", "circle", "rectangle");
const MOVERS = only("dot", "axes_point");
/** Scaling an arrow to nothing keeps its tip at full size, so it neither grows nor shrinks from a point. */
const SCALABLE = except("arrow");
const SCALABLE_2D = except("arrow", ...THREE_D);

const BOTH: readonly SceneKind[] = ["scene", "three_d_scene"];
const SCENE_ONLY: readonly SceneKind[] = ["scene"];

function param(
  name: string,
  label: string,
  type: ParamType,
  value: ParamValue | null,
  low: number | null = null,
  high: number | null = null,
): ParamSpec {
  return { name, label, type, default: value, low, high, positive: false };
}

/** A kind of one or more targets, whose absent easing means smooth unless `rest` says otherwise. */
function some(
  action: BeatAction,
  family: Family,
  label: string,
  types: readonly ObjectType[],
  easings: readonly Easing[],
  rest: Partial<Pick<BeatKind, "params" | "defaultEasing" | "withChildren" | "scenes">> = {},
): BeatKind {
  return {
    action,
    family,
    label,
    arity: "some",
    types,
    roles: [],
    params: [],
    easings,
    defaultEasing: "smooth",
    withChildren: false,
    scenes: BOTH,
    ...rest,
  };
}

/** A kind of exactly two targets, in the order of `roles`, offered in 2D scenes. */
function pair(
  action: BeatAction,
  family: Family,
  label: string,
  roles: readonly Role[],
  easings: readonly Easing[],
  defaultEasing: Easing = "smooth",
): BeatKind {
  return {
    action,
    family,
    label,
    arity: "pair",
    types: [],
    roles,
    params: [],
    easings,
    defaultEasing,
    withChildren: false,
    scenes: SCENE_ONLY,
  };
}

function role(name: string, label: string, types: readonly ObjectType[], before: OnScreen, after: OnScreen): Role {
  return { name, label, types, before, after };
}

export const KINDS: Record<BeatAction, BeatKind> = {
  wait: {
    action: "wait",
    family: "hold",
    label: "Wait",
    arity: "none",
    types: [],
    roles: [],
    params: [],
    easings: [],
    defaultEasing: null,
    withChildren: false,
    scenes: BOTH,
  },
  create: some("create", "entrance", "Create", DRAWN, PATH),
  fade_in: some("fade_in", "entrance", "Fade in", ALL, PATH),
  // Every scene written before easings existed renders Write linearly.
  write: some("write", "entrance", "Write", WRITTEN, PATH, { defaultEasing: "linear", scenes: SCENE_ONLY }),
  grow: some("grow", "entrance", "Grow from center", SCALABLE, PATH),
  grow_arrow: some("grow_arrow", "entrance", "Grow arrow", only("arrow"), PATH, { scenes: SCENE_ONLY }),
  draw_border_then_fill: some("draw_border_then_fill", "entrance", "Draw border, then fill", FILLED, PATH, {
    scenes: SCENE_ONLY,
  }),
  spin_in: some("spin_in", "entrance", "Spin in", SCALABLE_2D, PATH, { scenes: SCENE_ONLY }),
  fade_out: some("fade_out", "exit", "Fade out", ALL, PATH, { withChildren: true }),
  uncreate: some("uncreate", "exit", "Uncreate", DRAWN, MIRRORED, { withChildren: true }),
  unwrite: some("unwrite", "exit", "Unwrite", WRITTEN, MIRRORED, { withChildren: true, scenes: SCENE_ONLY }),
  shrink: some("shrink", "exit", "Shrink away", SCALABLE, PATH, { withChildren: true }),
  move: some("move", "change", "Move by", ALL, ALL_EASINGS, {
    params: [param("shift", "Shift", "vector", [1, 0])],
    withChildren: true,
  }),
  move_to: some("move_to", "change", "Move to", ALL, ALL_EASINGS, {
    params: [param("point", "Point", "vector", [0, 0], -100, 100)],
    withChildren: true,
  }),
  scale: some("scale", "change", "Scale", ALL, ALL_EASINGS, {
    params: [param("factor", "Factor", "number", 1.5, 0.01, 100)],
    withChildren: true,
  }),
  // Degrees, counter-clockwise.
  rotate: some("rotate", "change", "Rotate", ALL, ALL_EASINGS, {
    params: [param("angle", "Angle", "number", 90, -3600, 3600)],
    withChildren: true,
  }),
  set_opacity: some("set_opacity", "change", "Set opacity", ALL, ALL_EASINGS, {
    params: [param("opacity", "Opacity", "number", 0.5, 0, 1)],
  }),
  set_color: some("set_color", "change", "Recolor", ALL, ALL_EASINGS, {
    params: [param("color", "Color", "color", "#E07A5F")],
  }),
  move_along: pair(
    "move_along",
    "change",
    "Move along a path",
    [role("mover", "Object", MOVERS, "on", "on"), role("path", "Path", PATHS, "on", "on")],
    ALL_EASINGS,
  ),
  // Indicate scales and recolours the object itself: only an easing that comes back restores it.
  indicate: some("indicate", "emphasis", "Indicate", ALL, RETURN, {
    defaultEasing: "there_and_back",
    withChildren: true,
  }),
  // Its flash never runs backwards, so an easing that comes back would depend on the frame rate.
  circumscribe: some("circumscribe", "emphasis", "Circumscribe", TWO_D, PATH, { scenes: SCENE_ONLY }),
  flash: some("flash", "emphasis", "Flash", TWO_D, ALL_EASINGS, { scenes: SCENE_ONLY }),
  wiggle: some("wiggle", "emphasis", "Wiggle", ALL, ALL_EASINGS, { withChildren: true }),
  focus: some("focus", "emphasis", "Focus", TWO_D, ALL_EASINGS, { scenes: SCENE_ONLY }),
  wave: some("wave", "emphasis", "Wave", TWO_D, ALL_EASINGS, { withChildren: true, scenes: SCENE_ONLY }),
  transform: pair(
    "transform",
    "morph",
    "Transform",
    [role("source", "From", TWO_D, "on", "off"), role("destination", "To", TWO_D, "off", "on")],
    PATH,
  ),
  // Every scene written before easings existed renders this transform linearly.
  transform_formula: pair(
    "transform_formula",
    "morph",
    "Transform formula (match symbols)",
    [
      role("source", "From", only("math_formula"), "on", "off"),
      role("destination", "To", only("math_formula"), "off", "on"),
    ],
    PATH,
    "linear",
  ),
  // The rules of scene.camera; the worker fills in the camera in effect when an edit names no value.
  move_camera: {
    action: "move_camera",
    family: "camera",
    label: "Move camera",
    arity: "none",
    types: [],
    roles: [],
    params: [
      param("phi", "Phi", "number", null),
      param("theta", "Theta", "number", null),
      { ...param("zoom", "Zoom", "number", null), positive: true },
    ],
    easings: ALL_EASINGS,
    defaultEasing: "smooth",
    withChildren: false,
    scenes: ["three_d_scene"],
  },
};

const CENTER = param("center", "Center", "vector", [0, 0], -100, 100);
const DOT_RADIUS = param("radius", "Radius", "number", 0.08, 0.001, 50);
const WARM = param("color", "Color", "color", "#E07A5F");
const WHITE = param("color", "Color", "color", "#FFFFFF");
const BLUE = param("color", "Color", "color", "#58C4DD");
const STROKE = param("strokeWidth", "Stroke width", "number", 4, 0, 50);
const FILL = param("fillOpacity", "Fill opacity", "number", 0, 0, 1);

export const SHAPES: Record<ShapeType, Shape> = {
  dot: { type: "dot", label: "Dot", idPrefix: "dot", entrance: "grow", params: [CENTER, DOT_RADIUS, WARM] },
  line: {
    type: "line",
    label: "Line",
    idPrefix: "line",
    entrance: "create",
    params: [
      param("start", "Start", "vector", [-2, 0], -100, 100),
      param("end", "End", "vector", [2, 0], -100, 100),
      WHITE,
      STROKE,
    ],
  },
  arrow: {
    type: "arrow",
    label: "Arrow",
    idPrefix: "arrow",
    entrance: "grow_arrow",
    params: [
      param("start", "Start", "vector", [-2, -1], -100, 100),
      param("end", "End", "vector", [2, 1], -100, 100),
      WHITE,
      param("strokeWidth", "Stroke width", "number", 6, 0, 50),
    ],
  },
  circle: {
    type: "circle",
    label: "Circle",
    idPrefix: "circle",
    entrance: "create",
    params: [CENTER, param("radius", "Radius", "number", 1, 0.001, 50), BLUE, STROKE, FILL],
  },
  rectangle: {
    type: "rectangle",
    label: "Rectangle",
    idPrefix: "rectangle",
    entrance: "create",
    params: [
      CENTER,
      param("width", "Width", "number", 4, 0.001, 100),
      param("height", "Height", "number", 2, 0.001, 100),
      BLUE,
      STROKE,
      FILL,
    ],
  },
  axes_point: {
    type: "axes_point",
    label: "Point on axes",
    idPrefix: "point",
    entrance: "grow",
    params: [
      // The worker picks the axes and the middle of their ranges.
      param("axesId", "Axes", "id", null),
      param("x", "x", "number", null, -1_000_000, 1_000_000),
      param("y", "y", "number", null, -1_000_000, 1_000_000),
      DOT_RADIUS,
      WARM,
    ],
  },
};

/** The entrance an object takes when it is added, or when "+ Beat" brings it on screen. */
export const NATURAL_ENTRANCE: Record<ObjectType, BeatAction> = {
  axes: "create",
  function_plot: "create",
  math_formula: "write",
  text: "write",
  dot: SHAPES.dot.entrance,
  line: SHAPES.line.entrance,
  arrow: SHAPES.arrow.entrance,
  circle: SHAPES.circle.entrance,
  rectangle: SHAPES.rectangle.entrance,
  axes_point: SHAPES.axes_point.entrance,
  three_d_axes: "create",
  surface: "create",
  parametric_curve: "create",
};

export function isShapeType(type: ObjectType): type is ShapeType {
  return SHAPE_TYPES.some((shape) => shape === type);
}
