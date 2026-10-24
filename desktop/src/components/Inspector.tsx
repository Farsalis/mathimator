import { useEffect, useState } from "react";
import type { Anchor, Beat, BeatAction, Camera, Edit, ProjectDocument, SceneObject, ThreeDMode } from "../types";
import { FormulaPreview } from "./FormulaPreview";

interface InspectorProps {
  scene: ProjectDocument["scene"] | null;
  objects: SceneObject[];
  beats: Beat[];
  selectedBeatId: string | null;
  selectedObjectId: string | null;
  fieldError: string | null;
  onSelectObject: (objectId: string) => void;
  onEdit: (edit: Edit) => void;
}

export function Inspector({
  scene,
  objects,
  beats,
  selectedBeatId,
  selectedObjectId,
  fieldError,
  onSelectObject,
  onEdit,
}: InspectorProps) {
  const beat = beats.find((item) => item.id === selectedBeatId) ?? null;
  const selectedFromBeat =
    beat && beat.targets.length === 1 ? objects.find((item) => item.id === beat.targets[0]) : null;
  const selectedObject = objects.find((item) => item.id === selectedObjectId) ?? selectedFromBeat ?? null;
  const targets = beat ? objects.filter((item) => beat.targets.includes(item.id)) : [];

  return (
    <aside className="inspector" aria-label="Inspector" data-testid="inspector">
      <h2>Objects</h2>
      {objects.length === 0 ? <p className="hint">Nothing in the scene yet.</p> : null}
      {selectedObject ? (
        <button
          type="button"
          data-testid="delete-object"
          onClick={() => onEdit({ op: "delete_object", objectId: selectedObject.id })}
        >
          Delete object
        </button>
      ) : null}
      <ul className="object-list">
        {objects.map((item) => (
          <li key={item.id}>
            <button
              type="button"
              className={item.id === selectedObject?.id ? "object selected" : "object"}
              onClick={() => onSelectObject(item.id)}
            >
              {item.name}
              <span>{item.type}</span>
            </button>
          </li>
        ))}
      </ul>
      {scene?.kind === "three_d_scene" && scene.camera ? (
        <ThreeDPanel scene={scene} camera={scene.camera} objects={objects} fieldError={fieldError} onEdit={onEdit} />
      ) : null}
      {beat ? <BeatFields beat={beat} onEdit={onEdit} /> : null}
      {beat && beat.action === "transform_formula"
        ? targets.map((item) => <ObjectFields key={item.id} object={item} fieldError={fieldError} onEdit={onEdit} />)
        : null}
      {selectedObject && beat?.action !== "transform_formula" ? (
        <ObjectFields key={selectedObject.id} object={selectedObject} fieldError={fieldError} onEdit={onEdit} />
      ) : null}
    </aside>
  );
}

function BeatFields({ beat, onEdit }: { beat: Beat; onEdit: (edit: Edit) => void }) {
  return (
    <section className="fields">
      <h2>Beat</h2>
      <p className="hint">
        {beat.action} · {beat.id}
      </p>
      <CommitNumber
        label="Duration"
        value={beat.duration}
        testId="beat-duration"
        onCommit={(duration) => onEdit({ op: "set_beat_duration", beatId: beat.id, duration })}
      />
      <ActionFields beat={beat} onEdit={onEdit} />
    </section>
  );
}

function ActionFields({ beat, onEdit }: { beat: Beat; onEdit: (edit: Edit) => void }) {
  const action: BeatAction = beat.action;
  switch (action) {
    case "set_color":
      return (
        <ColorField
          label="Beat color"
          value={stringParam(beat.params?.color, "#FFFFFF")}
          onCommit={(color) => onEdit({ op: "set_beat_color", beatId: beat.id, color })}
        />
      );
    case "move":
      return (
        <ShiftFields
          value={pairParam(beat.params?.shift)}
          onCommit={(shift) => onEdit({ op: "set_beat_shift", beatId: beat.id, shift })}
        />
      );
    case "create":
    case "fade_in":
    case "write":
    case "wait":
    case "transform_formula":
      return null;
    default: {
      const unexpected: never = action;
      return unexpected;
    }
  }
}

function ObjectFields({
  object,
  fieldError,
  onEdit,
}: {
  object: SceneObject;
  fieldError: string | null;
  onEdit: (edit: Edit) => void;
}) {
  return (
    <section className="fields">
      <h2>{object.name}</h2>
      <CommitText
        label="Name"
        value={object.name}
        testId={`object-name-${object.id}`}
        onCommit={(name) => onEdit({ op: "set_object_name", objectId: object.id, name })}
      />
      <TypedFields object={object} fieldError={fieldError} onEdit={onEdit} />
    </section>
  );
}

