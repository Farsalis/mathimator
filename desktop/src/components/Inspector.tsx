import { Fragment } from "react";
import { SHAPES, type Shape } from "../beatKinds";
import {
  deleteObject,
  setAnchor,
  setAxesRange,
  setCamera,
  setCurveComponent,
  setDomain,
  setExpression,
  setLatex,
  setLength,
  setObjectColor,
  setObjectName,
  setObjectShift,
  setResolution,
  setShapeParam,
  setShowLabels,
  setText,
  setThreeDMode,
} from "../edits";
import type { Anchor, Beat, Camera, Edit, ParamValue, ProjectDocument, SceneObject, ThreeDMode } from "../types";
import { useCommitDraft } from "../useCommitDraft";
import type { FieldError } from "../useEditor";
import { BeatEditor } from "./BeatEditor";
import { ColorField, CommitNumber, CommitText, DraftText, ParamField } from "./fields";
import { FormulaPreview } from "./FormulaPreview";

interface InspectorProps {
  scene: ProjectDocument["scene"] | null;
  objects: SceneObject[];
  beats: Beat[];
  /** The object ids on screen before each beat, then after the last (`derived.onScreen`). */
  onScreen: string[][];
  selectedBeatId: string | null;
  selectedObjectId: string | null;
  fieldError: FieldError | null;
  /** The TeX check failed, so formulas and axis labels cannot render. */
  texBlocked: boolean;
  /** The worker is not ready, so nothing can be edited. */
  disabled: boolean;
  onSelectObject: (objectId: string) => void;
  /** Sends an edit. The promise settles once the edit was applied or refused. */
  onEdit: (edit: Edit) => Promise<void>;
}

/** What the object fields need besides their object. */
interface FieldContext {
  /** The scene's objects, among which a point on axes picks its axes. */
  objects: readonly SceneObject[];
  fieldError: FieldError | null;
  texBlocked: boolean;
  onEdit: (edit: Edit) => Promise<void>;
}

