import { useEffect, useRef, useState } from "react";

export type AddKind = "axes_function" | "formula" | "text" | "formula_transform" | "three_d_axes";

interface AddMenuProps {
  disabled: boolean;
  onAdd: (kind: AddKind) => void;
}

export function AddMenu({ disabled, onAdd }: AddMenuProps) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

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
            <button type="button" role="menuitem" data-testid="add-axes-function" onClick={() => choose("axes_function")}>
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
        </ul>
      ) : null}
    </div>
  );
}
