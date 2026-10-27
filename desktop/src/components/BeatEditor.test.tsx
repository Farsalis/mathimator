// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Beat, Edit, SceneKind, SceneObject } from "../types";
import type { FieldError } from "../useEditor";
import { BeatEditor } from "./BeatEditor";

afterEach(cleanup);

const OBJECTS: SceneObject[] = [
  { id: "axes_1", name: "Axes", type: "axes", params: {} },
  { id: "plot_1", name: "Sine", type: "function_plot", params: { axesId: "axes_1" } },
  { id: "text_1", name: "Caption", type: "text", params: {} },
  { id: "eq_1", name: "Formula", type: "math_formula", params: {} },
  { id: "dot_1", name: "Dot", type: "dot", params: {} },
  { id: "circle_1", name: "Circle", type: "circle", params: {} },
];
/** On screen before the beat: the graph and the caption. */
const ON = ["axes_1", "plot_1", "text_1"];

const INDICATE: Beat = { id: "beat_2", duration: 1, action: "indicate", targets: ["text_1"] };
const SCALE: Beat = { id: "beat_2", duration: 1, action: "scale", targets: ["text_1"], params: { factor: 1.5 } };

interface Options {
  index?: number;
  count?: number;
  sceneKind?: SceneKind;
  onScreen?: string[];
  templateId?: string | null;
  selectedObjectId?: string | null;
  fieldError?: FieldError | null;
}

/** Renders the editor of `beat`, the second of three unless `options` say otherwise, and returns its edit spy. */
function renderEditor(beat: Beat, options: Options = {}) {
  const onEdit = vi.fn<(edit: Edit) => Promise<void>>(() => Promise.resolve());
  render(
    <BeatEditor
      beat={beat}
      index={options.index ?? 1}
      count={options.count ?? 3}
      sceneKind={options.sceneKind ?? "scene"}
      objects={OBJECTS}
      onScreen={options.onScreen ?? ON}
      templateId={options.templateId ?? null}
      selectedObjectId={options.selectedObjectId ?? null}
      fieldError={options.fieldError ?? null}
      onEdit={onEdit}
    />,
  );
  return onEdit;
}

function kindSelect(): HTMLSelectElement {
  return screen.getByLabelText<HTMLSelectElement>("Kind");
}

function option(select: HTMLSelectElement, label: string): HTMLOptionElement {
  return within(select).getByRole<HTMLOptionElement>("option", { name: label });
}

function box(name: string): HTMLInputElement {
  return within(screen.getByTestId("beat-targets")).getByRole<HTMLInputElement>("checkbox", { name: new RegExp(name) });
}

function typeAndLeave(input: HTMLElement, value: string): void {
  fireEvent.change(input, { target: { value } });
  fireEvent.blur(input);
}

function refusal(op: Edit["op"], key: string | null = null): FieldError {
  return { objectId: null, beatId: "beat_2", field: null, op, axis: null, key, message: `${op} was refused` };
}

