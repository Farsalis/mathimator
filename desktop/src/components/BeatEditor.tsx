import { BEAT_ACTIONS, EASING_NAMES, EASINGS, KINDS, type BeatKind, type Easing } from "../beatKinds";
import { checkTarget, kindGroups, roleCandidates, targetRows, targetsForKind } from "../beatRules";
import {
  deleteBeat,
  moveBeat,
  setBeatAction,
  setBeatDuration,
  setBeatEasing,
  setBeatParam,
  setBeatTargets,
} from "../edits";
import type { Beat, BeatAction, Edit, SceneKind, SceneObject } from "../types";
import type { FieldError } from "../useEditor";
import { CommitNumber, ParamField } from "./fields";

interface BeatEditorProps {
  beat: Beat;
  /** The beat's place in the timeline, `scene.beats[index]` in refusals. */
  index: number;
  /** How many beats the timeline holds. */
  count: number;
  sceneKind: SceneKind;
  objects: readonly SceneObject[];
  /** The object ids on screen before the beat. */
  onScreen: readonly string[];
  /** The template that owns the beat, which sets its kind and targets; null for a beat of its own. */
  templateId: string | null;
  selectedObjectId: string | null;
  fieldError: FieldError | null;
  onEdit: (edit: Edit) => Promise<void>;
}

/** The templates by the name Add gives them. */
const TEMPLATE_LABELS: Record<string, string> = {
  axes_function: "Axes and function",
  formula_transform: "Formula transform",
  three_d_axes: "3D axes",
};

/** The settings measured in degrees, by `action.param`. */
const DEGREES: ReadonlySet<string> = new Set(["rotate.angle", "move_camera.phi", "move_camera.theta"]);

/**
 * The beat editor (spec §6, Beat editor): the kind, the targets, the duration, the kind's settings, the easing, and the
 * buttons that move and delete the beat. Each control sends one edit, and a refusal shows under the control of its op.
 */
