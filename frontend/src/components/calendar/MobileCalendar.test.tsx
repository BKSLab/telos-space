// @vitest-environment jsdom
import { useState } from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { CalendarGridTask } from "@/lib/types";
import type { WorkloadPerson } from "@/lib/workload";
import { CalendarSidePanel } from "./CalendarSidePanel";
import { CalendarTask } from "./CalendarTask";
import { CalendarToolbar } from "./CalendarToolbar";
import { WorkloadStrip } from "./TimelineWorkload";
import { CalendarGridView, type GridEnv } from "./grid/CalendarGridView";

function media(compact = true) {
    vi.stubGlobal("matchMedia", vi.fn((query: string) => ({
        matches: compact, media: query, onchange: null,
        addEventListener: vi.fn(), removeEventListener: vi.fn(),
        addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
    })));
}

beforeEach(() => {
    media();
    vi.stubGlobal("ResizeObserver", class { observe = vi.fn(); unobserve = vi.fn(); disconnect = vi.fn(); });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const task: CalendarGridTask = {
    id: 7, key: "TEST-7", title: "Подготовить документы для поездки", stage_id: 1, priority: "MEDIUM", assignee: null,
    start_date: "2026-11-03", due_date: "2026-11-03", baseline_start_date: null, baseline_due_date: null, drift_days: null,
    is_done: false, is_overdue: false, is_due_soon: false, risk_level: null, risk_reasons: [],
    updated_at: "2026-11-03T10:00:00Z", wbs_node_id: null,
};

function environment(): GridEnv {
    return { today: "2026-11-03", holidays: new Map(), colorMode: "status", canDrag: () => false,
        showSource: false, onOpenTask: vi.fn(), onScheduleTask: vi.fn(), onCreate: vi.fn(), onOpenPeriod: vi.fn() };
}

it("позволяет выбрать шкалу, масштаб и листать период компактными контролами", async () => {
    const user = userEvent.setup();
    const onViewChange = vi.fn();
    const onScaleChange = vi.fn();
    const onPrevious = vi.fn();
    const onNext = vi.fn();
    const onToday = vi.fn();
    render(<CalendarToolbar title="Ноябрь 2026" view="grid" scale="week" scales={[{ value: "week", label: "Неделя", hotkey: "W" }, { value: "month", label: "Месяц", hotkey: "M" }]}
        onViewChange={onViewChange} onScaleChange={onScaleChange} onPrevious={onPrevious} onNext={onNext} onToday={onToday} />);
    await user.selectOptions(screen.getByRole("combobox", { name: "Вид календаря" }), "timeline");
    await user.selectOptions(screen.getByRole("combobox", { name: "Масштаб календаря" }), "month");
    await user.click(screen.getByRole("button", { name: "Предыдущий период" }));
    await user.click(screen.getByRole("button", { name: "Следующий период" }));
    await user.click(screen.getByRole("button", { name: "Сегодня" }));
    expect(onViewChange).toHaveBeenCalledWith("timeline");
    expect(onScaleChange).toHaveBeenCalledWith("month");
    [onPrevious, onNext, onToday].forEach((callback) => expect(callback).toHaveBeenCalledOnce());
});

it("сохраняет сегменты видов и масштабов на широком экране", () => {
    media(false);
    render(<CalendarToolbar title="Ноябрь 2026" view="grid" allowSplit scale="month" scales={[{ value: "month", label: "Месяц", hotkey: "M" }]}
        onViewChange={vi.fn()} onScaleChange={vi.fn()} onPrevious={vi.fn()} onNext={vi.fn()} onToday={vi.fn()} />);
    expect(within(screen.getByRole("group", { name: "Вид календаря" })).getByRole("button", { name: "Рядом" })).toBeTruthy();
    expect(screen.queryByRole("combobox")).toBeNull();
});

it("сразу раскрывает задачи выбранного дня месяца и позволяет выбрать даты без drag", async () => {
    const env = environment();
    const user = userEvent.setup();
    render(<CalendarGridView scale="month" date="2026-11-03" tasks={[task]} milestones={[]} env={env} isPhone />);
    expect(screen.getByText(task.title)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Изменить даты TEST-7" }));
    expect(env.onScheduleTask).toHaveBeenCalledWith(task);
    expect(env.onOpenTask).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /^Новая задача$/ }));
    expect(env.onCreate).toHaveBeenCalledWith("2026-11-03");
});