describe("BeatEditor kind (spec §6, Beat editor)", () => {
  it("names the beat and the path refusals give it", () => {
    renderEditor(SCALE);
    expect(screen.getByText("Scale · beat_2 · scene.beats[1]")).toBeTruthy();
  });

  it("groups the kinds of a 2D scene by family, and disables those that have no target here", () => {
    renderEditor(INDICATE);
    const select = kindSelect();
    expect(select.value).toBe("indicate");
    const groups = [...select.querySelectorAll("optgroup")].map((group) => group.label);
    expect(groups).toEqual(["Wait", "Entrances", "Exits", "Changes", "Emphasis", "Morphs"]);
    const disabled = [...select.options].filter((item) => item.disabled).map((item) => item.textContent);
    // No arrow anywhere, and no dot or formula on screen to move along a path or transform.
    expect(disabled).toEqual(["Grow arrow", "Move along a path", "Transform formula (match symbols)"]);
  });

  it("offers a 3D scene its own kinds and the camera move", () => {
    renderEditor({ id: "beat_2", duration: 1, action: "wait", targets: [] }, { sceneKind: "three_d_scene" });
    const select = kindSelect();
    expect([...select.querySelectorAll("optgroup")].map((group) => group.label)).toEqual([
      "Wait",
      "Entrances",
      "Exits",
      "Changes",
      "Emphasis",
      "Camera",
    ]);
    expect(within(select).queryByRole("option", { name: "Write" })).toBeNull();
    expect(option(select, "Move camera")).toBeTruthy();
  });

  it("changes the kind with the targets that fit it, the selected object first", () => {
    const onEdit = renderEditor(INDICATE, { selectedObjectId: "dot_1" });
    fireEvent.change(kindSelect(), { target: { value: "fade_out" } });
    fireEvent.change(kindSelect(), { target: { value: "fade_in" } });
    fireEvent.change(kindSelect(), { target: { value: "wait" } });
    expect(onEdit.mock.calls).toEqual([
      [{ op: "set_beat_action", beatId: "beat_2", action: "fade_out", targets: ["text_1"] }],
      [{ op: "set_beat_action", beatId: "beat_2", action: "fade_in", targets: ["dot_1"] }],
      [{ op: "set_beat_action", beatId: "beat_2", action: "wait", targets: [] }],
    ]);
  });

  it("starts a morph from the selected object on screen, towards the first object off screen", () => {
    const onEdit = renderEditor(INDICATE, { selectedObjectId: "text_1" });
    fireEvent.change(kindSelect(), { target: { value: "transform" } });
    expect(onEdit).toHaveBeenCalledExactlyOnceWith({
      op: "set_beat_action",
      beatId: "beat_2",
      action: "transform",
      targets: ["text_1", "eq_1"],
    });
  });
});

