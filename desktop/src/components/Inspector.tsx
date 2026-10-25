import { useEffect, useEffectEvent, useRef } from "react";
import { parseDecimal } from "../format";
import type { Anchor, Beat, BeatAction, Camera, Edit, ProjectDocument, SceneObject, ThreeDMode } from "../types";
import { useCommitDraft, type CommitDraft } from "../useCommitDraft";
import type { FieldError } from "../useEditor";
import { FormulaPreview } from "./FormulaPreview";

interface InspectorProps {
  scene: ProjectDocument["scene"] | null;
  objects: SceneObject[];
  beats: Beat[];
  selectedBeatId: string | null;
  selectedObjectId: string | null;
  fieldError: FieldError | null;
  /** The TeX check failed, so formulas and axis labels cannot render (C9.7, C14 Q4). */
  texBlocked: boolean;
  /** The worker is not ready, so nothing can be edited (C9.5). */
  disabled: boolean;
  onSelectObject: (objectId: string) => void;
  /** Sends an edit. The promise settles once the edit was applied or refused. */
  onEdit: (edit: Edit) => Promise<void>;
}

/** What the object fields need besides their object. */
interface FieldContext {
  fieldError: FieldError | null;
  texBlocked: boolean;
  onEdit: (edit: Edit) => Promise<void>;
}

export function Inspector({
  scene,
  objects,
  beats,
  selectedBeatId,
  selectedObjectId,
  fieldError,
  texBlocked,
  disabled,
  onSelectObject,
  onEdit,
}: InspectorProps) {
  const beat = beats.find((item) => item.id === selectedBeatId) ?? null;
  const selectedFromBeat =
    beat && beat.targets.length === 1 ? objects.find((item) => item.id === beat.targets[0]) : null;
  const selectedObject = objects.find((item) => item.id === selectedObjectId) ?? selectedFromBeat ?? null;
  const targets = beat ? objects.filter((item) => beat.targets.includes(item.id)) : [];
  const context: FieldContext = { fieldError, texBlocked, onEdit };

  return (
    <aside className="inspector" aria-label="Inspector" data-testid="inspector">
      <fieldset className="inspector-body" disabled={disabled}>
        <h2>Objects</h2>
        {objects.length === 0 ? <p className="hint">Nothing in the scene yet.</p> : null}
        {selectedObject ? (
          <button
            type="button"
            data-testid="delete-object"
            onClick={() => void onEdit({ op: "delete_object", objectId: selectedObject.id })}
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
          <ThreeDPanel scene={scene} camera={scene.camera} objects={objects} context={context} />
        ) : null}
        {beat ? <BeatFields beat={beat} onEdit={onEdit} /> : null}
        {beat && beat.action === "transform_formula"
          ? targets.map((item) => <ObjectFields key={item.id} object={item} context={context} />)
          : null}
        {selectedObject && beat?.action !== "transform_formula" ? (
          <ObjectFields key={selectedObject.id} object={selectedObject} context={context} />
        ) : null}
      </fieldset>
    </aside>
  );
}

