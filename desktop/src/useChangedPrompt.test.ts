// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useChangedPrompt } from "./useChangedPrompt";

const CHANGED = "project.json was changed outside Mathimator.";

function retry(): Promise<void> {
  return Promise.resolve();
}

describe("useChangedPrompt", () => {
  it("opens one prompt, joins later refusals to it and hands back their retries in order on close", () => {
    const { result } = renderHook(() => useChangedPrompt());
    const later = (): Promise<void> => Promise.resolve();
    act(() => result.current.join(CHANGED, retry));
    act(() => result.current.join("A later message.", later));
    expect(result.current.message).toBe(CHANGED);
    let retries: unknown[] = [];
    act(() => {
      retries = result.current.close();
    });
    expect(retries).toEqual([retry, later]);
    expect(result.current.message).toBeNull();
  });

  it("opens a new prompt for a refusal after the close, without the earlier retries", () => {
    const { result } = renderHook(() => useChangedPrompt());
    act(() => result.current.join(CHANGED, retry));
    act(() => void result.current.close());
    const next = (): Promise<void> => Promise.resolve();
    act(() => result.current.join("Changed again.", next));
    expect(result.current.message).toBe("Changed again.");
    let retries: unknown[] = [];
    act(() => {
      retries = result.current.close();
    });
    expect(retries).toEqual([next]);
  });
});
