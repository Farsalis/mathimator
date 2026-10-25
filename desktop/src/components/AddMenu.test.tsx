// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AddMenu, type AddKind } from "./AddMenu";

afterEach(cleanup);

function openMenu(canAddPoint: boolean) {
  const onAdd = vi.fn<(kind: AddKind) => void>();
  render(<AddMenu disabled={false} canAddPoint={canAddPoint} onAdd={onAdd} />);
  fireEvent.click(screen.getByTestId("add-button"));
  return onAdd;
}

function shapes(): HTMLElement {
  return screen.getByRole("group", { name: "Shapes" });
}

describe("AddMenu (design §10.6)", () => {
  it("lists the shapes in a group after the templates", () => {
    openMenu(true);
    const items = screen.getAllByRole("menuitem").map((item) => item.textContent);
    expect(items).toEqual([
      "Axes and function",
      "Formula",
      "Text",
      "Formula transform",
      "3D axes",
      "Dot",
      "Line",
      "Arrow",
      "Circle",
      "Rectangle",
      "Point on axes",
    ]);
    expect(
      within(shapes())
        .getAllByRole("menuitem")
        .map((item) => item.dataset.testid),
    ).toEqual(["add-dot", "add-line", "add-arrow", "add-circle", "add-rectangle", "add-axes-point"]);
  });

  it.each([
    ["add-dot", "dot"],
    ["add-line", "line"],
    ["add-arrow", "arrow"],
    ["add-circle", "circle"],
    ["add-rectangle", "rectangle"],
    ["add-axes-point", "axes_point"],
  ] as const)("adds with %s and closes", (testId, kind) => {
    const onAdd = openMenu(true);
    fireEvent.click(screen.getByTestId(testId));
    expect(onAdd).toHaveBeenCalledExactlyOnceWith(kind);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("disables Point on axes until the document holds axes, and says why", () => {
    const onAdd = openMenu(false);
    const point = screen.getByTestId<HTMLButtonElement>("add-axes-point");
    expect(point.disabled).toBe(true);
    expect(point.title).toBe("Add axes first");
    fireEvent.click(point);
    expect(onAdd).not.toHaveBeenCalled();
    const others = within(shapes())
      .getAllByRole<HTMLButtonElement>("menuitem")
      .filter((item) => item !== point);
    expect(others.map((item) => item.disabled)).toEqual([false, false, false, false, false]);
    cleanup();
    openMenu(true);
    expect(screen.getByTestId<HTMLButtonElement>("add-axes-point").title).toBe("");
  });
});
