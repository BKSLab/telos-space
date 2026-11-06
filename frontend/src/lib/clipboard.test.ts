// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { copyText } from "@/lib/clipboard";

const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");
const commandDescriptor = Object.getOwnPropertyDescriptor(document, "execCommand");
const command = vi.fn<(name: string) => boolean>();

function clipboard(value: { writeText: (text: string) => Promise<void> } | undefined) {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value });
}

beforeEach(() => {
    clipboard(undefined);
    command.mockReset().mockReturnValue(true);
    Object.defineProperty(document, "execCommand", { configurable: true, value: command });
});

afterEach(() => {
    document.body.replaceChildren();
    document.getSelection()?.removeAllRanges();
    if (clipboardDescriptor) Object.defineProperty(navigator, "clipboard", clipboardDescriptor);
    else Reflect.deleteProperty(navigator, "clipboard");
    if (commandDescriptor) Object.defineProperty(document, "execCommand", commandDescriptor);
    else Reflect.deleteProperty(document, "execCommand");
    vi.restoreAllMocks();
});

describe("copyText", () => {
    it("uses the modern API without changing DOM or selection", async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        clipboard({ writeText });
        const field = document.createElement("input");
        field.value = "selected text";
        document.body.append(field);
        field.focus();
        field.setSelectionRange(2, 5);

        await copyText("example");

        expect(writeText).toHaveBeenCalledWith("example");
        expect(command).not.toHaveBeenCalled();
        expect(document.activeElement).toBe(field);
        expect([field.selectionStart, field.selectionEnd]).toEqual([2, 5]);
        expect(document.querySelector("textarea")).toBeNull();
    });

    it("copies all Unicode and multiline text synchronously without the modern API", async () => {
        const text = "Пример 🔑\nвторая строка\n";
        let temporary: HTMLTextAreaElement | undefined;
        command.mockImplementation((name) => {
            expect(name).toBe("copy");
            temporary = document.activeElement as HTMLTextAreaElement;
            expect(temporary.tagName).toBe("TEXTAREA");
            expect(temporary.value).toBe(text);
            expect([temporary.selectionStart, temporary.selectionEnd]).toEqual([0, text.length]);
            return true;
        });

        const copying = copyText(text);
        expect(command).toHaveBeenCalledOnce();
        await copying;
        expect(temporary?.isConnected).toBe(false);
        expect(temporary?.value).toBe("");
        expect(document.querySelector("textarea")).toBeNull();
    });

    it("falls back when modern clipboard permission is denied", async () => {
        const writeText = vi.fn().mockRejectedValue(new DOMException("Denied", "NotAllowedError"));
        clipboard({ writeText });

        await copyText("example");

        expect(writeText).toHaveBeenCalledOnce();
        expect(command).toHaveBeenCalledWith("copy");
    });

    it("keeps the copy target inside the active dialog and restores focus", async () => {
        const outer = document.createElement("div");
        outer.setAttribute("role", "dialog");
        const dialog = document.createElement("div");
        dialog.setAttribute("role", "dialog");
        const button = document.createElement("button");
        outer.append(dialog);
        dialog.append(button);
        document.body.append(outer);
        button.focus();
        command.mockImplementation(() => {
            expect(document.activeElement?.parentElement).toBe(dialog);
            return true;
        });

        await copyText("example");

        expect(document.activeElement).toBe(button);
        expect(dialog.querySelector("textarea")).toBeNull();
    });

    it.each(["input", "textarea"] as const)("restores the original %s selection", async (tag) => {
        const control = document.createElement(tag);
        control.value = "original selection";
        document.body.append(control);
        control.focus();
        control.setSelectionRange(1, 9, "backward");

        await copyText("example");

        expect(document.activeElement).toBe(control);
        expect([control.selectionStart, control.selectionEnd, control.selectionDirection]).toEqual([1, 9, "backward"]);
        expect(document.querySelectorAll("textarea")).toHaveLength(tag === "textarea" ? 1 : 0);
    });

    it("restores a selected DOM range", async () => {
        const code = document.createElement("code");
        code.textContent = "original text";
        document.body.append(code);
        const range = document.createRange();
        range.selectNodeContents(code);
        document.getSelection()?.addRange(range);

        await copyText("example");

        expect(document.getSelection()?.toString()).toBe("original text");
    });

    it.each(["false", "throw", "missing"])("reports failure and cleans up when copy returns %s", async (result) => {
        const button = document.createElement("button");
        document.body.append(button);
        button.focus();
        if (result === "missing") Reflect.deleteProperty(document, "execCommand");
        else command.mockImplementation(() => {
            if (result === "throw") throw new Error("Do not expose the text being copied");
            return false;
        });

        await expect(copyText("private example")).rejects.toThrow(/^Clipboard copy unavailable$/);

        expect(document.querySelector("textarea")).toBeNull();
        expect(document.activeElement).toBe(button);
    });
});
