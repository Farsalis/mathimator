import { describe, expect, it } from "vitest";
import catalog from "../../tests/fixtures/protocol/beat-kinds.json";
import {
  BEAT_ACTIONS,
  EASING_NAMES,
  EASINGS,
  FAMILIES,
  isShapeType,
  KINDS,
  NATURAL_ENTRANCE,
  OBJECT_TYPES,
  SHAPE_TYPES,
  SHAPES,
} from "./beatKinds";

describe("the UI's copy of the beat catalog (beat-kinds.json)", () => {
  it("equals the worker's table", () => {
    expect({
      easings: Object.values(EASINGS),
      families: Object.values(FAMILIES),
      kinds: Object.values(KINDS),
      shapes: Object.values(SHAPES),
      naturalEntrance: NATURAL_ENTRANCE,
    }).toStrictEqual(catalog);
  });

  it("keys each row by its name, in the order of the name lists", () => {
    expect(Object.entries(KINDS).map(([action, kind]) => [action, kind.action])).toEqual(
      BEAT_ACTIONS.map((action) => [action, action]),
    );
    expect(Object.entries(EASINGS).map(([name, easing]) => [name, easing.name])).toEqual(
      EASING_NAMES.map((name) => [name, name]),
    );
    expect(Object.entries(SHAPES).map(([type, shape]) => [type, shape.type])).toEqual(
      SHAPE_TYPES.map((type) => [type, type]),
    );
    expect(Object.entries(FAMILIES).map(([name, family]) => [name, family.order])).toEqual(
      Object.values(FAMILIES).map((family, order) => [family.name, order]),
    );
    expect(Object.keys(NATURAL_ENTRANCE)).toEqual(OBJECT_TYPES);
  });

  it("tells the shapes from the other object types", () => {
    expect(OBJECT_TYPES.filter(isShapeType)).toEqual(SHAPE_TYPES);
  });
});