function TypedFields({
  object,
  fieldError,
  onEdit,
}: {
  object: SceneObject;
  fieldError: string | null;
  onEdit: (edit: Edit) => void;
}) {
  switch (object.type) {
    case "axes":
      return <AxesFields object={object} onEdit={onEdit} />;
    case "function_plot":
      return <PlotFields object={object} fieldError={fieldError} onEdit={onEdit} />;
    case "math_formula":
      return <FormulaFields object={object} onEdit={onEdit} />;
    case "text":
      return <TextFields object={object} onEdit={onEdit} />;
    case "three_d_axes":
    case "surface":
    case "parametric_curve":
      return null;
    default: {
      const unexpected: never = object.type;
      return unexpected;
    }
  }
}

function ThreeDPanel({
  scene,
  camera,
  objects,
  fieldError,
  onEdit,
}: {
  scene: ProjectDocument["scene"];
  camera: Camera;
  objects: SceneObject[];
  fieldError: string | null;
  onEdit: (edit: Edit) => void;
}) {
  const template = scene.templateInstances.find((item) => item.templateId === "three_d_axes");
  const mode: ThreeDMode = template?.params.mode === "curve" ? "curve" : "surface";
  const surface = objects.find((item) => item.type === "surface");
  const curve = objects.find((item) => item.type === "parametric_curve");
  const resolution = pairParam(surface?.params.resolution);
  const components = tripleStrings(curve?.params.components);
  return (
    <section className="fields" data-testid="three-d-panel">
      <h2>3D template</h2>
      <p className="hint" data-testid="cairo-limits">
        Cairo limits: software rendering, flat-shaded faces, a static camera, and a resolution cap of 24×24.
      </p>
      <label>
        Mode
        <select
          data-testid="three-d-mode"
          value={mode}
          onChange={(event) => onEdit({ op: "set_three_d_mode", mode: modeFromSelect(event.target.value) })}
        >
          <option value="surface">Surface</option>
          <option value="curve">Curve</option>
        </select>
      </label>
      {mode === "surface" && surface ? (
        <>
          <CommitText
            label="Expression"
            value={stringParam(surface.params.expression, "sin(x)*cos(y)")}
            testId="surface-expression"
            onCommit={(expression) => onEdit({ op: "set_expression", objectId: surface.id, expression })}
          />
          {fieldError ? <p className="field-error">{fieldError}</p> : null}
          <p className="hint">Natural log is log. Variables: x and y.</p>
          <CommitNumber
            label="Resolution u"
            value={resolution[0]}
            testId="resolution-u"
            onCommit={(u) => onEdit({ op: "set_resolution", objectId: surface.id, value: [u, resolution[1]] })}
          />
          <CommitNumber
            label="Resolution v"
            value={resolution[1]}
            testId="resolution-v"
            onCommit={(v) => onEdit({ op: "set_resolution", objectId: surface.id, value: [resolution[0], v] })}
          />
        </>
      ) : null}
      {mode === "curve" && curve ? (
        <>
          <CommitText
            label="x(t)"
            value={components[0]}
            testId="curve-x"
            onCommit={(expression) => onEdit({ op: "set_curve_component", objectId: curve.id, index: 0, expression })}
          />
          <CommitText
            label="y(t)"
            value={components[1]}
            testId="curve-y"
            onCommit={(expression) => onEdit({ op: "set_curve_component", objectId: curve.id, index: 1, expression })}
          />
          <CommitText
            label="z(t)"
            value={components[2]}
            testId="curve-z"
            onCommit={(expression) => onEdit({ op: "set_curve_component", objectId: curve.id, index: 2, expression })}
          />
          {fieldError ? <p className="field-error">{fieldError}</p> : null}
          <p className="hint">Natural log is log. Variable: t.</p>
        </>
      ) : null}
      <CommitNumber
        label="Phi"
        value={camera.phi}
        testId="camera-phi"
        onCommit={(value) => onEdit({ op: "set_camera", key: "phi", value })}
      />
      <CommitNumber
        label="Theta"
        value={camera.theta}
        testId="camera-theta"
        onCommit={(value) => onEdit({ op: "set_camera", key: "theta", value })}
      />
      <CommitNumber
        label="Zoom"
        value={camera.zoom}
        testId="camera-zoom"
        onCommit={(value) => onEdit({ op: "set_camera", key: "zoom", value })}
      />
    </section>
  );
}

function modeFromSelect(value: string): ThreeDMode {
  switch (value) {
    case "surface":
      return "surface";
    case "curve":
      return "curve";
    default:
      return "surface";
  }
}

