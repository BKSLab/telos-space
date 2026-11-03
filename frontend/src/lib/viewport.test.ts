// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { observeViewport } from "./viewport";

let stop: (() => void) | undefined;
let viewport: EventTarget & { height: number; offsetTop: number; scale: number };
beforeEach(() => {
    viewport = Object.assign(new EventTarget(), { height: 800, offsetTop: 0, scale: 1 });
    vi.stubGlobal("visualViewport", viewport);
    vi.spyOn(window, "requestAnimationFrame").mockImplementation(callback => { callback(0); return 1; });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
});
afterEach(() => { stop?.(); document.body.replaceChildren(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("keeps the shell and keyboard marker within the visual viewport", () => {
    stop = observeViewport();
    const input = document.createElement("textarea");
    document.body.append(input);
    input.focus();
    viewport.height = 420;
    viewport.offsetTop = 30;
    viewport.dispatchEvent(new Event("resize"));
    expect(document.documentElement.style.getPropertyValue("--app-height")).toBe("420px");
    expect(document.documentElement.style.getPropertyValue("--app-top")).toBe("30px");
    expect(document.documentElement.dataset.keyboardOpen).toBe("true");
    input.blur();
    expect(document.documentElement.dataset.keyboardOpen).toBe("false");
});

it("does not resize or reposition the app during native pinch zoom", () => {
    stop = observeViewport();
    viewport.scale = 2;
    viewport.height = 400;
    viewport.offsetTop = 100;
    viewport.dispatchEvent(new Event("resize"));
    expect(document.documentElement.style.getPropertyValue("--app-height")).toBe("800px");
    expect(document.documentElement.style.getPropertyValue("--app-top")).toBe("0px");
    stop();
    stop = undefined;
    expect(document.documentElement.style.getPropertyValue("--app-height")).toBe("");
});
