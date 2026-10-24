// @vitest-environment jsdom
import { invoke } from "@tauri-apps/api/core";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Edit, SceneObject } from "../types";
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
];

const UNSUPPORTED: FieldError = {
  objectId: "plot_1",
  field: "expression",
  op: "set_expression",
  axis: null,
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
  it("shows a field error under the refused field of the edited object only (C9.3, F112)", () => {
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

  it("shows a refused domain under x max, the field the worker names (C9.3)", () => {
    const message = "xMax must be greater than xMin";
    renderInspector("plot_2", {
      fieldError: { objectId: "plot_2", field: "xMax", op: "set_domain", axis: null, message },
    });
    expect(fieldOf("x max")?.nextElementSibling).toBe(screen.getByText(message));
  });

  it.each([
    ["set_axes_range", "x", "X range"],
    ["set_axes_range", "y", "Y range"],
    ["set_length", "x", "X length"],
    ["set_length", "y", "Y length"],
  ] as const)("shows a refused %s on axis %s under %s only (C9.3)", (op, axis, label) => {
    renderInspector("axes_2", {
      fieldError: { objectId: "axes_2", field: "value", op, axis, message: TOO_MANY_TICKS },
    });
    const shown = screen.getAllByText(TOO_MANY_TICKS);
    expect(shown).toHaveLength(1);
    expect(fieldOf(label)?.nextElementSibling).toBe(shown[0]);
  });

  it.each([
    ["a formula", "eq_1", true],
    ["labelled axes", "axes_1", true],
    ["axes without labels", "axes_2", false],
  ])("says LaTeX must be reinstalled on %s while TeX is blocked (C14 Q4)", (_, objectId, shown) => {
    renderInspector(objectId, { texBlocked: true });
    expect(screen.queryByTestId("tex-blocked") !== null).toBe(shown);
    cleanup();
    renderInspector(objectId, { texBlocked: false });
    expect(screen.queryByTestId("tex-blocked")).toBeNull();
  });

  it("disables every field while the worker is not ready (C9.5)", () => {
    renderInspector("plot_1", { disabled: true });
    expect(screen.getByTestId("plot-expression").matches(":disabled")).toBe(true);
    expect(screen.getByTestId("delete-object").matches(":disabled")).toBe(true);
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

  it("commits a colour once, when the picker closes (D08, C10)", () => {
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

  it("sends no edit for a colour that differs only in case (D08)", () => {
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
  ])("shows the value again for %s and sends no edit (D24)", (_, text) => {
    const onEdit = vi.fn(() => Promise.resolve());
    renderInspector("eq_1", { onEdit });
    const dx = screen.getByLabelText<HTMLInputElement>("dx");
    typeAndLeave(dx, text);
    expect(onEdit).not.toHaveBeenCalled();
    expect(dx.value).toBe("1.5");
  });

  it("reads a comma as the decimal point (D24)", () => {
    const onEdit = vi.fn(() => Promise.resolve());
    renderInspector("eq_1", { onEdit });
    typeAndLeave(screen.getByLabelText("dx"), " 2,5 ");
    expect(onEdit).toHaveBeenCalledExactlyOnceWith({ op: "set_object_shift", objectId: "eq_1", shift: [2.5, 0] });
  });

  it("keeps a number while its edit is pending and shows the stored value once it is refused (F045)", async () => {
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

  it("previews the formula being typed in its own slot, and the stored one after a refusal (C9.9)", async () => {
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