describe("BeatEditor targets (spec §6, Beat editor)", () => {
  it("lists the objects on screen for a kind that acts on them", () => {
    renderEditor(INDICATE);
    const names = within(screen.getByTestId("beat-targets"))
      .getAllByRole("checkbox")
      .map((item) => item.closest("label")?.textContent);
    expect(names).toEqual(["Axes", "Sine", "Caption"]);
    expect(box("Caption").checked).toBe(true);
  });

  it("lists the objects off screen for an entrance", () => {
    renderEditor({ id: "beat_2", duration: 1, action: "fade_in", targets: ["dot_1"] });
    const names = within(screen.getByTestId("beat-targets"))
      .getAllByRole("checkbox")
      .map((item) => item.closest("label")?.textContent);
    expect(names).toEqual(["Formula", "Dot", "Circle"]);
  });

  it("sends the targets in canonical order, and checking axes checks what is drawn on them", () => {
    const onEdit = renderEditor(INDICATE);
    fireEvent.click(box("Axes"));
    expect(onEdit).toHaveBeenLastCalledWith({
      op: "set_beat_targets",
      beatId: "beat_2",
      targets: ["axes_1", "plot_1", "text_1"],
    });
    cleanup();
    const recolor = renderEditor({ ...INDICATE, action: "set_color", params: { color: "#E07A5F" } });
    fireEvent.click(box("Axes"));
    expect(recolor).toHaveBeenLastCalledWith({
      op: "set_beat_targets",
      beatId: "beat_2",
      targets: ["axes_1", "text_1"],
    });
  });

  it("unchecks a target, but never the last one", () => {
    const onEdit = renderEditor({ ...INDICATE, targets: ["plot_1", "text_1"] });
    expect(box("Caption").disabled).toBe(false);
    fireEvent.click(box("Caption"));
    expect(onEdit).toHaveBeenCalledExactlyOnceWith({ op: "set_beat_targets", beatId: "beat_2", targets: ["plot_1"] });
    cleanup();
    renderEditor(INDICATE);
    expect(box("Caption").disabled).toBe(true);
  });

  it("holds what is drawn on axes in an exit while the axes are checked", () => {
    const onEdit = renderEditor({ id: "beat_2", duration: 1, action: "fade_out", targets: ["axes_1", "plot_1"] });
    expect(box("Sine").disabled).toBe(true);
    expect(box("Sine").closest("label")?.textContent).toBe("SineLeaves with its axes");
    expect(box("Axes").disabled).toBe(false);
    fireEvent.click(box("Axes"));
    expect(onEdit).toHaveBeenCalledExactlyOnceWith({ op: "set_beat_targets", beatId: "beat_2", targets: ["plot_1"] });
  });

  it("marks a current target that cannot be a target here", () => {
    renderEditor({ ...INDICATE, targets: ["text_1", "dot_1"] });
    expect(box("Dot").closest("label")?.textContent).toBe("DotNot available at this point");
    expect(box("Caption").closest("label")?.textContent).toBe("Caption");
  });

  it("picks the two targets of a morph by role", () => {
    const onEdit = renderEditor({ id: "beat_2", duration: 1, action: "transform", targets: ["text_1", "eq_1"] });
    const from = screen.getByLabelText<HTMLSelectElement>("From");
    const to = screen.getByLabelText<HTMLSelectElement>("To");
    expect([...from.options].map((item) => item.textContent)).toEqual(["Axes", "Sine", "Caption"]);
    expect([...to.options].map((item) => item.textContent)).toEqual(["Formula", "Dot", "Circle"]);
    expect([from.value, to.value]).toEqual(["text_1", "eq_1"]);
    fireEvent.change(to, { target: { value: "circle_1" } });
    expect(onEdit).toHaveBeenCalledExactlyOnceWith({
      op: "set_beat_targets",
      beatId: "beat_2",
      targets: ["text_1", "circle_1"],
    });
  });

  it("picks the object and the path of a move along a path, and says where the object goes", () => {
    const beat: Beat = { id: "beat_2", duration: 1, action: "move_along", targets: ["dot_1", "plot_1"] };
    renderEditor(beat, { onScreen: ["axes_1", "plot_1", "dot_1", "circle_1"] });
    expect(screen.getByLabelText<HTMLSelectElement>("Object").value).toBe("dot_1");
    expect([...screen.getByLabelText<HTMLSelectElement>("Path").options].map((item) => item.value)).toEqual([
      "plot_1",
      "circle_1",
    ]);
    expect(screen.getByText("The object jumps to the start of the path when the beat begins.")).toBeTruthy();
    cleanup();
    renderEditor({ ...beat, easing: "there_and_back" }, { onScreen: ["axes_1", "plot_1", "dot_1"] });
    expect(
      screen.getByText("The object jumps to the start of the path when the beat begins, and ends back at the start."),
    ).toBeTruthy();
  });
});