function AxesFields({ object, onEdit }: { object: SceneObject; onEdit: (edit: Edit) => void }) {
  const xRange = tripleParam(object.params.xRange);
  const yRange = tripleParam(object.params.yRange);
  return (
    <>
      <ColorField
        label="Color"
        value={stringParam(object.params.color, "#FFFFFF")}
        onCommit={(color) => onEdit({ op: "set_object_color", objectId: object.id, color })}
      />
      <RangeFields
        label="X range"
        value={xRange}
        onCommit={(value) => onEdit({ op: "set_axes_range", objectId: object.id, axis: "x", value })}
      />
      <RangeFields
        label="Y range"
        value={yRange}
        onCommit={(value) => onEdit({ op: "set_axes_range", objectId: object.id, axis: "y", value })}
      />
      <CommitNumber
        label="X length"
        value={numberParam(object.params.xLength, 10)}
        onCommit={(value) => onEdit({ op: "set_length", objectId: object.id, axis: "x", value })}
      />
      <CommitNumber
        label="Y length"
        value={numberParam(object.params.yLength, 4)}
        onCommit={(value) => onEdit({ op: "set_length", objectId: object.id, axis: "y", value })}
      />
      <label className="check">
        <input
          type="checkbox"
          checked={object.params.showLabels === true}
          onChange={(event) => onEdit({ op: "set_show_labels", objectId: object.id, value: event.target.checked })}
        />
        Show labels
      </label>
    </>
  );
}

function PlotFields({
  object,
  fieldError,
  onEdit,
}: {
  object: SceneObject;
  fieldError: string | null;
  onEdit: (edit: Edit) => void;
}) {
  return (
    <>
      <CommitText
        label="Expression"
        value={stringParam(object.params.expression, "sin(x)")}
        testId="plot-expression"
        onCommit={(expression) => onEdit({ op: "set_expression", objectId: object.id, expression })}
      />
      {fieldError ? <p className="field-error">{fieldError}</p> : null}
      <p className="hint">Natural log is log. Variables: x.</p>
      <ColorField
        label="Color"
        value={stringParam(object.params.color, "#58C4DD")}
        onCommit={(color) => onEdit({ op: "set_object_color", objectId: object.id, color })}
      />
      <CommitNumber
        label="x min"
        value={numberParam(object.params.xMin, -6.28)}
        onCommit={(xMin) =>
          onEdit({
            op: "set_domain",
            objectId: object.id,
            xMin,
            xMax: numberParam(object.params.xMax, 6.28),
          })
        }
      />
      <CommitNumber
        label="x max"
        value={numberParam(object.params.xMax, 6.28)}
        onCommit={(xMax) =>
          onEdit({
            op: "set_domain",
            objectId: object.id,
            xMin: numberParam(object.params.xMin, -6.28),
            xMax,
          })
        }
      />
    </>
  );
}

function FormulaFields({ object, onEdit }: { object: SceneObject; onEdit: (edit: Edit) => void }) {
  const latex = stringParam(object.params.latex, "y = \\sin(x)");
  const [previewLatex, setPreviewLatex] = useState(latex);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- TODO(audit-ui): resync during render (F119)
    setPreviewLatex(latex);
  }, [object.id, latex]);
  return (
    <>
      <CommitText
        label="Formula"
        value={latex}
        testId="formula-latex"
        onDraft={setPreviewLatex}
        onCommit={(next) => onEdit({ op: "set_latex", objectId: object.id, latex: next })}
      />
      <FormulaPreview latex={previewLatex} />
      <AnchorField
        value={anchorParam(object.params.anchor)}
        onCommit={(anchor) => onEdit({ op: "set_anchor", objectId: object.id, anchor })}
      />
      <ColorField
        label="Color"
        value={stringParam(object.params.color, "#FFFFFF")}
        onCommit={(color) => onEdit({ op: "set_object_color", objectId: object.id, color })}
      />
      <ShiftFields
        value={pairParam(object.params.shift)}
        onCommit={(shift) => onEdit({ op: "set_object_shift", objectId: object.id, shift })}
      />
    </>
  );
}

function TextFields({ object, onEdit }: { object: SceneObject; onEdit: (edit: Edit) => void }) {
  return (
    <>
      <CommitText
        label="Text"
        value={stringParam(object.params.text, "Text")}
        testId="text-value"
        onCommit={(text) => onEdit({ op: "set_text", objectId: object.id, text })}
      />
      <AnchorField
        value={anchorParam(object.params.anchor)}
        onCommit={(anchor) => onEdit({ op: "set_anchor", objectId: object.id, anchor })}
      />
      <ColorField
        label="Color"
        value={stringParam(object.params.color, "#FFFFFF")}
        onCommit={(color) => onEdit({ op: "set_object_color", objectId: object.id, color })}
      />
      <ShiftFields
        value={pairParam(object.params.shift)}
        onCommit={(shift) => onEdit({ op: "set_object_shift", objectId: object.id, shift })}
      />
    </>
  );
}

