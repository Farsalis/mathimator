// @vitest-environment jsdom
import { invoke } from "@tauri-apps/api/core";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Beat, Edit, ProjectDocument, SceneObject } from "../types";
import type { FieldError } from "../useEditor";
import { Inspector } from "./Inspector";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(() => Promise.resolve({ status: "unsupported" })),
  convertFileSrc: (path: string) => path,
}));

afterEach(cleanup);

const OBJECTS: SceneObject[] = [
  { id: "plot_1", name: "Plot 1", type: "function_plot", params: { expression: "x^" } },
  { id: "plot_2", name: "Plot 2", type: "function_plot", params: { expression: "sin(x)" } },
  {
    id: "eq_1",
    name: "Title",
    type: "math_formula",
    params: { latex: "y = \\sin(x)", shift: [1.5, 0], color: "#FFFFFF" },
  },
  { id: "axes_1", name: "Axes", type: "axes", params: { showLabels: true } },
  { id: "axes_2", name: "Plain axes", type: "axes", params: { showLabels: false } },
  { id: "text_1", name: "Caption", type: "text", params: { text: "Area", anchor: "edge_down", shift: [0, 0.5] } },
];

const UNSUPPORTED: FieldError = {
  objectId: "plot_1",
  beatId: null,
  field: "expression",
  op: "set_expression",
  axis: null,
  key: null,
  message: "This expression is not supported.",
};
const TOO_MANY_TICKS = "value has more than 500 ticks";

/** The input or group that `label` names: its label element, or the fieldset of a group. */
function fieldOf(label: string): Element | null {
  return screen.getByText(label).closest("fieldset, label");
}

function renderInspector(
  selectedObjectId: string,
  {
    fieldError = null,
    texBlocked = false,
    disabled = false,
    onEdit = vi.fn(() => Promise.resolve()),
  }: {
    fieldError?: FieldError | null;
    texBlocked?: boolean;
    disabled?: boolean;
    onEdit?: (edit: Edit) => Promise<void>;
  } = {},
): void {
  render(
    <Inspector
      scene={null}
      objects={OBJECTS}
      beats={[]}
      onScreen={[[]]}
      selectedBeatId={null}
      selectedObjectId={selectedObjectId}
      fieldError={fieldError}
      texBlocked={texBlocked}
      disabled={disabled}
      onSelectObject={vi.fn()}
      onEdit={onEdit}
    />,
  );
}

describe("Inspector", () => {
  it("shows a field error under the refused field of the edited object only", () => {
    renderInspector("plot_1", { fieldError: UNSUPPORTED });
    expect(screen.getByTestId("field-error-plot_1-expression").textContent).toBe(UNSUPPORTED.message);
    cleanup();
    renderInspector("plot_2", { fieldError: UNSUPPORTED });
    expect(screen.queryByText(UNSUPPORTED.message)).toBeNull();
    cleanup();
    renderInspector("plot_1", { fieldError: { ...UNSUPPORTED, field: "name" } });
    expect(screen.getByTestId("field-error-plot_1-name")).toBeTruthy();
    expect(screen.queryByTestId("field-error-plot_1-expression")).toBeNull();
  });

  it("shows a refused domain under x max, the field the worker names", () => {
    const message = "xMax must be greater than xMin";
    renderInspector("plot_2", {
      fieldError: { ...UNSUPPORTED, objectId: "plot_2", field: "xMax", op: "set_domain", message },
    });
    expect(fieldOf("x max")?.nextElementSibling).toBe(screen.getByText(message));
  });

  it.each([
    ["set_axes_range", "x", "X range"],
    ["set_axes_range", "y", "Y range"],
    ["set_length", "x", "X length"],
    ["set_length", "y", "Y length"],
  ] as const)("shows a refused %s on axis %s under %s only", (op, axis, label) => {
    renderInspector("axes_2", {
      fieldError: { ...UNSUPPORTED, objectId: "axes_2", field: "value", op, axis, message: TOO_MANY_TICKS },
    });
    const shown = screen.getAllByText(TOO_MANY_TICKS);
    expect(shown).toHaveLength(1);
    expect(fieldOf(label)?.nextElementSibling).toBe(shown[0]);
  });

  it.each([
    ["a formula", "eq_1", true],
    ["labelled axes", "axes_1", true],
    ["axes without labels", "axes_2", false],
  ])("says LaTeX must be reinstalled on %s while TeX is blocked", (_, objectId, shown) => {
    renderInspector(objectId, { texBlocked: true });
    expect(screen.queryByTestId("tex-blocked") !== null).toBe(shown);
    cleanup();
    renderInspector(objectId, { texBlocked: false });
    expect(screen.queryByTestId("tex-blocked")).toBeNull();
  });

  it("disables every field while the worker is not ready", () => {
    renderInspector("plot_1", { disabled: true });
    expect(screen.getByTestId("plot-expression").matches(":disabled")).toBe(true);
    expect(screen.getByTestId("delete-object").matches(":disabled")).toBe(true);
  });
});