function BeatFields({ beat, onEdit }: { beat: Beat; onEdit: (edit: Edit) => Promise<void> }) {
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

function ActionFields({ beat, onEdit }: { beat: Beat; onEdit: (edit: Edit) => Promise<void> }) {
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

function ObjectFields({ object, context }: { object: SceneObject; context: FieldContext }) {
  return (
    <section className="fields">
      <h2>{object.name}</h2>
      <CommitText
        label="Name"
        value={object.name}
        testId={`object-name-${object.id}`}
        onCommit={(name) => context.onEdit({ op: "set_object_name", objectId: object.id, name })}
      />
      <FieldMessage error={context.fieldError} objectId={object.id} field="name" />
      <TypedFields object={object} context={context} />
    </section>
  );
}

function TypedFields({ object, context }: { object: SceneObject; context: FieldContext }) {
  switch (object.type) {
    case "axes":
      return <AxesFields object={object} context={context} />;
    case "function_plot":
      return <PlotFields object={object} context={context} />;
    case "math_formula":
      return <FormulaFields object={object} context={context} />;
    case "text":
      return <TextFields object={object} context={context} />;
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
  context,
}: {
  scene: ProjectDocument["scene"];
  camera: Camera;
  objects: SceneObject[];
  context: FieldContext;
}) {
  const { fieldError, onEdit } = context;
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
          onChange={(event) => void onEdit({ op: "set_three_d_mode", mode: modeFromSelect(event.target.value) })}
        >
          <option value="surface">Surface</option>
          <option value="curve">Curve</option>
        </select>
      </label>
      {mode === "surface" && surface ? (
        <>
          <ExpressionField
            object={surface}
            fallback="sin(x)*cos(y)"
            variables="x and y"
            testId="surface-expression"
            context={context}
          />
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
          <FieldMessage error={fieldError} objectId={surface.id} field="value" />
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
          <FieldMessage error={fieldError} objectId={curve.id} field="expression" />
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

function AxesFields({ object, context }: { object: SceneObject; context: FieldContext }) {
  const { fieldError, onEdit } = context;
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
      <FieldMessage error={fieldError} objectId={object.id} field="value" op="set_axes_range" axis="x" />
      <RangeFields
        label="Y range"
        value={yRange}
        onCommit={(value) => onEdit({ op: "set_axes_range", objectId: object.id, axis: "y", value })}
      />
      <FieldMessage error={fieldError} objectId={object.id} field="value" op="set_axes_range" axis="y" />
      <CommitNumber
        label="X length"
        value={numberParam(object.params.xLength, 10)}
        onCommit={(value) => onEdit({ op: "set_length", objectId: object.id, axis: "x", value })}
      />
      <FieldMessage error={fieldError} objectId={object.id} field="value" op="set_length" axis="x" />
      <CommitNumber
        label="Y length"
        value={numberParam(object.params.yLength, 4)}
        onCommit={(value) => onEdit({ op: "set_length", objectId: object.id, axis: "y", value })}
      />
      <FieldMessage error={fieldError} objectId={object.id} field="value" op="set_length" axis="y" />
      <label className="check">
        <input
          type="checkbox"
          checked={object.params.showLabels === true}
          onChange={(event) => void onEdit({ op: "set_show_labels", objectId: object.id, value: event.target.checked })}
        />
        Show labels
      </label>
      {context.texBlocked && object.params.showLabels === true ? (
        <p className="field-error" data-testid="tex-blocked">
          LaTeX is not working on this PC, so the axis labels cannot render. Reinstall Mathimator.
        </p>
      ) : null}
    </>
  );
}

function PlotFields({ object, context }: { object: SceneObject; context: FieldContext }) {
  const { onEdit } = context;
  return (
    <>
      <ExpressionField object={object} fallback="sin(x)" variables="x" testId="plot-expression" context={context} />
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
      <FieldMessage error={context.fieldError} objectId={object.id} field="xMin" />
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
      <FieldMessage error={context.fieldError} objectId={object.id} field="xMax" />
    </>
  );
}

function FormulaFields({ object, context }: { object: SceneObject; context: FieldContext }) {
  const { onEdit } = context;
  const latex = stringParam(object.params.latex, "y = \\sin(x)");
  const draft = useCommitDraft(latex);
  return (
    <>
      <DraftText
        label="Formula"
        value={latex}
        testId="formula-latex"
        draft={draft}
        onCommit={(next) => onEdit({ op: "set_latex", objectId: object.id, latex: next })}
      />
      <FieldMessage error={context.fieldError} objectId={object.id} field="latex" />
      {context.texBlocked ? (
        <p className="field-error" data-testid="tex-blocked">
          LaTeX is not working on this PC, so this formula cannot render. Reinstall Mathimator.
        </p>
      ) : null}
      <FormulaPreview slot={object.id} latex={draft.text} />
      <PlacementFields object={object} onEdit={onEdit} />
    </>
  );
}

function TextFields({ object, context }: { object: SceneObject; context: FieldContext }) {
  const { onEdit } = context;
  return (
    <>
      <CommitText
        label="Text"
        value={stringParam(object.params.text, "Text")}
        testId="text-value"
        onCommit={(text) => onEdit({ op: "set_text", objectId: object.id, text })}
      />
      <FieldMessage error={context.fieldError} objectId={object.id} field="text" />
      <PlacementFields object={object} onEdit={onEdit} />
    </>
  );
}

/** Where a formula or a text sits and its colour: the anchor, the colour and the shift from the anchor. */
function PlacementFields({ object, onEdit }: { object: SceneObject; onEdit: (edit: Edit) => Promise<void> }) {
  return (
    <>
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

/** The expression of a plot or a surface, its refusal, and the variables it may use. */
function ExpressionField({
  object,
  fallback,
  variables,
  testId,
  context,
}: {
  object: SceneObject;
  fallback: string;
  variables: string;
  testId: string;
  context: FieldContext;
}) {
  return (
    <>
      <CommitText
        label="Expression"
        value={stringParam(object.params.expression, fallback)}
        testId={testId}
        onCommit={(expression) => context.onEdit({ op: "set_expression", objectId: object.id, expression })}
      />
      <FieldMessage error={context.fieldError} objectId={object.id} field="expression" />
      <p className="hint">{`Natural log is log. Variables: ${variables}.`}</p>
    </>
  );
}

/**
 * The refusal of an edit to `field` on `objectId`, shown under that field only (C9.3, F112). `op` and `axis` pick one
 * input out of those whose edits name the same field, such as the axes ranges and lengths.
 */
function FieldMessage({
  error,
  objectId,
  field,
  op,
  axis,
}: {
  error: FieldError | null;
  objectId: string;
  field: string;
  op?: Edit["op"];
  axis?: string;
}) {
  if (
    error?.objectId !== objectId ||
    error.field !== field ||
    (op !== undefined && error.op !== op) ||
    (axis !== undefined && error.axis !== axis)
  ) {
    return null;
  }
  return (
    <p className="field-error" data-testid={`field-error-${objectId}-${field}`}>
      {error.message}
    </p>
  );
}

function AnchorField({ value, onCommit }: { value: Anchor; onCommit: (anchor: Anchor) => Promise<void> }) {
  return (
    <label>
      Anchor
      <select value={value} onChange={(event) => void onCommit(anchorParam(event.target.value))}>
        <option value="edge_up">Top edge</option>
        <option value="edge_down">Bottom edge</option>
        <option value="edge_left">Left edge</option>
        <option value="edge_right">Right edge</option>
        <option value="origin">Center</option>
      </select>
    </label>
  );
}

/**
 * A colour picker beside a hex field. While the picker is open it moves only the draft; it commits once, on the
 * native `change` event when it closes, so one use of it is one edit and one undo step (D08, F009). React's onChange
 * on a colour input follows every `input` event, hence the listener. The hex field commits on blur. Both compare
 * case-insensitively.
 */
function ColorField({
  label,
  value,
  onCommit,
}: {
  label: string;
  value: string;
  onCommit: (color: string) => Promise<void>;
}) {
  const draft = useCommitDraft(value);
  const picker = useRef<HTMLInputElement>(null);

  function commitColor(color: string): void {
    if (color.toUpperCase() === value.toUpperCase()) {
      draft.revert();
    } else {
      draft.commit(onCommit(color));
    }
  }

  const onPickerClosed = useEffectEvent((color: string) => commitColor(color.toUpperCase()));

  useEffect(() => {
    const element = picker.current;
    if (!element) {
      return;
    }
    const listener = (): void => onPickerClosed(element.value);
    element.addEventListener("change", listener);
    return () => element.removeEventListener("change", listener);
  }, []);

  return (
    <label>
      {label}
      <span className="color-row">
        <input
          ref={picker}
          type="color"
          value={toColorInput(draft.text)}
          onChange={(event) => draft.change(event.target.value.toUpperCase())}
        />
        <input
          value={draft.text}
          onChange={(event) => draft.change(event.target.value)}
          onBlur={() => commitColor(draft.text)}
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
  onCommit: (value: [number, number, number]) => Promise<void>;
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

function ShiftFields({
  value,
  onCommit,
}: {
  value: [number, number];
  onCommit: (shift: [number, number]) => Promise<void>;
}) {
  return (
    <fieldset>
      <legend>Shift</legend>
      <CommitNumber label="dx" value={value[0]} onCommit={(dx) => onCommit([dx, value[1]])} />
      <CommitNumber label="dy" value={value[1]} onCommit={(dy) => onCommit([value[0], dy])} />
    </fieldset>
  );
}

interface TextFieldProps {
  label: string;
  value: string;
  testId?: string;
  onCommit: (value: string) => Promise<void>;
}

/** A text field that commits on blur or Enter. */
function CommitText(props: TextFieldProps) {
  const draft = useCommitDraft(props.value);
  return <DraftText {...props} draft={draft} />;
}

/** CommitText over a draft its parent holds, for a parent that shows the draft too, as the formula preview does. */
function DraftText({ label, value, testId, draft, onCommit }: TextFieldProps & { draft: CommitDraft }) {
  return (
    <label>
      {label}
      <input
        data-testid={testId}
        value={draft.text}
        onChange={(event) => draft.change(event.target.value)}
        onBlur={() => {
          if (draft.text !== value) {
            draft.commit(onCommit(draft.text));
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

/** A number field that commits on blur or Enter. A cleared field, or text that is not a decimal, shows the value again. */
function CommitNumber({
  label,
  value,
  testId,
  onCommit,
}: {
  label: string;
  value: number;
  testId?: string;
  onCommit: (value: number) => Promise<void>;
}) {
  const draft = useCommitDraft(String(value));
  return (
    <label>
      {label}
      <input
        data-testid={testId}
        inputMode="decimal"
        value={draft.text}
        onChange={(event) => draft.change(event.target.value)}
        onBlur={() => {
          const next = parseDecimal(draft.text);
          if (next === null || next === value) {
            draft.revert();
          } else {
            draft.commit(onCommit(next));
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
