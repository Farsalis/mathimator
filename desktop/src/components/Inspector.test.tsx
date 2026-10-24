// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SceneObject } from "../types";
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
  { id: "eq_1", name: "Title", type: "math_formula", params: { latex: "y = \\sin(x)" } },
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
  }: { fieldError?: FieldError | null; texBlocked?: boolean; disabled?: boolean } = {},
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
      onEdit={vi.fn()}
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