describe("BeatEditor settings and easing (spec §6, Beat editor)", () => {
  it("edits a number setting, with its range", () => {
    const onEdit = renderEditor(SCALE);
    const factor = screen.getByLabelText<HTMLInputElement>("Factor");
    expect(factor.value).toBe("1.5");
    expect(screen.getByText("From 0.01 to 100.")).toBeTruthy();
    typeAndLeave(factor, "2");
    expect(onEdit).toHaveBeenCalledExactlyOnceWith({ op: "set_beat_param", beatId: "beat_2", key: "factor", value: 2 });
  });

  it("measures a rotation in degrees", () => {
    const onEdit = renderEditor({ ...SCALE, action: "rotate", params: { angle: 90 } });
    typeAndLeave(screen.getByLabelText("Angle (°)"), "-45");
    expect(screen.getByText("From -3600 to 3600.")).toBeTruthy();
    expect(onEdit).toHaveBeenCalledExactlyOnceWith({
      op: "set_beat_param",
      beatId: "beat_2",
      key: "angle",
      value: -45,
    });
  });

  it("edits each part of a vector setting", () => {
    const onEdit = renderEditor({ ...SCALE, action: "move_to", params: { point: [1, -2] } });
    expect(screen.getByText("Point", { selector: "legend" })).toBeTruthy();
    expect(screen.getByText("Each from -100 to 100.")).toBeTruthy();
    typeAndLeave(screen.getByLabelText("y"), "3");
    typeAndLeave(screen.getByLabelText("x"), "0,5");
    expect(onEdit.mock.calls).toEqual([
      [{ op: "set_beat_param", beatId: "beat_2", key: "point", value: [1, 3] }],
      [{ op: "set_beat_param", beatId: "beat_2", key: "point", value: [0.5, -2] }],
    ]);
  });

  it("edits a colour setting", () => {
    const onEdit = renderEditor({ ...SCALE, action: "set_color", params: { color: "#E07A5F" } });
    const hex = screen.getByTestId<HTMLInputElement>("beat-param-color");
    expect(hex.value).toBe("#E07A5F");
    typeAndLeave(hex, "#58C4DD");
    expect(onEdit).toHaveBeenCalledExactlyOnceWith({
      op: "set_beat_param",
      beatId: "beat_2",
      key: "color",
      value: "#58C4DD",
    });
  });

  it("edits the camera of a camera move, whose zoom must stay above 0", () => {
    const camera: Beat = {
      id: "beat_2",
      duration: 2,
      action: "move_camera",
      targets: [],
      params: { phi: 60, theta: 45, zoom: 1 },
    };
    const onEdit = renderEditor(camera, { sceneKind: "three_d_scene", onScreen: [] });
    expect(screen.getByLabelText<HTMLInputElement>("Phi (°)").value).toBe("60");
    expect(screen.getByLabelText<HTMLInputElement>("Theta (°)").value).toBe("45");
    expect(screen.getByText("Greater than 0.")).toBeTruthy();
    expect(screen.queryByTestId("beat-targets")).toBeNull();
    typeAndLeave(screen.getByTestId("beat-param-zoom"), "0.8");
    expect(onEdit).toHaveBeenCalledExactlyOnceWith({ op: "set_beat_param", beatId: "beat_2", key: "zoom", value: 0.8 });
  });

  it("offers the kind's easings after its default, and sends null for the default", () => {
    const onEdit = renderEditor(SCALE);
    const easing = screen.getByLabelText<HTMLSelectElement>("Easing");
    expect(easing.value).toBe("");
    expect([...easing.options].map((item) => item.textContent)).toEqual([
      "Default (Smooth)",
      "Linear",
      "Rush in",
      "Rush out",
      "Slow into",
      "Double smooth",
      "Lingering (arrives early, holds)",
      "There and back",
      "There and back, with a pause",
    ]);
    fireEvent.change(easing, { target: { value: "rush_into" } });
    expect(onEdit).toHaveBeenLastCalledWith({ op: "set_beat_easing", beatId: "beat_2", easing: "rush_into" });
    cleanup();
    const reset = renderEditor({ ...SCALE, easing: "linear" });
    expect(screen.getByLabelText<HTMLSelectElement>("Easing").value).toBe("linear");
    fireEvent.change(screen.getByLabelText("Easing"), { target: { value: "" } });
    expect(reset).toHaveBeenCalledExactlyOnceWith({ op: "set_beat_easing", beatId: "beat_2", easing: null });
  });

  it("names each kind's own default, and gives a wait no easing", () => {
    renderEditor(INDICATE);
    expect([...screen.getByLabelText<HTMLSelectElement>("Easing").options].map((item) => item.textContent)).toEqual([
      "Default (There and back)",
      "There and back, with a pause",
    ]);
    cleanup();
    renderEditor({ id: "beat_2", duration: 1, action: "wait", targets: [] });
    expect(screen.queryByLabelText("Easing")).toBeNull();
    expect(screen.queryByTestId("beat-targets")).toBeNull();
  });

  it("edits the duration", () => {
    const onEdit = renderEditor(SCALE);
    typeAndLeave(screen.getByTestId("beat-duration"), "2.5");
    expect(onEdit).toHaveBeenCalledExactlyOnceWith({ op: "set_beat_duration", beatId: "beat_2", duration: 2.5 });
  });
});

