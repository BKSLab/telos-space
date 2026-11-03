// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { useChatHistory } from "./useChatHistory";

const rect = (top: number, height: number) => ({ top, bottom: top + height, left: 0, right: 300, width: 300, height, x: 0, y: top, toJSON: () => ({}) }) as DOMRect;

function History({ rows, session = "one" }: { rows: string[]; session?: string }) {
    const { viewportRef, contentRef, atBottom, onScroll, scrollToLatest } = useChatHistory(session, rows.join("|"));
    return <>
        <div data-testid="history" ref={element => {
            viewportRef.current = element;
            if (!element || element.dataset.metrics) return;
            element.dataset.metrics = "true";
            let scroll = 0;
            Object.defineProperties(element, {
                clientHeight: { get: () => 200 },
                scrollHeight: { get: () => element.querySelectorAll("[data-chat-message]").length * 100 },
                scrollTop: { get: () => scroll, set: value => { scroll = Math.max(0, Math.min(value, element.scrollHeight - element.clientHeight)); } },
            });
            element.getBoundingClientRect = () => rect(0, 200);
        }} onScroll={onScroll}>
            <div ref={contentRef}>{rows.map(id => <div key={id} data-chat-message={id} ref={row => {
                if (!row) return;
                row.getBoundingClientRect = () => {
                    const root = viewportRef.current!;
                    const index = [...root.querySelectorAll("[data-chat-message]")].indexOf(row);
                    return rect(index * 100 - root.scrollTop, 100);
                };
            }}>{id}</div>)}</div>
        </div>
        {!atBottom && <button onClick={scrollToLatest}>Последние</button>}
    </>;
}
afterEach(cleanup);

it("сохраняет читаемое сообщение при подгрузке старых сообщений и новых ответах", () => {
    const { rerender } = render(<History rows={["3", "4", "5", "6", "7"]} />);
    const viewport = screen.getByTestId("history");
    expect(viewport.scrollTop).toBe(300);
    viewport.scrollTop = 100;
    fireEvent.scroll(viewport);
    expect(screen.getByRole("button", { name: "Последние" })).toBeTruthy();
    rerender(<History rows={["1", "2", "3", "4", "5", "6", "7"]} />);
    expect(viewport.scrollTop).toBe(300);
    rerender(<History rows={["1", "2", "3", "4", "5", "6", "7", "8"]} />);
    expect(viewport.scrollTop).toBe(300);
    fireEvent.click(screen.getByRole("button", { name: "Последние" }));
    expect(viewport.scrollTop).toBe(600);
    rerender(<History rows={["1", "2", "3", "4", "5", "6", "7", "8", "9"]} />);
    expect(viewport.scrollTop).toBe(700);
});

it("при выборе другого диалога открывает его конец отдельно от позиции прежней истории", () => {
    const { rerender } = render(<History rows={["1", "2", "3", "4", "5"]} />);
    const viewport = screen.getByTestId("history");
    viewport.scrollTop = 50;
    fireEvent.scroll(viewport);
    rerender(<History session="two" rows={["20", "21", "22", "23"]} />);
    expect(viewport.scrollTop).toBe(200);
    expect(screen.queryByRole("button", { name: "Последние" })).toBeNull();
});
