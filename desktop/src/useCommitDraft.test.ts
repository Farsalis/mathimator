// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useCommitDraft } from "./useCommitDraft";

/** A pending edit and the means to settle it. */
function pendingEdit(): { promise: Promise<void>; settle: () => Promise<void> } {
  let settle: () => void = () => undefined;
  const promise = new Promise<void>((resolve) => {
    settle = resolve;
  });
  return {
    promise,
    settle: async () => {
      settle();
      await promise;
    },
  };
}

describe("useCommitDraft (F119)", () => {
  it("shows the draft, and follows a new value from the document", () => {
    const { result, rerender } = renderHook(({ value }) => useCommitDraft(value), { initialProps: { value: "Sine" } });
    act(() => result.current.change("Cosine"));
    expect(result.current.text).toBe("Cosine");
    rerender({ value: "Tangent" });
    expect(result.current.text).toBe("Tangent");
    rerender({ value: "Sine" });
    expect(result.current.text).toBe("Sine");
  });

  it("keeps a committed draft until the edit settles, then shows the value a refusal left (F045)", async () => {
    const { result } = renderHook(() => useCommitDraft("2"));
    const edit = pendingEdit();
    act(() => result.current.change("-5"));
    act(() => result.current.commit(edit.promise));
    expect(result.current.text).toBe("-5");
    await act(edit.settle);
    expect(result.current.text).toBe("2");
  });

  it("keeps what the user typed after the commit when the edit settles", async () => {
    const { result } = renderHook(() => useCommitDraft("2"));
    const edit = pendingEdit();
    act(() => result.current.change("3"));
    act(() => result.current.commit(edit.promise));
    act(() => result.current.change("4"));
    await act(edit.settle);
    expect(result.current.text).toBe("4");
  });

  it("reverts to the value", () => {
    const { result } = renderHook(() => useCommitDraft("2"));
    act(() => result.current.change(""));
    act(() => result.current.revert());
    expect(result.current.text).toBe("2");
  });
});