it("не перехватывает прокрутку карточки без срока для touch drag", async () => {
    const onSchedule = vi.fn();
    render(<CalendarTask task={task} draggable onOpen={vi.fn()} onSchedule={onSchedule} />);
    const card = screen.getByRole("button", { name: /^Открыть TEST-7/ });
    expect(card.classList.contains("touch-none")).toBe(false);
    expect(card.getAttribute("aria-roledescription")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Изменить срок TEST-7" }));
    expect(onSchedule).toHaveBeenCalledWith(task);
});

it("листит период жестом по фону, отменяет жест и не перехватывает контролы", () => {
    const onSwipe = vi.fn();
    const { container } = render(<CalendarGridView scale="month" date="2026-11-03" tasks={[task]} milestones={[]} env={environment()} isPhone onSwipe={onSwipe} />);
    const grid = container.querySelector("[data-calendar-grid]")!;
    const start = { touches: [{ clientX: 200, clientY: 100 }] };
    const end = { changedTouches: [{ clientX: 80, clientY: 102 }] };
    fireEvent.touchStart(grid, start);
    fireEvent.touchEnd(grid, end);
    expect(onSwipe).toHaveBeenCalledWith(1);
    onSwipe.mockClear();
    fireEvent.touchStart(grid, start);
    fireEvent.touchCancel(grid);
    fireEvent.touchEnd(grid, end);
    const button = screen.getByRole("button", { name: /^Новая задача$/ });
    fireEvent.touchStart(button, start);
    fireEvent.touchEnd(button, end);
    expect(onSwipe).not.toHaveBeenCalled();
});

it("открывает мобильную панель как диалог с переключаемыми вкладками и закрытием", async () => {
    const onClose = vi.fn();
    function Panel() {
        const [active, setActive] = useState("milestones");
        return <CalendarSidePanel active={active} onActiveChange={setActive} onClose={onClose} tabs={[
            { id: "milestones", label: "Вехи", title: "Вехи", content: <p>Контрольные точки</p> },
            { id: "unscheduled", label: "Без срока", title: "Задачи без дат", content: <p>Выберите даты задачи</p> },
        ]} />;
    }
    render(<Panel />);
    const dialog = screen.getByRole("dialog", { name: "Панель календаря" });
    fireEvent.keyDown(within(dialog).getByRole("tab", { name: "Вехи" }), { key: "ArrowRight" });
    expect(within(dialog).getByRole("tabpanel", { name: "Без срока" }).textContent).toContain("Выберите даты задачи");
    await userEvent.click(within(dialog).getByRole("button", { name: "Свернуть панель" }));
    expect(onClose).toHaveBeenCalledOnce();
});

it("показывает список задач загрузки по нажатию вместо подсказки только при наведении", async () => {
    const person: WorkloadPerson = { user_id: 1, name: "Анна", other_tasks: [], other_hidden: 1, days: [
        { date: "2026-11-03", project_tasks: 1, project_keys: ["TEST-7"], other_tasks: 2, other_keys: ["OTHER-1"], level: 3 },
    ] };
    render(<WorkloadStrip person={person} range={{ dateFrom: "2026-11-01", dateTo: "2026-11-30" }} dayWidth={44} height={64} />);
    await userEvent.click(screen.getByRole("button", { name: "Показать загрузку Анна на 2026-11-03" }));
    const dialog = screen.getByRole("dialog", { name: "Загрузка: Анна" });
    expect(dialog.textContent).toContain("3 задачи — перегруз");
    expect(dialog.textContent).toContain("В проекте: TEST-7");
    expect(dialog.textContent).toContain("В других проектах: OTHER-1, 1 без доступа к проекту");
});
