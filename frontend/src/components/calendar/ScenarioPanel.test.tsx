// @vitest-environment jsdom
import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { ScenarioPreview } from "@/lib/types";
import { ScenarioPanel } from "./ScenarioPanel";

const preview: ScenarioPreview = {
    changes: [{ task_id: 1, task_key: "TEST-1", task_title: "Задача", source: "DIRECT", expected_updated_at: "revision", reasons: [],
        current: { start_date: "2026-11-16", due_date: "2026-11-18" }, proposed: { start_date: "2026-11-17", due_date: "2026-11-19" } }],
    can_apply: true, conflicts: [], consequences_count: 0,
};
afterEach(cleanup);

function setup(value: ScenarioPreview = preview) {
    const onApply = vi.fn();
    function Harness() {
        const [reason, setReason] = useState("");
        return <ScenarioPanel preview={value} isPreviewing={false} isApplying={false} dateChangeReason={reason} onReasonChange={setReason} onApply={onApply} onCancel={vi.fn()} />;
    }
    render(<Harness />);
    return onApply;
}

it("применение сценария требует непустую общую причину и передаёт её без внешних пробелов", async () => {
    const onApply = setup();
    const apply = screen.getByRole("button", { name: "Apply" }) as HTMLButtonElement;
    expect(apply.disabled).toBe(true);
    const reason = screen.getByRole("textbox", { name: /Причина/ });
    fireEvent.change(reason, { target: { value: "   " } });
    expect(apply.disabled).toBe(true);
    fireEvent.change(reason, { target: { value: "  Изменён объём проекта  " } });
    await userEvent.click(apply);
    expect(onApply).toHaveBeenCalledExactlyOnceWith("Изменён объём проекта");
});

it("первое назначение дат в сценарии применяется без причины", async () => {
    const onApply = setup({ ...preview, changes: preview.changes.map(change => ({ ...change, current: { start_date: null, due_date: null } })) });
    expect(screen.queryByRole("textbox", { name: /Причина/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledExactlyOnceWith(undefined);
});

it("изменение старых дат каскадной задачи требует причину даже при первом назначении прямой", () => {
    setup({ ...preview, changes: [
        { ...preview.changes[0], current: { start_date: null, due_date: null } },
        { ...preview.changes[0], task_id: 2, task_key: "TEST-2", source: "CASCADE" },
    ], consequences_count: 1 });
    expect(screen.getByRole("textbox", { name: /Причина/ })).toBeTruthy();
    expect((screen.getByRole("button", { name: "Apply" }) as HTMLButtonElement).disabled).toBe(true);
});
