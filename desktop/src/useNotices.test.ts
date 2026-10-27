// @vitest-environment jsdom
import { invoke } from "@tauri-apps/api/core";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useNotices } from "./useNotices";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(() => Promise.resolve(null)) }));

afterEach(() => {
  cleanup();
  vi.mocked(invoke).mockClear();
});

const RESTART = { id: 4, message: "The worker restarted." };
const REOPEN = { id: 5, message: "Could not reopen C:/first after the worker restarted." };

describe("useNotices", () => {
  it("shows each notice id once, however often it is added", () => {
    const { result } = renderHook(() => useNotices());
    act(() => result.current.add([RESTART]));
    act(() => result.current.add([RESTART, REOPEN]));
    expect(result.current.notices).toEqual([RESTART, REOPEN]);
  });

  it("hides a dismissed notice for good and tells the shell", () => {
    const { result } = renderHook(() => useNotices());
    act(() => result.current.add([RESTART, REOPEN]));
    act(() => result.current.dismiss(RESTART.id));
    act(() => result.current.add([RESTART]));
    expect(result.current.notices).toEqual([REOPEN]);
    expect(vi.mocked(invoke)).toHaveBeenCalledExactlyOnceWith("dismiss_notice", { id: RESTART.id });
  });
});