export function Inspector({
  scene,
  objects,
  beats,
  onScreen,
  selectedBeatId,
  selectedObjectId,
  fieldError,
  texBlocked,
  disabled,
  onSelectObject,
  onEdit,
}: InspectorProps) {
  const index = beats.findIndex((item) => item.id === selectedBeatId);
  const beat = beats[index] ?? null;
  const selectedObject = objects.find((item) => item.id === selectedObjectId) ?? null;
  // A formula transform shows the fields of both its formulas below the editor (spec §6, Beat editor).
  const formulas = beat?.action === "transform_formula" ? objects.filter((item) => beat.targets.includes(item.id)) : [];
  const context: FieldContext = { objects, fieldError, texBlocked, onEdit };

  return (
    <aside className="inspector" aria-label="Inspector" data-testid="inspector">
      <fieldset className="inspector-body" disabled={disabled}>
        <h2>Objects</h2>
        {objects.length === 0 ? <p className="hint">Nothing in the scene yet.</p> : null}
        {selectedObject ? (
          <button
            type="button"
            data-testid="delete-object"
            onClick={() => void onEdit(deleteObject(selectedObject.id))}
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
        {beat ? (
          <BeatEditor
            key={beat.id}
            beat={beat}
            index={index}
            count={beats.length}
            sceneKind={scene?.kind ?? "scene"}
            objects={objects}
            onScreen={onScreen[index] ?? []}
            templateId={scene?.templateInstances.find((item) => item.beatIds.includes(beat.id))?.templateId ?? null}
            selectedObjectId={selectedObject?.id ?? null}
            fieldError={fieldError}
            onEdit={onEdit}
          />
        ) : null}
        {formulas.map((item) => (
          <ObjectFields key={item.id} object={item} context={context} />
        ))}
        {selectedObject && !formulas.includes(selectedObject) ? (
          <ObjectFields key={selectedObject.id} object={selectedObject} context={context} />
        ) : null}
      </fieldset>
    </aside>
  );
}

function ObjectFields({ object, context }: { object: SceneObject; context: FieldContext }) {
  return (
    <section className="fields">
      <h2>{object.name}</h2>
      <CommitText
        label="Name"
        value={object.name}
        testId={`object-name-${object.id}`}
        onCommit={(name) => context.onEdit(setObjectName(object.id, name))}
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
    case "dot":
    case "line":
    case "arrow":
    case "circle":
    case "rectangle":
    case "axes_point":
      return <ShapeFields object={object} shape={SHAPES[object.type]} context={context} />;
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
        Cairo limits: software rendering, flat-shaded faces, a camera that turns only in Move camera beats, with no
        orbit drag, and a resolution cap of 24×24.
      </p>
      <label>
        Mode
        <select
          data-testid="three-d-mode"
          value={mode}
          onChange={(event) => void onEdit(setThreeDMode(modeFromSelect(event.target.value)))}
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
            onCommit={(u) => onEdit(setResolution(surface.id, [u, resolution[1]]))}
          />
          <CommitNumber
            label="Resolution v"
            value={resolution[1]}
            testId="resolution-v"
            onCommit={(v) => onEdit(setResolution(surface.id, [resolution[0], v]))}
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
            onCommit={(expression) => onEdit(setCurveComponent(curve.id, 0, expression))}
          />
          <CommitText
            label="y(t)"
            value={components[1]}
            testId="curve-y"
            onCommit={(expression) => onEdit(setCurveComponent(curve.id, 1, expression))}
          />
          <CommitText
            label="z(t)"
            value={components[2]}
            testId="curve-z"
            onCommit={(expression) => onEdit(setCurveComponent(curve.id, 2, expression))}
          />
          <FieldMessage error={fieldError} objectId={curve.id} field="expression" />
          <p className="hint">Natural log is log. Variable: t.</p>
        </>
      ) : null}
      <CommitNumber
        label="Phi"
        value={camera.phi}
        testId="camera-phi"
        onCommit={(value) => onEdit(setCamera("phi", value))}
      />
      <CommitNumber
        label="Theta"
        value={camera.theta}
        testId="camera-theta"
        onCommit={(value) => onEdit(setCamera("theta", value))}
      />
      <CommitNumber
        label="Zoom"
        value={camera.zoom}
        testId="camera-zoom"
        onCommit={(value) => onEdit(setCamera("zoom", value))}
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
        onCommit={(color) => onEdit(setObjectColor(object.id, color))}
      />
      <RangeFields label="X range" value={xRange} onCommit={(value) => onEdit(setAxesRange(object.id, "x", value))} />
      <FieldMessage error={fieldError} objectId={object.id} field="value" op="set_axes_range" axis="x" />
      <RangeFields label="Y range" value={yRange} onCommit={(value) => onEdit(setAxesRange(object.id, "y", value))} />
      <FieldMessage error={fieldError} objectId={object.id} field="value" op="set_axes_range" axis="y" />
      <CommitNumber
        label="X length"
        value={numberParam(object.params.xLength, 10)}
        onCommit={(value) => onEdit(setLength(object.id, "x", value))}
      />
      <FieldMessage error={fieldError} objectId={object.id} field="value" op="set_length" axis="x" />
      <CommitNumber
        label="Y length"
        value={numberParam(object.params.yLength, 4)}
        onCommit={(value) => onEdit(setLength(object.id, "y", value))}
      />
      <FieldMessage error={fieldError} objectId={object.id} field="value" op="set_length" axis="y" />
      <label className="check">
        <input
          type="checkbox"
          checked={object.params.showLabels === true}
          onChange={(event) => void onEdit(setShowLabels(object.id, event.target.checked))}
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
        onCommit={(color) => onEdit(setObjectColor(object.id, color))}
      />
      <CommitNumber
        label="x min"
        value={numberParam(object.params.xMin, -6.28)}
        onCommit={(xMin) => onEdit(setDomain(object.id, xMin, numberParam(object.params.xMax, 6.28)))}
      />
      <FieldMessage error={context.fieldError} objectId={object.id} field="xMin" />
      <CommitNumber
        label="x max"
        value={numberParam(object.params.xMax, 6.28)}
        onCommit={(xMax) => onEdit(setDomain(object.id, numberParam(object.params.xMin, -6.28), xMax))}
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
        onCommit={(next) => onEdit(setLatex(object.id, next))}
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
        onCommit={(text) => onEdit(setText(object.id, text))}
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
        onCommit={(anchor) => onEdit(setAnchor(object.id, anchor))}
      />
      <ColorField
        label="Color"
        value={stringParam(object.params.color, "#FFFFFF")}
        onCommit={(color) => onEdit(setObjectColor(object.id, color))}
      />
      <ShiftFields
        value={pairParam(object.params.shift)}
        onCommit={(shift) => onEdit(setObjectShift(object.id, shift))}
      />
    </>
  );
}

