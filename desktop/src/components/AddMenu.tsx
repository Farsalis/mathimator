import { useEffect, useId, useRef, useState } from "react";
import { SHAPE_TYPES, SHAPES, type ShapeType } from "../beatKinds";

/** What Add inserts after the selected beat: a template, or a shape with its entrance beat. */
export type AddKind = "axes_function" | "formula" | "text" | "formula_transform" | "three_d_axes" | ShapeType;

interface AddMenuProps {
  disabled: boolean;
  /** The document holds 2D axes, so a point can be put on them. */
  canAddPoint: boolean;
  onAdd: (kind: AddKind) => void;
}

export function AddMenu({ disabled, canAddPoint, onAdd }: AddMenuProps) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const shapesHeading = useId();

  useEffect(() => {
    if (!open) {
      return;
    }
    function onPointerDown(event: PointerEvent): void {
      if (!root.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  function choose(kind: AddKind): void {
    setOpen(false);
    onAdd(kind);
  }

  return (
    <div className="add-menu" ref={root}>
      <button
        type="button"
        data-testid="add-button"
        aria-expanded={open}
        aria-haspopup="menu"
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
      >
        Add
      </button>
      {open ? (
        <ul className="menu" role="menu">
          <li>
            <button
              type="button"
              role="menuitem"
              data-testid="add-axes-function"
              onClick={() => choose("axes_function")}
            >
              Axes and function
            </button>
          </li>
          <li>
            <button type="button" role="menuitem" data-testid="add-formula" onClick={() => choose("formula")}>
              Formula
            </button>
          </li>
          <li>
            <button type="button" role="menuitem" data-testid="add-text" onClick={() => choose("text")}>
              Text
            </button>
          </li>
          <li>
            <button
              type="button"
              role="menuitem"
              data-testid="add-formula-transform"
              onClick={() => choose("formula_transform")}
            >
              Formula transform
            </button>
          </li>
          <li>
            <button type="button" role="menuitem" data-testid="add-three-d-axes" onClick={() => choose("three_d_axes")}>
              3D axes
            </button>
          </li>
          <li role="none">
            <span className="menu-heading" id={shapesHeading}>
              Shapes
            </span>
            <ul className="menu-group" role="group" aria-labelledby={shapesHeading}>
              {SHAPE_TYPES.map((type) => {
                // A point is put on axes, so it waits for some.
                const blocked = type === "axes_point" && !canAddPoint;
                return (
                  <li key={type}>
                    <button
                      type="button"
                      role="menuitem"
                      data-testid={`add-${type.replaceAll("_", "-")}`}
                      disabled={blocked}
                      title={blocked ? "Add axes first" : undefined}
                      onClick={() => choose(type)}
                    >
                      {SHAPES[type].label}
                    </button>
                  </li>
                );
              })}
            </ul>
          </li>
        </ul>
      ) : null}
    </div>
  );
}