function AnchorField({ value, onCommit }: { value: Anchor; onCommit: (anchor: Anchor) => void }) {
  return (
    <label>
      Anchor
      <select
        value={value}
        onChange={(event) => {
          const next = anchorParam(event.target.value);
          onCommit(next);
        }}
      >
        <option value="edge_up">Top edge</option>
        <option value="edge_down">Bottom edge</option>
        <option value="edge_left">Left edge</option>
        <option value="edge_right">Right edge</option>
        <option value="origin">Center</option>
      </select>
    </label>
  );
}

function ColorField({ label, value, onCommit }: { label: string; value: string; onCommit: (color: string) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- TODO(audit-ui): resync during render (F119)
    setDraft(value);
  }, [value]);
  return (
    <label>
      {label}
      <span className="color-row">
        <input
          type="color"
          value={toColorInput(draft)}
          onChange={(event) => {
            const next = event.target.value.toUpperCase();
            setDraft(next);
            onCommit(next);
          }}
        />
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={() => {
            if (draft !== value) {
              onCommit(draft);
            }
          }}
        />
      </span>
    </label>
  );
}

function RangeFields({
  label,
  value,
  onCommit,
}: {
  label: string;
  value: [number, number, number];
  onCommit: (value: [number, number, number]) => void;
}) {
  return (
    <fieldset>
      <legend>{label}</legend>
      <CommitNumber label="Min" value={value[0]} onCommit={(min) => onCommit([min, value[1], value[2]])} />
      <CommitNumber label="Max" value={value[1]} onCommit={(max) => onCommit([value[0], max, value[2]])} />
      <CommitNumber label="Step" value={value[2]} onCommit={(step) => onCommit([value[0], value[1], step])} />
    </fieldset>
  );
}

function ShiftFields({ value, onCommit }: { value: [number, number]; onCommit: (shift: [number, number]) => void }) {
  return (
    <fieldset>
      <legend>Shift</legend>
      <CommitNumber label="dx" value={value[0]} onCommit={(dx) => onCommit([dx, value[1]])} />
      <CommitNumber label="dy" value={value[1]} onCommit={(dy) => onCommit([value[0], dy])} />
    </fieldset>
  );
}

function CommitText({
  label,
  value,
  testId,
  onCommit,
  onDraft,
}: {
  label: string;
  value: string;
  testId?: string;
  onCommit: (value: string) => void;
  onDraft?: (value: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- TODO(audit-ui): resync during render (F119)
    setDraft(value);
  }, [value]);
  return (
    <label>
      {label}
      <input
        data-testid={testId}
        value={draft}
        onChange={(event) => {
          const next = event.target.value;
          setDraft(next);
          onDraft?.(next);
        }}
        onBlur={() => {
          if (draft !== value) {
            onCommit(draft);
          }
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.currentTarget.blur();
          }
        }}
      />
    </label>
  );
}

function CommitNumber({
  label,
  value,
  testId,
  onCommit,
}: {
  label: string;
  value: number;
  testId?: string;
  onCommit: (value: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- TODO(audit-ui): resync during render (F119)
    setDraft(String(value));
  }, [value]);
  return (
    <label>
      {label}
      <input
        data-testid={testId}
        inputMode="decimal"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => {
          const next = Number(draft);
          if (Number.isFinite(next) && next !== value) {
            onCommit(next);
          } else {
            setDraft(String(value));
          }
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.currentTarget.blur();
          }
        }}
      />
    </label>
  );
}

function stringParam(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

function numberParam(value: unknown, fallback: number): number {
  return typeof value === "number" ? value : fallback;
}

function pairParam(value: unknown): [number, number] {
  if (Array.isArray(value) && value.length === 2 && typeof value[0] === "number" && typeof value[1] === "number") {
    return [value[0], value[1]];
  }
  return [0, 0];
}

function tripleStrings(value: unknown): [string, string, string] {
  if (Array.isArray(value) && value.length === 3) {
    const items: unknown[] = value;
    const [x, y, z] = items;
    if (typeof x === "string" && typeof y === "string" && typeof z === "string") {
      return [x, y, z];
    }
  }
  return ["cos(t)", "sin(t)", "t / 3"];
}

function tripleParam(value: unknown): [number, number, number] {
  if (
    Array.isArray(value) &&
    value.length === 3 &&
    typeof value[0] === "number" &&
    typeof value[1] === "number" &&
    typeof value[2] === "number"
  ) {
    return [value[0], value[1], value[2]];
  }
  return [0, 1, 1];
}

function anchorParam(value: unknown): Anchor {
  switch (value) {
    case "origin":
    case "edge_up":
    case "edge_down":
    case "edge_left":
    case "edge_right":
      return value;
    default:
      return "edge_up";
  }
}

function toColorInput(value: string): string {
  return /^#[0-9A-Fa-f]{6}$/.test(value) ? value.toLowerCase() : "#ffffff";
}
