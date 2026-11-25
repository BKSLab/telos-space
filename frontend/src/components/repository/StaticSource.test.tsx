// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { useState } from "react";
import { StaticSource } from "./StaticSource";
import type { SourceRange } from "./SourceViewer";

afterEach(cleanup);

it.each(["", "\n", "\r\n", "\t\n\n", "😀Привет\r\n\r\nend\r", "<script>&amp;</script>\n"]) ("preserves the exact text %j without gutter numbers in code", text => {
    const { container } = render(<StaticSource text={text} path="test.txt" firstLine={55} line={55} end={56} />);
    const code = container.querySelector("code")!;
    expect(code.textContent).toBe(text);
    expect(code.closest('[aria-hidden="true"]')).toBeNull();
    expect(container.querySelectorAll("pre")).toHaveLength(1);
    expect(code.querySelector("script")).toBeNull();
    expect(screen.getByRole("region", { name: "Код test.txt" }).tabIndex).toBe(0);
});

it("provides one gutter tab stop and keyboard range selection", async () => {
    const user = userEvent.setup();
    const onRange = vi.fn();
    render(<StaticSource text={"one\ntwo\nthree\n"} path="test.py" firstLine={55} line={55} end={55} onRange={onRange} />);
    const first = screen.getByRole("button", { name: "Выбрать строку 55" });
    const second = screen.getByRole("button", { name: "Выбрать строку 56" });
    const last = screen.getByRole("button", { name: "Выбрать строку 57" });
    expect(screen.queryByRole("button", { name: "Выбрать строку 58" })).toBeNull();
    expect(first.tabIndex).toBe(0);
    expect(second.tabIndex).toBe(-1);
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("region", { name: "Код test.py" }));
    await user.tab();
    expect(document.activeElement).toBe(first);
    await user.keyboard("{ArrowDown}{Enter}");
    expect(document.activeElement).toBe(second);
    expect(onRange).toHaveBeenLastCalledWith({ line: 56, end: 56 });
    fireEvent.keyDown(second, { key: "End", shiftKey: true });
    expect(document.activeElement).toBe(last);
    expect(onRange).toHaveBeenLastCalledWith({ line: 56, end: 57 });
    fireEvent.keyDown(last, { key: "Home" });
    expect(document.activeElement).toBe(first);
    expect(screen.getAllByRole("button").filter(button => button.tabIndex === 0)).toHaveLength(1);
});

it("keeps a fixed gesture anchor while controlled ranges grow and reverse direction", async () => {
    const user = userEvent.setup();
    function Controlled() {
        const [range, setRange] = useState<SourceRange>({ line: 3, end: 3 });
        return <><StaticSource text={"one\ntwo\nthree\nfour\nfive"} path="test.py" firstLine={1} {...range} onRange={setRange} /><output>{range.line}–{range.end}</output><button onClick={() => setRange({ line: 4, end: 4 })}>External navigation</button></>;
    }
    render(<Controlled />);
    fireEvent.click(screen.getByRole("button", { name: "Выбрать строку 3" }));
    screen.getByRole("button", { name: "Выбрать строку 3" }).focus();
    await user.keyboard("{Shift>}{ArrowUp}{ArrowUp}{/Shift}");
    expect(screen.getByRole("status").textContent).toBe("1–3");
    await user.keyboard("{Shift>}{ArrowDown}{ArrowDown}{ArrowDown}{/Shift}");
    expect(screen.getByRole("status").textContent).toBe("3–4");
    fireEvent.click(screen.getByRole("button", { name: "Выбрать строку 1" }), { shiftKey: true });
    expect(screen.getByRole("status").textContent).toBe("1–3");
    fireEvent.click(screen.getByRole("button", { name: "Выбрать строку 5" }), { shiftKey: true });
    expect(screen.getByRole("status").textContent).toBe("3–5");
    fireEvent.click(screen.getByRole("button", { name: "Выбрать строку 2" }));
    fireEvent.click(screen.getByRole("button", { name: "Выбрать строку 5" }), { shiftKey: true });
    expect(screen.getByRole("status").textContent).toBe("2–5");
    fireEvent.click(screen.getByRole("button", { name: "External navigation" }));
    fireEvent.click(screen.getByRole("button", { name: "Выбрать строку 1" }), { shiftKey: true });
    expect(screen.getByRole("status").textContent).toBe("1–4");
});

it("preserves the gesture anchor when a router delays and combines range updates", async () => {
    const user = userEvent.setup();
    function Controlled() {
        const [range, setRange] = useState<SourceRange>({ line: 3, end: 3 });
        const [pending, setPending] = useState(range);
        return <><StaticSource text={"one\ntwo\nthree\nfour"} path="test.py" firstLine={1} {...range} onRange={setPending} /><output>{range.line}–{range.end}</output><button onClick={() => setRange(pending)}>Apply URL</button></>;
    }
    render(<Controlled />);
    screen.getByRole("button", { name: "Выбрать строку 3" }).focus();
    await user.keyboard("{Shift>}{ArrowUp}{ArrowUp}{/Shift}");
    expect(screen.getByRole("status").textContent).toBe("3–3");
    fireEvent.click(screen.getByRole("button", { name: "Apply URL" }));
    expect(screen.getByRole("status").textContent).toBe("1–3");
    fireEvent.keyDown(screen.getByRole("button", { name: "Выбрать строку 1" }), { key: "ArrowDown", shiftKey: true });
    fireEvent.click(screen.getByRole("button", { name: "Apply URL" }));
    expect(screen.getByRole("status").textContent).toBe("2–3");
    fireEvent.click(screen.getByRole("button", { name: "Выбрать строку 4" }), { shiftKey: true });
    fireEvent.click(screen.getByRole("button", { name: "Apply URL" }));
    expect(screen.getByRole("status").textContent).toBe("3–4");
});

it("preserves standalone CR as source text without inventing an API line number", () => {
    const { container } = render(<StaticSource text={"one\rtwo\r"} path="test.py" firstLine={1} line={1} end={1} onRange={vi.fn()} />);
    expect(container.querySelector("code")?.textContent).toBe("one\rtwo\r");
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Выбрать строку 1" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Выбрать строку 2" })).toBeNull();
});

it("keeps all lines in DOM and places readable code before its separate controls", () => {
    const text = Array(350).fill("очень длинная строка 😀").join("\r\n");
    const { container } = render(<StaticSource text={text} path="large.py" firstLine={500} line={1} end={2} onRange={vi.fn()} />);
    expect(container.querySelector("code")?.textContent).toBe(text);
    expect(container.querySelectorAll(".repository-source-line")).toHaveLength(350);
    expect(container.querySelector(".repository-source-content")?.firstElementChild?.tagName).toBe("PRE");
    expect(container.querySelector(".repository-source-range")).toBeNull();
    expect(screen.getByRole("button", { name: "Выбрать строку 500" }).tabIndex).toBe(0);
});
