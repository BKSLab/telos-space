// @vitest-environment jsdom
import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ContextMenu } from "./ContextMenu";
import { TaskCard } from "./nodes/TaskCard";
import type { TaskCompact } from "@/lib/types";

afterEach(cleanup);

it("opens task actions by a visible button without opening the task itself", () => {
    const open = vi.fn();
    const menu = vi.fn();
    const task = { id: 10, key: "DEMO-10", title: "Задача", priority: "MEDIUM", is_done: false, due_date: null } as TaskCompact;
    render(<div onClick={open}><TaskCard task={task} stage={undefined} detail="full" isFloating={false} isDraft={false} onContextMenu={menu} /></div>);
    fireEvent.click(screen.getByRole("button", { name: "Действия задачи DEMO-10" }));
    expect(menu).toHaveBeenCalledWith(10, { x: 0, y: 0 });
    expect(open).not.toHaveBeenCalled();
});

it("supports keyboard navigation, disabled actions and restoring focus on close", () => {
    const select = vi.fn();
    function Example() {
        const [open, setOpen] = useState(false);
        return <><button onClick={() => setOpen(true)}>Открыть действия</button>{open && <ContextMenu anchor={{ x: 3, y: 3 }} label="Действия задачи" onClose={() => setOpen(false)} items={[
            { key: "disabled", label: "Недоступно", disabled: true, onSelect: vi.fn() },
            { key: "move", label: "Переместить", onSelect: select },
        ]} />}</>;
    }
    render(<Example />);
    const trigger = screen.getByRole("button", { name: "Открыть действия" });
    trigger.focus();
    fireEvent.click(trigger);
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Переместить" }));
    fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Закрыть меню" }));
    fireEvent.keyDown(document.activeElement!, { key: "ArrowUp" });
    fireEvent.click(document.activeElement!);
    expect(select).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(trigger);
});