export function BeatEditor({
  beat,
  index,
  count,
  sceneKind,
  objects,
  onScreen,
  templateId,
  selectedObjectId,
  fieldError,
  onEdit,
}: BeatEditorProps) {
  const kind = KINDS[beat.action];
  const template = templateId === null ? null : (TEMPLATE_LABELS[templateId] ?? templateId);
  const message = (ops: readonly Edit["op"][], key?: string) => (
    <BeatMessage error={fieldError} beatId={beat.id} ops={ops} paramKey={key} />
  );

  function changeKind(action: BeatAction): void {
    const targets = targetsForKind(action, beat.targets, selectedObjectId, objects, onScreen);
    if (targets !== null) {
      void onEdit(setBeatAction(beat.id, action, targets));
    }
  }

  return (
    <section className="fields beat-editor" data-testid="beat-editor">
      <h2>Beat</h2>
      <p className="hint">
        {kind.label} · {beat.id} · scene.beats[{index}]
      </p>
      {template !== null ? (
        <p className="hint" data-testid="beat-template">
          Set by the {template} template.
        </p>
      ) : null}
      <label>
        Kind
        <select
          data-testid="beat-kind"
          value={beat.action}
          disabled={template !== null}
          onChange={(event) => changeKind(actionOf(event.target.value))}
        >
          {kindGroups(sceneKind).map(({ family, kinds }) => (
            <optgroup key={family.name} label={family.label}>
              {kinds.map((item) => (
                <option
                  key={item.action}
                  value={item.action}
                  disabled={
                    item.action !== beat.action &&
                    targetsForKind(item.action, beat.targets, selectedObjectId, objects, onScreen) === null
                  }
                >
                  {item.label}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </label>
      {message(["set_beat_action"])}
      {kind.arity === "some" ? (
        <TargetChecklist
          beat={beat}
          objects={objects}
          onScreen={onScreen}
          disabled={template !== null}
          onTargets={(targets) => void onEdit(setBeatTargets(beat.id, targets))}
        />
      ) : null}
      {kind.arity === "pair" ? (
        <RoleSelects
          beat={beat}
          kind={kind}
          objects={objects}
          onScreen={onScreen}
          disabled={template !== null}
          onTargets={(targets) => void onEdit(setBeatTargets(beat.id, targets))}
        />
      ) : null}
      {message(["set_beat_targets"])}
      <CommitNumber
        label="Duration"
        value={beat.duration}
        testId="beat-duration"
        onCommit={(duration) => onEdit(setBeatDuration(beat.id, duration))}
      />
      {message(["set_beat_duration"])}
      {kind.params.map((spec) => (
        <div key={`${beat.action}.${spec.name}`} className="beat-param">
          <ParamField
            spec={spec}
            value={beat.params?.[spec.name] ?? spec.default}
            label={DEGREES.has(`${beat.action}.${spec.name}`) ? `${spec.label} (°)` : spec.label}
            testId={`beat-param-${spec.name}`}
            onCommit={(value) => onEdit(setBeatParam(beat.id, spec.name, value))}
          />
          {message(["set_beat_param"], spec.name)}
        </div>
      ))}
      {beat.action === "move_along" ? <p className="hint">{pathHint(easingOf(beat, kind))}</p> : null}
      {kind.defaultEasing !== null ? (
        <>
          <EasingSelect
            beat={beat}
            kind={kind}
            defaultEasing={kind.defaultEasing}
            onEasing={(easing) => void onEdit(setBeatEasing(beat.id, easing))}
          />
          {message(["set_beat_easing"])}
        </>
      ) : null}
      <div className="beat-order">
        <button
          type="button"
          data-testid="move-beat-earlier"
          disabled={index === 0}
          onClick={() => void onEdit(moveBeat(beat.id, index - 1))}
        >
          Move earlier
        </button>
        <button
          type="button"
          data-testid="move-beat-later"
          disabled={index === count - 1}
          onClick={() => void onEdit(moveBeat(beat.id, index + 1))}
        >
          Move later
        </button>
        <button
          type="button"
          data-testid="delete-beat"
          disabled={template !== null}
          onClick={() => void onEdit(deleteBeat(beat.id))}
        >
          Delete beat
        </button>
      </div>
      {message(["move_beat", "delete_beat"])}
      {template !== null ? <p className="hint">To remove a template's beat, delete one of its objects.</p> : null}
    </section>
  );
}

/**
 * The targets of a kind of one or more objects: a box for each object the kind can take here and for each current
 * target, in the order the worker stores them. Checking axes checks what is drawn on them too, with a kind that takes
 * children along; in an exit, those boxes follow the axes. The last checked box cannot be unchecked.
 */
function TargetChecklist({
  beat,
  objects,
  onScreen,
  disabled,
  onTargets,
}: {
  beat: Beat;
  objects: readonly SceneObject[];
  onScreen: readonly string[];
  disabled: boolean;
  onTargets: (targets: string[]) => void;
}) {
  const rows = targetRows(beat.action, objects, onScreen, beat.targets);
  const only = beat.targets.length === 1;
  return (
    <fieldset className="beat-targets" data-testid="beat-targets" disabled={disabled}>
      <legend>Targets</legend>
      {rows.map(({ object, checked, fits, locked }) => (
        <label key={object.id} className="check">
          <input
            type="checkbox"
            checked={checked}
            disabled={locked || (checked && only)}
            onChange={(event) =>
              onTargets(
                event.target.checked
                  ? checkTarget(beat.action, objects, onScreen, beat.targets, object.id)
                  : beat.targets.filter((id) => id !== object.id),
              )
            }
          />
          {object.name}
          {locked ? <span className="hint">Leaves with its axes</span> : null}
          {fits ? null : <span className="target-warning">Not available at this point</span>}
        </label>
      ))}
    </fieldset>
  );
}

/** The two targets of a pair kind, one select per role, labelled by the role: From and To, or Object and Path. */
function RoleSelects({
  beat,
  kind,
  objects,
  onScreen,
  disabled,
  onTargets,
}: {
  beat: Beat;
  kind: BeatKind;
  objects: readonly SceneObject[];
  onScreen: readonly string[];
  disabled: boolean;
  onTargets: (targets: string[]) => void;
}) {
  return (
    <>
      {kind.roles.map((role, at) => {
        const current = beat.targets[at] ?? "";
        const fitting = roleCandidates(role, objects, onScreen);
        const listed = fitting.some((object) => object.id === current)
          ? fitting
          : [...objects.filter((object) => object.id === current), ...fitting];
        return (
          <label key={role.name}>
            {role.label}
            <select
              data-testid={`beat-role-${role.name}`}
              value={current}
              disabled={disabled}
              onChange={(event) => onTargets(beat.targets.map((id, item) => (item === at ? event.target.value : id)))}
            >
              {listed.map((object) => (
                <option key={object.id} value={object.id}>
                  {object.name}
                </option>
              ))}
            </select>
          </label>
        );
      })}
    </>
  );
}

/** The easing: the kind's default, named, then the kind's other easings. A wait has none. */
function EasingSelect({
  beat,
  kind,
  defaultEasing,
  onEasing,
}: {
  beat: Beat;
  kind: BeatKind;
  defaultEasing: Easing;
  onEasing: (easing: Easing | null) => void;
}) {
  return (
    <label>
      Easing
      <select
        data-testid="beat-easing"
        value={beat.easing === undefined || beat.easing === defaultEasing ? "" : beat.easing}
        onChange={(event) => onEasing(easingFrom(event.target.value))}
      >
        <option value="">Default ({EASINGS[defaultEasing].label})</option>
        {kind.easings
          .filter((easing) => easing !== defaultEasing)
          .map((easing) => (
            <option key={easing} value={easing}>
              {EASINGS[easing].label}
            </option>
          ))}
      </select>
    </label>
  );
}

/**
 * The refusal of an edit to the beat, shown under the control of its op: `ops` names the ops that control sends, and
 * `paramKey` the setting a `set_beat_param` refusal must be about (spec §6, Refusals in the UI).
 */
function BeatMessage({
  error,
  beatId,
  ops,
  paramKey,
}: {
  error: FieldError | null;
  beatId: string;
  ops: readonly Edit["op"][];
  paramKey?: string;
}) {
  if (error?.beatId !== beatId || !ops.includes(error.op) || (paramKey !== undefined && error.key !== paramKey)) {
    return null;
  }
  return (
    <p className="field-error" data-testid={`beat-error-${beatId}`}>
      {error.message}
    </p>
  );
}

/** What a move along a path does at its ends, which its easing decides. */
function pathHint(easing: Easing | null): string {
  const start = "The object jumps to the start of the path when the beat begins";
  return easing !== null && EASINGS[easing].endsAt === 0 ? `${start}, and ends back at the start.` : `${start}.`;
}

/** The easing a beat runs with: its own, or its kind's default. */
function easingOf(beat: Beat, kind: BeatKind): Easing | null {
  return beat.easing ?? kind.defaultEasing;
}

function actionOf(value: string): BeatAction {
  const action = BEAT_ACTIONS.find((item) => item === value);
  if (action === undefined) {
    throw new Error(`${value} is not a beat kind`);
  }
  return action;
}

/** The easing an option names, or null for the default. */
function easingFrom(value: string): Easing | null {
  return EASING_NAMES.find((item) => item === value) ?? null;
}
