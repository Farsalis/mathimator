/** A reply that does not have the shape the UI expects. `field` is the path of the value that failed (D50). */
export class ParseError extends Error {
  name = "ParseError";

  constructor(
    readonly field: string,
    reason: string,
  ) {
    super(`Unexpected reply from the worker: ${field} ${reason}.`);
  }
}

/** Reads one value, naming `path` in the ParseError when the value has the wrong shape. */
export type Read<T> = (value: unknown, path: string) => T;

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function asRecord(value: unknown, path: string): Record<string, unknown> {
  if (!isRecord(value)) {
    throw new ParseError(path, "must be an object");
  }
  return value;
}

export function asString(value: unknown, path: string): string {
  if (typeof value !== "string") {
    throw new ParseError(path, "must be a string");
  }
  return value;
}

export function asNumber(value: unknown, path: string): number {
  if (typeof value !== "number") {
    throw new ParseError(path, "must be a number");
  }
  return value;
}

export function asInteger(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isInteger(value)) {
    throw new ParseError(path, "must be an integer");
  }
  return value;
}

export function asBoolean(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") {
    throw new ParseError(path, "must be true or false");
  }
  return value;
}

export function listOf<T>(read: Read<T>): Read<T[]> {
  return (value, path) => {
    if (!Array.isArray(value)) {
      throw new ParseError(path, "must be a list");
    }
    return value.map((item: unknown, index) => read(item, `${path}[${index}]`));
  };
}

/** Reads a list of exactly two items, such as an `[x, y]` vector. */
export function pairOf<T>(read: Read<T>): Read<[T, T]> {
  return (value, path) => {
    if (!Array.isArray(value) || value.length !== 2) {
      throw new ParseError(path, "must be a list of two items");
    }
    return [read(value[0], `${path}[0]`), read(value[1], `${path}[1]`)];
  };
}

export function oneOf<T extends string>(options: readonly T[]): Read<T> {
  return (value, path) => {
    const match = options.find((option) => option === value);
    if (match === undefined) {
      throw new ParseError(path, `must be one of ${options.join(", ")}`);
    }
    return match;
  };
}

export function nullable<T>(read: Read<T>): Read<T | null> {
  return (value, path) => (value === null ? null : read(value, path));
}

export function optional<T>(read: Read<T>): Read<T | undefined> {
  return (value, path) => (value === undefined ? undefined : read(value, path));
}

/** Checks that `value` is an object and returns a reader for its fields: `read(key, as)` names `path.key`. */
export function fieldsOf(value: unknown, path: string): <T>(key: string, read: Read<T>) => T {
  const record = asRecord(value, path);
  return (key, read) => read(record[key], `${path}.${key}`);
}