describe("Inspector beat editor (spec §6, Selection and Beat editor)", () => {
  const graph: SceneObject[] = [
    { id: "axes_1", name: "Axes", type: "axes", params: { showLabels: false } },
    { id: "plot_1", name: "Sine", type: "function_plot", params: { axesId: "axes_1", expression: "sin(x)" } },
    { id: "text_1", name: "Caption", type: "text", params: { text: "Area", anchor: "edge_down", shift: [0, 0] } },
  ];
  const beats: Beat[] = [
    { id: "beat_1", duration: 2, action: "create", targets: ["axes_1"] },
    { id: "beat_2", duration: 2, action: "create", targets: ["plot_1"] },
    { id: "beat_3", duration: 1, action: "fade_in", targets: ["text_1"] },
  ];
  const scene: ProjectDocument["scene"] = {
    kind: "scene",
    camera: null,
    objects: graph,
    beats,
    templateInstances: [
      {
        id: "template_1",
        templateId: "axes_function",
        params: {},
        objectIds: ["axes_1", "plot_1"],
        beatIds: ["beat_1", "beat_2"],
      },
    ],
  };

  function renderSelected(selectedBeatId: string, selectedObjectId: string | null): void {
    render(
      <Inspector
        scene={scene}
        objects={graph}
        beats={beats}
        onScreen={[[], ["axes_1"], ["axes_1", "plot_1"], ["axes_1", "plot_1", "text_1"]]}
        selectedBeatId={selectedBeatId}
        selectedObjectId={selectedObjectId}
        fieldError={null}
        texBlocked={false}
        disabled={false}
        onSelectObject={vi.fn()}
        onEdit={vi.fn(() => Promise.resolve())}
      />,
    );
  }

  it("edits the selected beat where it stands, above the selected object's fields", () => {
    renderSelected("beat_3", "plot_1");
    const editor = screen.getByTestId("beat-editor");
    expect(within(editor).getByText("Fade in · beat_3 · scene.beats[2]")).toBeTruthy();
    // Only the caption is off screen before the third beat.
    expect(
      within(editor)
        .getAllByRole("checkbox")
        .map((item) => item.closest("label")?.textContent),
    ).toEqual(["Caption"]);
    const plotField = screen.getByTestId("plot-expression");
    expect(editor.compareDocumentPosition(plotField) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByTestId("beat-template")).toBeNull();
  });

  it("names the template that sets a beat's kind and targets", () => {
    renderSelected("beat_2", null);
    expect(screen.getByTestId("beat-template").textContent).toBe("Set by the Axes and function template.");
    expect(screen.getByLabelText<HTMLSelectElement>("Kind").disabled).toBe(true);
    expect(screen.queryByTestId("delete-object")).toBeNull();
  });

  describe("on a formula transform", () => {
    const formulas: SceneObject[] = [
      { id: "eq_1", name: "Before", type: "math_formula", params: { latex: "a+b" } },
      { id: "eq_2", name: "After", type: "math_formula", params: { latex: "b+a" } },
      { id: "text_1", name: "Caption", type: "text", params: { text: "Swap", anchor: "edge_down", shift: [0, 0] } },
    ];
    const morph: Beat[] = [
      { id: "beat_1", duration: 1, action: "write", targets: ["eq_1"] },
      { id: "beat_2", duration: 1, action: "fade_in", targets: ["text_1"] },
      { id: "beat_3", duration: 2, action: "transform_formula", targets: ["eq_1", "eq_2"] },
    ];

    function renderMorph(selectedObjectId: string | null): void {
      render(
        <Inspector
          scene={{ kind: "scene", camera: null, objects: formulas, beats: morph, templateInstances: [] }}
          objects={formulas}
          beats={morph}
          onScreen={[[], ["eq_1"], ["eq_1", "text_1"], ["eq_2", "text_1"]]}
          selectedBeatId="beat_3"
          selectedObjectId={selectedObjectId}
          fieldError={null}
          texBlocked={false}
          disabled={false}
          onSelectObject={vi.fn()}
          onEdit={vi.fn(() => Promise.resolve())}
        />,
      );
    }

    /** How many times each object's fields are shown. */
    function shownFields(): Record<string, number> {
      return Object.fromEntries(
        formulas.map((item) => [item.id, screen.queryAllByTestId(`object-name-${item.id}`).length]),
      );
    }

    it("shows both formulas below the editor, and the fields of another selected object", () => {
      renderMorph("text_1");
      expect(shownFields()).toEqual({ eq_1: 1, eq_2: 1, text_1: 1 });
      const editor = screen.getByTestId("beat-editor");
      const caption = screen.getByTestId("object-name-text_1");
      expect(editor.compareDocumentPosition(caption) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it.each([["eq_1"], ["eq_2"], [null]])("shows each formula once with %s selected", (selectedObjectId) => {
      renderMorph(selectedObjectId);
      expect(shownFields()).toEqual({ eq_1: 1, eq_2: 1, text_1: 0 });
    });
  });
});

describe("Inspector shape fields (spec §6, Shapes)", () => {
  const SHAPE_OBJECTS: SceneObject[] = [
    { id: "axes_1", name: "Axes", type: "axes", params: { showLabels: false } },
    { id: "axes_2", name: "Second axes", type: "axes", params: { showLabels: false } },
    { id: "axes3d_1", name: "3D axes", type: "three_d_axes", params: {} },
    { id: "dot_1", name: "Dot", type: "dot", params: { center: [1, -0.5], radius: 0.08, color: "#E07A5F" } },
    {
      id: "line_1",
      name: "Line",
      type: "line",
      params: { start: [-2, 0], end: [2, 0], color: "#FFFFFF", strokeWidth: 4 },
    },
    {
      id: "arrow_1",
      name: "Arrow",
      type: "arrow",
      params: { start: [-2, -1], end: [2, 1], color: "#FFFFFF", strokeWidth: 6 },
    },
    {
      id: "circle_1",
      name: "Circle",
      type: "circle",
      params: { center: [0, 0], radius: 1, color: "#58C4DD", strokeWidth: 4, fillOpacity: 0 },
    },
    {
      id: "rectangle_1",
      name: "Rectangle",
      type: "rectangle",
      params: { center: [0, 1], width: 4, height: 2, color: "#58C4DD", strokeWidth: 4, fillOpacity: 0.3 },
    },
    {
      id: "point_1",
      name: "Point",
      type: "axes_point",
      params: { axesId: "axes_2", x: 1.5, y: -2, radius: 0.08, color: "#E07A5F" },
    },
  ];

  function renderShape(
    selectedObjectId: string,
    { fieldError = null }: { fieldError?: FieldError | null } = {},
  ): (edit: Edit) => Promise<void> {
    const onEdit = vi.fn<(edit: Edit) => Promise<void>>(() => Promise.resolve());
    render(
      <Inspector
        scene={null}
        objects={SHAPE_OBJECTS}
        beats={[]}
        onScreen={[[]]}
        selectedBeatId={null}
        selectedObjectId={selectedObjectId}
        fieldError={fieldError}
        texBlocked={false}
        disabled={false}
        onSelectObject={vi.fn()}
        onEdit={onEdit}
      />,
    );
    return onEdit;
  }

  /** The test ids of the shape's controls, in the order they are shown. */
  function shapeControls(): (string | undefined)[] {
    return [...document.querySelectorAll<HTMLElement>('[data-testid^="shape-param-"]')].map(
      (item) => item.dataset.testid,
    );
  }

  function shapeField(key: string): HTMLInputElement {
    return screen.getByTestId<HTMLInputElement>(`shape-param-${key}`);
  }

  it.each([
    ["dot_1", ["center-x", "center-y", "radius", "color"]],
    ["line_1", ["start-x", "start-y", "end-x", "end-y", "strokeWidth", "color"]],
    ["arrow_1", ["start-x", "start-y", "end-x", "end-y", "strokeWidth", "color"]],
    ["circle_1", ["center-x", "center-y", "radius", "strokeWidth", "fillOpacity", "color"]],
    ["rectangle_1", ["center-x", "center-y", "width", "height", "strokeWidth", "fillOpacity", "color"]],
    ["point_1", ["axesId", "x", "y", "radius", "color"]],
  ])("shows the settings of %s with the colour last", (objectId, keys) => {
    renderShape(objectId);
    expect(shapeControls()).toEqual(keys.map((key) => `shape-param-${key}`));
  });

  it("shows each setting's value, label and range", () => {
    renderShape("rectangle_1");
    expect([shapeField("center-x").value, shapeField("center-y").value]).toEqual(["0", "1"]);
    expect(shapeField("fillOpacity").value).toBe("0.3");
    expect(shapeField("color").value).toBe("#58C4DD");
    expect(fieldOf("Center")?.textContent).toContain("Each from -100 to 100.");
    expect(fieldOf("Width")?.nextElementSibling?.textContent).toBe("From 0.001 to 100.");
    expect(fieldOf("Stroke width")?.nextElementSibling?.textContent).toBe("From 0 to 50.");
    expect(fieldOf("Fill opacity")?.nextElementSibling?.textContent).toBe("From 0 to 1.");
  });

  it("sends each setting of a shape as set_shape_param", () => {
    const onEdit = renderShape("circle_1");
    typeAndLeave(shapeField("center-x"), "2");
    typeAndLeave(shapeField("radius"), "1,5");
    typeAndLeave(shapeField("strokeWidth"), "8");
    typeAndLeave(shapeField("fillOpacity"), "0.4");
    typeAndLeave(shapeField("color"), "#FF0000");
    expect(vi.mocked(onEdit).mock.calls).toEqual(
      [
        { key: "center", value: [2, 0] },
        { key: "radius", value: 1.5 },
        { key: "strokeWidth", value: 8 },
        { key: "fillOpacity", value: 0.4 },
        { key: "color", value: "#FF0000" },
      ].map((change) => [{ op: "set_shape_param", objectId: "circle_1", ...change }]),
    );
  });

  it("moves either end of a line on its own", () => {
    const onEdit = renderShape("line_1");
    typeAndLeave(shapeField("start-y"), "-1");
    typeAndLeave(shapeField("end-x"), "3");
    expect(vi.mocked(onEdit).mock.calls).toEqual([
      [{ op: "set_shape_param", objectId: "line_1", key: "start", value: [-2, -1] }],
      [{ op: "set_shape_param", objectId: "line_1", key: "end", value: [3, 0] }],
    ]);
  });

  it("puts a point on any 2D axes, at coordinates on those axes", () => {
    const onEdit = renderShape("point_1");
    const axes = screen.getByLabelText<HTMLSelectElement>("Axes");
    expect(axes.value).toBe("axes_2");
    expect([...axes.options].map((item) => item.textContent)).toEqual(["Axes", "Second axes"]);
    expect(screen.getByText("x and y are coordinates on these axes.")).toBeTruthy();
    expect([shapeField("x").value, shapeField("y").value]).toEqual(["1.5", "-2"]);
    fireEvent.change(axes, { target: { value: "axes_1" } });
    typeAndLeave(shapeField("y"), "0.25");
    expect(vi.mocked(onEdit).mock.calls).toEqual([
      [{ op: "set_shape_param", objectId: "point_1", key: "axesId", value: "axes_1" }],
      [{ op: "set_shape_param", objectId: "point_1", key: "y", value: 0.25 }],
    ]);
  });

  it("shows a refused setting under that setting only (spec §6, Refusals in the UI)", () => {
    const message = "value must be from 0.001 to 50";
    const refusal: FieldError = {
      objectId: "circle_1",
      beatId: null,
      field: "value",
      op: "set_shape_param",
      axis: null,
      key: "radius",
      message,
    };
    renderShape("circle_1", { fieldError: refusal });
    const shown = screen.getAllByText(message);
    expect(shown).toHaveLength(1);
    // Under the radius and its range.
    expect(fieldOf("Radius")?.nextElementSibling?.nextElementSibling).toBe(shown[0]);
    cleanup();
    renderShape("dot_1", { fieldError: refusal });
    expect(screen.queryByText(message)).toBeNull();
  });
});

/** The colour picker of the one colour field shown, and the hex field beside it. */
function colorInputs(): { picker: HTMLInputElement; hex: HTMLInputElement } {
  const picker = document.querySelector<HTMLInputElement>('input[type="color"]');
  const hex = picker?.nextElementSibling;
  if (!picker || !(hex instanceof HTMLInputElement)) {
    throw new Error("No colour field is shown.");
  }
  return { picker, hex };
}

function typeAndLeave(input: HTMLInputElement, value: string): void {
  fireEvent.change(input, { target: { value } });
  fireEvent.blur(input);
}

/** The arguments of every compile the formula preview sent. */
function compiles(): unknown[] {
  return vi
    .mocked(invoke)
    .mock.calls.filter(([command]) => command === "compile_equation_preview")
    .map(([, args]) => args);
}

describe("Inspector fields", () => {
  afterEach(() => vi.mocked(invoke).mockClear());

  it("commits a colour once, when the picker closes", () => {
    const onEdit = vi.fn(() => Promise.resolve());
    renderInspector("eq_1", { onEdit });
    const { picker, hex } = colorInputs();
    for (let shade = 0; shade < 10; shade += 1) {
      fireEvent.input(picker, { target: { value: `#ff00${shade}0` } });
    }
    expect(hex.value).toBe("#FF0090");
    expect(onEdit).not.toHaveBeenCalled();
    fireEvent.change(picker);
    expect(onEdit).toHaveBeenCalledExactlyOnceWith({ op: "set_object_color", objectId: "eq_1", color: "#FF0090" });
  });

  it("sends no edit for a colour that differs only in case", () => {
    const onEdit = vi.fn(() => Promise.resolve());
    renderInspector("eq_1", { onEdit });
    const { picker, hex } = colorInputs();
    typeAndLeave(hex, "#ffffff");
    fireEvent.input(picker, { target: { value: "#ffffff" } });
    fireEvent.change(picker);
    expect(onEdit).not.toHaveBeenCalled();
    expect(hex.value).toBe("#FFFFFF");
  });

  it.each([
    ["an empty field", "  "],
    ["text that is not a decimal", "1e3"],
    ["the same number", "1.50"],
  ])("shows the value again for %s and sends no edit", (_, text) => {
    const onEdit = vi.fn(() => Promise.resolve());
    renderInspector("eq_1", { onEdit });
    const dx = screen.getByLabelText<HTMLInputElement>("dx");
    typeAndLeave(dx, text);
    expect(onEdit).not.toHaveBeenCalled();
    expect(dx.value).toBe("1.5");
  });

  it("reads a comma as the decimal point", () => {
    const onEdit = vi.fn(() => Promise.resolve());
    renderInspector("eq_1", { onEdit });
    typeAndLeave(screen.getByLabelText("dx"), " 2,5 ");
    expect(onEdit).toHaveBeenCalledExactlyOnceWith({ op: "set_object_shift", objectId: "eq_1", shift: [2.5, 0] });
  });

  it("commits a number once on Enter", () => {
    const onEdit = vi.fn(() => Promise.resolve());
    renderInspector("eq_1", { onEdit });
    const dy = screen.getByLabelText<HTMLInputElement>("dy");
    dy.focus();
    fireEvent.change(dy, { target: { value: "-2" } });
    fireEvent.keyDown(dy, { key: "Enter" });
    expect(document.activeElement).not.toBe(dy);
    expect(onEdit).toHaveBeenCalledExactlyOnceWith({ op: "set_object_shift", objectId: "eq_1", shift: [1.5, -2] });
  });

  it("commits a hex colour typed in the field when it loses focus", () => {
    const onEdit = vi.fn(() => Promise.resolve());
    renderInspector("eq_1", { onEdit });
    const { picker, hex } = colorInputs();
    fireEvent.change(hex, { target: { value: "#58C4DD" } });
    expect(picker.value).toBe("#58c4dd");
    expect(onEdit).not.toHaveBeenCalled();
    fireEvent.blur(hex);
    expect(onEdit).toHaveBeenCalledExactlyOnceWith({ op: "set_object_color", objectId: "eq_1", color: "#58C4DD" });
  });

  it("places a text with the same anchor, colour and shift fields as a formula", () => {
    const onEdit = vi.fn(() => Promise.resolve());
    renderInspector("text_1", { onEdit });
    const anchor = screen.getByLabelText<HTMLSelectElement>("Anchor");
    expect(anchor.value).toBe("edge_down");
    fireEvent.change(anchor, { target: { value: "origin" } });
    typeAndLeave(screen.getByLabelText("dx"), "3");
    expect(onEdit.mock.calls).toEqual([
      [{ op: "set_anchor", objectId: "text_1", anchor: "origin" }],
      [{ op: "set_object_shift", objectId: "text_1", shift: [3, 0.5] }],
    ]);
  });

  it("edits a surface's expression with its variables and refusal shown, as a plot's", () => {
    const surface: SceneObject = { id: "surface_1", name: "Surface", type: "surface", params: { expression: "x*y" } };
    const onEdit = vi.fn(() => Promise.resolve());
    render(
      <Inspector
        scene={{
          kind: "three_d_scene",
          camera: { phi: 75, theta: -45, zoom: 1 },
          objects: [surface],
          beats: [],
          templateInstances: [],
        }}
        objects={[surface]}
        beats={[]}
        onScreen={[[]]}
        selectedBeatId={null}
        selectedObjectId={null}
        fieldError={{ ...UNSUPPORTED, objectId: "surface_1" }}
        texBlocked={false}
        disabled={false}
        onSelectObject={vi.fn()}
        onEdit={onEdit}
      />,
    );
    const field = screen.getByTestId<HTMLInputElement>("surface-expression");
    expect(field.value).toBe("x*y");
    expect(screen.getByTestId("field-error-surface_1-expression").textContent).toBe(UNSUPPORTED.message);
    expect(screen.getByText("Natural log is log. Variables: x and y.")).toBeTruthy();
    // Spec §3, Journey C: the camera turns only in Move camera beats.
    expect(screen.getByTestId("cairo-limits").textContent).toBe(
      "Cairo limits: software rendering, flat-shaded faces, a camera that turns only in Move camera beats, with no " +
        "orbit drag, and a resolution cap of 24×24.",
    );
    typeAndLeave(field, "x + y");
    expect(onEdit).toHaveBeenCalledExactlyOnceWith({
      op: "set_expression",
      objectId: "surface_1",
      expression: "x + y",
    });
  });

  it("keeps a number while its edit is pending and shows the stored value once it is refused", async () => {
    let settle: () => void = () => undefined;
    const pending = new Promise<void>((resolve) => {
      settle = resolve;
    });
    renderInspector("eq_1", { onEdit: () => pending });
    const dx = screen.getByLabelText<HTMLInputElement>("dx");
    typeAndLeave(dx, "-4");
    expect(dx.value).toBe("-4");
    await act(async () => {
      settle();
      await pending;
    });
    expect(dx.value).toBe("1.5");
  });

  it("previews the formula being typed in its own slot, and the stored one after a refusal", async () => {
    renderInspector("eq_1");
    await waitFor(() => expect(compiles()).toHaveLength(1));
    expect(compiles()[0]).toMatchObject({ latex: "y = \\sin(x)", slot: "eq_1" });
    const field = screen.getByTestId<HTMLInputElement>("formula-latex");
    fireEvent.change(field, { target: { value: "y = \\cos(x)" } });
    await waitFor(() => expect(compiles().at(-1)).toMatchObject({ latex: "y = \\cos(x)", slot: "eq_1" }));
    fireEvent.blur(field);
    await waitFor(() => expect(field.value).toBe("y = \\sin(x)"));
    await waitFor(() => expect(compiles().at(-1)).toMatchObject({ latex: "y = \\sin(x)", slot: "eq_1" }));
  });
});
