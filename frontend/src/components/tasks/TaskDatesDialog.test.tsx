// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { TaskDatesDialog } from "./TaskDatesDialog";

const task = { id: 22, key: "TELOS-22", title: "Проверить перенос", start_date: "2026-11-19", due_date: "2026-11-21" };
afterEach(cleanup);

it("отправляет обе даты с непустой причиной только после подтверждения", async () => {
    const onSave = vi.fn();
    render(<TaskDatesDialog task={task} initialDates={{ startDate: "2026-11-21", dueDate: "2026-11-24" }}
        isSaving={false} onClose={vi.fn()} onSave={onSave} />);
    const save = screen.getByRole("button", { name: "Сохранить сроки" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    const reason = screen.getByRole("textbox", { name: /Причина изменения/ });
    fireEvent.change(reason, { target: { value: "   " } });
    expect(save.disabled).toBe(true);
    expect(onSave).not.toHaveBeenCalled();
    fireEvent.change(reason, { target: { value: "  Ждём согласование\nот заказчика  " } });
    await userEvent.click(save);
    expect(onSave).toHaveBeenCalledExactlyOnceWith({ startDate: "2026-11-21", dueDate: "2026-11-24", dateChangeReason: "Ждём согласование\nот заказчика" });
});

it.each([
    { title: "обе даты", original: { start_date: null, due_date: null }, proposed: { startDate: "2026-11-19", dueDate: "2026-11-24" } },
    { title: "только начало", original: { start_date: null, due_date: "2026-11-24" }, proposed: { startDate: "2026-11-19", dueDate: "2026-11-24" } },
    { title: "только завершение", original: { start_date: "2026-11-19", due_date: null }, proposed: { startDate: "2026-11-19", dueDate: "2026-11-24" } },
])("первое назначение ($title) не запрашивает причину", async ({ original, proposed }) => {
    const onSave = vi.fn();
    render(<TaskDatesDialog task={{ ...task, ...original }} initialDates={proposed} isSaving={false} onClose={vi.fn()} onSave={onSave} />);
    expect(screen.queryByRole("textbox", { name: /Причина/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Сохранить сроки" }));
    expect(onSave).toHaveBeenCalledExactlyOnceWith(proposed);
});

it("возврат к исходным датам убирает запрос причины, снятие даты требует её", () => {
    render(<TaskDatesDialog task={task} isSaving={false} onClose={vi.fn()} onSave={vi.fn()} />);
    const due = screen.getByLabelText("Завершение");
    const save = screen.getByRole("button", { name: "Сохранить сроки" }) as HTMLButtonElement;
    fireEvent.change(due, { target: { value: "2026-11-24" } });
    expect(screen.getByRole("textbox", { name: /Причина/ })).toBeTruthy();
    fireEvent.change(due, { target: { value: task.due_date } });
    expect(screen.queryByRole("textbox", { name: /Причина/ })).toBeNull();
    expect(save.disabled).toBe(true);
    fireEvent.change(due, { target: { value: "" } });
    expect(screen.getByRole("textbox", { name: /Причина/ })).toBeTruthy();
    expect(save.disabled).toBe(true);
});

it("ошибка сохраняет введённые данные, отмена не вызывает сохранение", async () => {
    const onSave = vi.fn();
    const onClose = vi.fn();
    const props = { task, isSaving: false, onClose, onSave };
    const { rerender } = render(<TaskDatesDialog {...props} />);
    fireEvent.change(screen.getByLabelText("Завершение"), { target: { value: "2026-11-24" } });
    fireEvent.change(screen.getByRole("textbox", { name: /Причина/ }), { target: { value: "Моя причина" } });
    rerender(<TaskDatesDialog {...props} error="Задача уже изменена" />);
    expect((screen.getByLabelText("Завершение") as HTMLInputElement).value).toBe("2026-11-24");
    expect((screen.getByRole("textbox", { name: /Причина/ }) as HTMLTextAreaElement).value).toBe("Моя причина");
    await userEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(onClose).toHaveBeenCalledOnce();
    expect(onSave).not.toHaveBeenCalled();
});

it("не сохраняет обратный диапазон, не закрывается при отправке", async () => {
    const onClose = vi.fn();
    const props = { task, isSaving: false, onClose, onSave: vi.fn() };
    const { rerender } = render(<TaskDatesDialog {...props} />);
    fireEvent.change(screen.getByLabelText("Начало"), { target: { value: "2026-11-25" } });
    fireEvent.change(screen.getByRole("textbox", { name: /Причина/ }), { target: { value: "Причина" } });
    expect(screen.getByText("Завершение не может быть раньше начала.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Сохранить сроки" }) as HTMLButtonElement).disabled).toBe(true);
    rerender(<TaskDatesDialog {...props} isSaving />);
    await userEvent.click(screen.getByRole("button", { name: "Закрыть" }));
    expect(onClose).not.toHaveBeenCalled();
    expect((screen.getByLabelText("Начало") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Отмена" }) as HTMLButtonElement).disabled).toBe(true);
});