/**
 * A shape's settings, one control per setting of its type in the catalog with the colour last (spec §6, Shapes). Each
 * sends `set_shape_param`, and a refused value shows under its own control. A point on axes picks its axes.
 */
function ShapeFields({ object, shape, context }: { object: SceneObject; shape: Shape; context: FieldContext }) {
  const { fieldError, onEdit } = context;
  const specs = [
    ...shape.params.filter((spec) => spec.type !== "color"),
    ...shape.params.filter((spec) => spec.type === "color"),
  ];
  return (
    <>
      {specs.map((spec) => {
        const commit = (value: ParamValue) => onEdit(setShapeParam(object.id, spec.name, value));
        return (
          <Fragment key={spec.name}>
            {spec.type === "id" ? (
              <AxesField
                label={spec.label}
                value={stringParam(object.params[spec.name], "")}
                objects={context.objects}
                onCommit={commit}
              />
            ) : (
              <ParamField
                spec={spec}
                value={object.params[spec.name]}
                testId={`shape-param-${spec.name}`}
                onCommit={commit}
              />
            )}
            <FieldMessage
              error={fieldError}
              objectId={object.id}
              field="value"
              op="set_shape_param"
              paramKey={spec.name}
            />
          </Fragment>
        );
      })}
    </>
  );
}

/** The 2D axes a point sits on, by name. Its x and y are coordinates on those axes. */
function AxesField({
  label,
  value,
  objects,
  onCommit,
}: {
  label: string;
  value: string;
  objects: readonly SceneObject[];
  onCommit: (axesId: string) => Promise<void>;
}) {
  return (
    <>
      <label>
        {label}
        <select data-testid="shape-param-axesId" value={value} onChange={(event) => void onCommit(event.target.value)}>
          {objects
            .filter((item) => item.type === "axes")
            .map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
        </select>
      </label>
      <p className="hint">x and y are coordinates on these axes.</p>
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
        onCommit={(expression) => context.onEdit(setExpression(object.id, expression))}
      />
      <FieldMessage error={context.fieldError} objectId={object.id} field="expression" />
      <p className="hint">{`Natural log is log. Variables: ${variables}.`}</p>
    </>
  );
}

/**
 * The refusal of an edit to `field` on `objectId`, shown under that field only. `op`, `axis` and
 * `paramKey` pick one input out of those whose edits name the same field, such as the axes ranges and lengths, or the
 * settings of a shape.
 */
function FieldMessage({
  error,
  objectId,
  field,
  op,
  axis,
  paramKey,
}: {
  error: FieldError | null;
  objectId: string;
  field: string;
  op?: Edit["op"];
  axis?: string;
  paramKey?: string;
}) {
  if (
    error?.objectId !== objectId ||
    error.field !== field ||
    (op !== undefined && error.op !== op) ||
    (axis !== undefined && error.axis !== axis) ||
    (paramKey !== undefined && error.key !== paramKey)
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