describe("BeatEditor order and delete (spec §6, Beat editor)", () => {
  it("moves the beat one place earlier or later, and deletes it", () => {
    const onEdit = renderEditor(INDICATE);
    fireEvent.click(screen.getByRole("button", { name: "Move earlier" }));
    fireEvent.click(screen.getByRole("button", { name: "Move later" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete beat" }));
    expect(onEdit.mock.calls).toEqual([
      [{ op: "move_beat", beatId: "beat_2", toIndex: 0 }],
      [{ op: "move_beat", beatId: "beat_2", toIndex: 2 }],
      [{ op: "delete_beat", beatId: "beat_2" }],
    ]);
  });

  it.each([
    ["first", 0, "Move earlier"],
    ["last", 2, "Move later"],
  ])("cannot move the %s beat past the end", (_, index, label) => {
    renderEditor(INDICATE, { index });
    expect(screen.getByRole<HTMLButtonElement>("button", { name: label }).disabled).toBe(true);
  });

  it("lets a template set a beat's kind and targets, and keeps it, but moves it and edits the rest", () => {
    const create: Beat = { id: "beat_2", duration: 2, action: "create", targets: ["plot_1"] };
    const onEdit = renderEditor(create, { templateId: "axes_function", onScreen: ["axes_1"] });
    expect(screen.getByTestId("beat-template").textContent).toBe("Set by the Axes and function template.");
    expect(kindSelect().disabled).toBe(true);
    expect(screen.getByTestId<HTMLFieldSetElement>("beat-targets").disabled).toBe(true);
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Delete beat" }).disabled).toBe(true);
    expect(screen.getByText("To remove a template's beat, delete one of its objects.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Move later" }));
    fireEvent.change(screen.getByLabelText("Easing"), { target: { value: "linear" } });
    expect(onEdit.mock.calls).toEqual([
      [{ op: "move_beat", beatId: "beat_2", toIndex: 2 }],
      [{ op: "set_beat_easing", beatId: "beat_2", easing: "linear" }],
    ]);
  });
});

describe("BeatEditor refusals (spec §6, Refusals in the UI)", () => {
  /** The element just before the refusal shown, which is the control it is about. */
  function shownAfter(error: FieldError, beat: Beat = SCALE): Element | null {
    renderEditor(beat, { fieldError: error });
    const shown = screen.getByTestId("beat-error-beat_2");
    expect(shown.textContent).toBe(error.message);
    return shown.previousElementSibling;
  }

  it.each([
    ["set_beat_action", "Kind"],
    ["set_beat_duration", "Duration"],
    ["set_beat_easing", "Easing"],
  ] as const)("shows a refused %s under %s", (op, label) => {
    expect(shownAfter(refusal(op))).toBe(screen.getByText(label).closest("label"));
  });

  it("shows a refused retarget under the targets", () => {
    expect(shownAfter(refusal("set_beat_targets"))).toBe(screen.getByTestId("beat-targets"));
  });

  it.each(["move_beat", "delete_beat"] as const)("shows a refused %s under the order buttons", (op) => {
    expect(shownAfter(refusal(op))?.contains(screen.getByRole("button", { name: "Delete beat" }))).toBe(true);
  });

  it("shows a refused setting under that setting only", () => {
    const beat: Beat = { ...SCALE, action: "move_camera", targets: [], params: { phi: 60, theta: 45, zoom: 1 } };
    renderEditor(beat, { sceneKind: "three_d_scene", fieldError: refusal("set_beat_param", "zoom") });
    const shown = screen.getAllByTestId("beat-error-beat_2");
    expect(shown).toHaveLength(1);
    expect(shown[0]?.closest(".beat-param")?.contains(screen.getByTestId("beat-param-zoom"))).toBe(true);
  });

  it("shows nothing for another beat's refusal", () => {
    renderEditor(SCALE, { fieldError: { ...refusal("set_beat_duration"), beatId: "beat_3" } });
    expect(screen.queryByTestId("beat-error-beat_2")).toBeNull();
  });
});
