import { useMemo, useState } from "react";
import {
    DndContext,
    DragOverlay,
    KeyboardSensor,
    PointerSensor,
    pointerWithin,
    rectIntersection,
    useSensor,
    useSensors,
    type CollisionDetection,
    type DragEndEvent,
    type DragStartEvent,
} from "@dnd-kit/core";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { CalendarDays, Filter, FolderKanban, Inbox, PanelRightClose, PanelRightOpen } from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { CalendarPulseChips, type PulseChip } from "@/components/calendar/CalendarPulseChips";
import { CalendarSidePanel } from "@/components/calendar/CalendarSidePanel";
import { CalendarTask } from "@/components/calendar/CalendarTask";
import { CalendarToolbar } from "@/components/calendar/CalendarToolbar";
import { ColorModeSelect } from "@/components/calendar/ColorModeSelect";
import { DeadlineDialog } from "@/components/calendar/DeadlineDialog";
import { IcsSubscribePopover } from "@/components/calendar/IcsSubscribePopover";
import { PortfolioTimeline } from "@/components/calendar/PortfolioTimeline";
import { TimelineGrid } from "@/components/calendar/TimelineGrid";
import { UnscheduledList } from "@/components/calendar/UnscheduledList";
import { CalendarGridView, type GridEnv } from "@/components/calendar/grid/CalendarGridView";
import { gridGrabDate } from "@/components/calendar/grid/gridHelpers";
import { CreatePersonalTaskDialog } from "@/components/tasks/CreatePersonalTaskDialog";
import { IconButton } from "@/components/ui/Button";
import { Select } from "@/components/ui/Field";
import { Popover } from "@/components/ui/Popover";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import { api, endpoints, queryKeys } from "@/lib/api";
import {
    buildTimelineDays,
    dateFromDropId,
    localToday,
    moveTaskInterval,
    normalizeDate,
    rescheduleCalendarTask,
    resizeTaskInterval,
    shiftTimelineAnchor,
    timelineRange,
    timelineTitle,
    type TimelineScale,
} from "@/lib/calendar";
import {
    GRID_SCALES,
    TIMELINE_SCALES,
    gridRange,
    gridTitle,
    shiftTaskDates,
    type ColorMode,
} from "@/lib/calendarGrid";
import { TASK_STATUS_LABELS, TASK_STATUS_ORDER } from "@/lib/tasks";
import { plural } from "@/lib/plural";
import { useToast } from "@/lib/toast";
import type {
    CalendarGridTask,
    CalendarStage,
    CalendarTask as CalendarTaskModel,
    MyCalendar,
    MyCalendarTask,
    MyUnscheduledTasksPage,
    PortfolioCalendar,
    Project,
    Task,
    TaskPriority,
} from "@/lib/types";
import { PRIORITY_LABELS, PRIORITY_ORDER } from "@/lib/types";
import {
    useCalendarHotkeys,
    useCalendarViewState,
    useHolidays,
    useMediaQuery,
    useStoredChoice,
    useStoredFlag,
} from "@/lib/useCalendarView";
import { cn } from "@/lib/cn";
import { useUiStore } from "@/stores/ui";

const FILTER_NAMES = ["status", "priority", "project"] as const;
const NO_TASKS: MyCalendarTask[] = [];
const COLOR_MODES: ColorMode[] = ["project", "status", "assignee"];
const PORTFOLIO_SCALES = TIMELINE_SCALES.filter((item) => item.value === "quarter" || item.value === "year");

const gridCollision: CollisionDetection = (args) => {
    const hits = pointerWithin(args);
    return hits.length > 0 ? hits : rectIntersection(args);
};

/**
 * «Мой календарь» (/calendar): все мои задачи — проектов и разовые — в сетке или на
 * шкале по проектам; вкладка «Проекты» — портфель на шкале. Контракт — docs/calendar.md.
 */
export function MyCalendarPage() {
    const [searchParams, setSearchParams] = useSearchParams();
    const tab = searchParams.get("tab") === "portfolio" ? "portfolio" : "mine";

    function switchTab(next: "mine" | "portfolio") {
        const params = new URLSearchParams();
        if (next === "portfolio") params.set("tab", "portfolio");
        setSearchParams(params, { replace: true });
    }

    return (
        <div className="instrument-surface flex h-full min-h-0 flex-col [@media(max-height:500px)]:overflow-y-auto [@media(max-height:500px)]:overscroll-contain">
            <div className="flex shrink-0 items-center gap-1 px-3 pt-2 sm:px-4" role="tablist" aria-label="Разделы календаря">
                {(
                    [
                        { id: "mine", label: "Мои задачи", icon: <CalendarDays size={13} aria-hidden="true" />, title: "Сроки всех ваших задач: проекты и разовые" },
                        { id: "portfolio", label: "Проекты", icon: <FolderKanban size={13} aria-hidden="true" />, title: "Сроки и вехи ваших проектов на одной шкале" },
                    ] as const
                ).map((item) => (
                    <button
                        key={item.id}
                        type="button"
                        role="tab"
                        aria-selected={tab === item.id}
                        title={item.title}
                        onClick={() => switchTab(item.id)}
                        className={cn(
                            "brand-tab flex h-11 items-center gap-1.5 border-b px-3 text-[13px] lg:h-8",
                            tab === item.id ? "border-accent bg-accent/[0.075] font-semibold text-primary" : "border-transparent text-muted hover:bg-hover hover:text-primary",
                        )}
                    >
                        {item.icon}
                        {item.label}
                    </button>
                ))}
            </div>
            {tab === "mine" ? <MyTasksCalendar /> : <PortfolioCalendarView />}
        </div>
    );
}

export default MyCalendarPage;

function sourceGroupId(task: MyCalendarTask, ids: Map<string, number>): number {
    const key = `${task.source.kind}:${task.source.id ?? "none"}`;
    let id = ids.get(key);
    if (id === undefined) {
        id = -(ids.size + 1);
        ids.set(key, id);
    }
    return id;
}

function MyTasksCalendar() {
    const queryClient = useQueryClient();
    const toast = useToast();
    const setSelectedTaskId = useUiStore((state) => state.setSelectedTaskId);
    const [searchParams, setSearchParams] = useSearchParams();
    const today = localToday();
    const isPhone = useMediaQuery("(max-width: 1023px)");
    const isTouch = useMediaQuery("(pointer: coarse)");
    const viewState = useCalendarViewState(
        isPhone ? "tt.calendar.my.view.phone" : "tt.calendar.my.view",
        { view: "grid", gridScale: isPhone ? "week" : "month", timelineScale: "month" },
        today,
    );
    // «Рядом» есть только у календаря проекта; ссылка с view=split открывает сетку.
    const view = viewState.view === "split" ? "grid" : viewState.view;
    const { gridScale, timelineScale, date } = viewState;
    const range = useMemo(
        () => (view === "grid" ? gridRange(gridScale, date) : timelineRange(date, timelineScale)),
        [date, gridScale, timelineScale, view],
    );
    const { holidays } = useHolidays(range);
    const [colorMode, setColorMode] = useStoredChoice<ColorMode>("tt.calendar.my.color", COLOR_MODES, "project");
    const [isPanelOpen, setPanelOpen] = useStoredFlag("tt.calendar.my.panel", false);
    const [createDate, setCreateDate] = useState<string | null>(null);
    const [activeTask, setActiveTask] = useState<CalendarGridTask | null>(null);
    const [deadlineTask, setDeadlineTask] = useState<CalendarGridTask | null>(null);
    const statusFilter = searchParams.get("status") ?? "";
    const priorityFilter = searchParams.get("priority") ?? "";
    const projectFilter = searchParams.get("project") ?? "";
    const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }), useSensor(KeyboardSensor));

    const commonParams = useMemo(() => {
        const params = new URLSearchParams({ today });
        if (statusFilter) params.set("status", statusFilter);
        if (priorityFilter) params.set("priority", priorityFilter);
        if (projectFilter) params.set("project_id", projectFilter);
        return params;
    }, [priorityFilter, projectFilter, statusFilter, today]);
    const rangeParams = useMemo(() => {
        const params = new URLSearchParams(commonParams);
        params.set("date_from", range.dateFrom);
        params.set("date_to", range.dateTo);
        return params;
    }, [commonParams, range]);
    const calendarKey = queryKeys.myCalendar(rangeParams.toString());
    const unscheduledKey = queryKeys.myCalendarUnscheduled(commonParams.toString());

    const calendarQuery = useQuery({
        queryKey: calendarKey,
        queryFn: () => api.get<MyCalendar>(`${endpoints.myCalendar()}?${rangeParams.toString()}`),
        placeholderData: (previous) => previous,
    });
    const unscheduledQuery = useInfiniteQuery<MyUnscheduledTasksPage>({
        queryKey: unscheduledKey,
        initialPageParam: null,
        queryFn: ({ pageParam }) => {
            const params = new URLSearchParams(commonParams);
            params.set("limit", "50");
            if (pageParam !== null) params.set("cursor", String(pageParam));
            return api.get<MyUnscheduledTasksPage>(`${endpoints.myCalendarUnscheduled()}?${params.toString()}`);
        },
        getNextPageParam: (page) => page.next_cursor ?? undefined,
    });
    const projectsQuery = useQuery({ queryKey: queryKeys.projects, queryFn: () => api.get<Project[]>(endpoints.projects()) });

    const datesMutation = useMutation({
        mutationFn: ({ taskId, dueDate, startDate }: { taskId: number; dueDate: string | null; startDate: string | null }) =>
            api.patch<Task>(endpoints.task(taskId), { due_date: dueDate, start_date: startDate }),
        onMutate: async ({ taskId, dueDate, startDate }) => {
            await Promise.all([queryClient.cancelQueries({ queryKey: calendarKey }), queryClient.cancelQueries({ queryKey: unscheduledKey })]);
            const previousCalendar = queryClient.getQueryData<MyCalendar>(calendarKey);
            const previousUnscheduled = queryClient.getQueryData<InfiniteData<MyUnscheduledTasksPage>>(unscheduledKey);
            const source =
                previousCalendar?.tasks.find((task) => task.id === taskId) ??
                previousUnscheduled?.pages.flatMap((page) => page.items).find((task) => task.id === taskId);
            if (source) {
                const changed = {
                    ...source,
                    ...rescheduleCalendarTask(source as unknown as CalendarTaskModel, dueDate, today, startDate),
                    stage_id: source.stage_id,
                    source: source.source,
                } as MyCalendarTask;
                queryClient.setQueryData<MyCalendar>(calendarKey, (old) =>
                    old ? { ...old, tasks: [...old.tasks.filter((task) => task.id !== taskId), ...(dueDate || startDate ? [changed] : [])] } : old,
                );
                queryClient.setQueryData<InfiniteData<MyUnscheduledTasksPage>>(unscheduledKey, (old) =>
                    old ? { ...old, pages: old.pages.map((page) => ({ ...page, items: page.items.filter((task) => task.id !== taskId) })) } : old,
                );
            }
            return { previousCalendar, previousUnscheduled };
        },
        onError: (error, _variables, context) => {
            if (context?.previousCalendar) queryClient.setQueryData(calendarKey, context.previousCalendar);
            if (context?.previousUnscheduled) queryClient.setQueryData(unscheduledKey, context.previousUnscheduled);
            toast.error(`Не удалось изменить даты: ${(error as Error).message}`);
        },
        onSuccess: () => toast.success("Плановые даты задачи обновлены."),
        onSettled: (_data, _error, variables) => {
            queryClient.invalidateQueries({ queryKey: ["me", "calendar"] });
            queryClient.invalidateQueries({ queryKey: ["projects"] });
            queryClient.invalidateQueries({ queryKey: queryKeys.task(variables.taskId) });
        },
    });

    function changeDates(task: CalendarGridTask, dates: { startDate?: string | null; dueDate: string | null }) {
        const startDate = dates.startDate === undefined ? task.start_date : dates.startDate;
        if (task.due_date === dates.dueDate && task.start_date === startDate) return;
        datesMutation.mutate({ taskId: task.id, dueDate: dates.dueDate, startDate });
    }

    function setFilter(name: string, value: string) {
        const next = new URLSearchParams(searchParams);
        if (value) next.set(name, value);
        else next.delete(name);
        setSearchParams(next, { replace: true });
    }

    function resetFilters() {
        const next = new URLSearchParams(searchParams);
        for (const name of FILTER_NAMES) next.delete(name);
        setSearchParams(next, { replace: true });
    }

    const movePeriod = viewState.shift;

    useCalendarHotkeys((action) => {
        if (action === "prev") movePeriod(-1);
        else if (action === "next") movePeriod(1);
        else if (action === "today") viewState.setDate(today);
        else if (view === "timeline" && action !== "day") viewState.setTimelineScale(action);
        else viewState.setGridScale(action);
    });

    function handleDragStart(event: DragStartEvent) {
        setActiveTask((event.active.data.current?.task as CalendarGridTask | undefined) ?? null);
    }

    function handleDragEnd(event: DragEndEvent) {
        const data = event.active.data.current as { task?: CalendarGridTask; dragKind?: string; date?: string } | undefined;
        const targetDate = event.over ? dateFromDropId(event.over.id) : null;
        setActiveTask(null);
        const task = data?.task;
        if (!task || !targetDate) return;
        const original = taskById.get(task.id) ?? task;
        if (data.dragKind === "grid" && data.date) {
            const shifted = shiftTaskDates(original, gridGrabDate(event.active.id, data.date), targetDate);
            if (shifted) changeDates(original, shifted);
        } else if (data.dragKind === "interval") {
            changeDates(original, moveTaskInterval(original, targetDate));
        } else if (data.dragKind === "unscheduled") {
            changeDates(original, view === "grid" ? { startDate: null, dueDate: targetDate } : { startDate: targetDate, dueDate: targetDate });
        } else {
            changeDates(original, { dueDate: targetDate });
        }
    }

    const tasks = calendarQuery.data?.tasks ?? NO_TASKS;
    const taskById = new Map<number, CalendarGridTask>(tasks.map((task) => [task.id, task]));
    const unscheduledTasks = useMemo(() => unscheduledQuery.data?.pages.flatMap((page) => page.items) ?? [], [unscheduledQuery.data?.pages]);
    // Шкала группирует по источнику: каждый проект и разовые задачи — «этап» со своим цветом.
    const timeline = useMemo(() => {
        const ids = new Map<string, number>();
        const stages = new Map<number, CalendarStage>();
        const mapped = tasks.map((task) => {
            const id = sourceGroupId(task, ids);
            if (!stages.has(id)) {
                stages.set(id, {
                    id,
                    name: task.source.key ? `${task.source.key} · ${task.source.name ?? ""}` : task.source.name ?? "Разовые задачи",
                    color: task.source.color ?? "var(--color-text-muted)",
                    order_index: stages.size,
                    is_done_stage: false,
                });
            }
            return { ...task, stage_id: id } as CalendarTaskModel;
        });
        return { tasks: mapped, stagesById: stages };
    }, [tasks]);
    const days = useMemo(() => buildTimelineDays(range, today, holidays), [holidays, range, today]);

    const summary = calendarQuery.data?.summary;
    const overdue = summary?.overdue ?? tasks.filter((task) => task.is_overdue && !task.is_done).length;
    const dueInPeriod = tasks.filter((task) => task.due_date !== null && task.due_date >= range.dateFrom && task.due_date <= range.dateTo).length;
    const unscheduledCount = summary?.unscheduled ?? unscheduledTasks.length;
    const chips: PulseChip[] = [
        { id: "period", label: `${dueInPeriod} ${plural(dueInPeriod, "срок", "срока", "сроков")} в периоде`, title: "Ваших задач со сроком в показанном периоде", tone: "muted" },
        ...(overdue > 0 ? [{ id: "overdue", label: `${overdue} просрочено`, title: "Ваши незавершённые задачи с прошедшим сроком — по всем периодам", tone: "danger" as const }] : []),
        ...(summary && summary.due_soon > 0 ? [{ id: "soon", label: `${summary.due_soon} на 7 дней`, title: "Ваши задачи со сроком в ближайшие 7 дней", tone: "warn" as const }] : []),
        ...(unscheduledCount > 0
            ? [{ id: "unscheduled", label: `${unscheduledCount} без срока`, title: "Открыть список задач без срока — их можно перетащить на день", icon: <Inbox size={12} aria-hidden="true" />, onClick: () => setPanelOpen(true) }]
            : []),
        ...(calendarQuery.data?.truncated
            ? [{ id: "truncated", label: "показаны не все", title: "В периоде больше 2000 задач — показана часть. Сузьте период или включите фильтр.", tone: "warn" as const }]
            : []),
    ];

    const gridEnv: GridEnv = {
        today,
        holidays,
        colorMode,
        canDrag: () => !isPhone && !isTouch,
        showSource: true,
        onOpenTask: setSelectedTaskId,
        onScheduleTask: setDeadlineTask,
        onCreate: setCreateDate,
        onOpenPeriod: (scale, value) => viewState.setGridScale(scale, value),
    };

    const hasFilters = FILTER_NAMES.some((name) => searchParams.has(name));
    const activeFilterCount = FILTER_NAMES.filter((name) => searchParams.has(name)).length;
    const error = calendarQuery.error ?? unscheduledQuery.error;
    const title = view === "grid" ? gridTitle(gridScale, date) : timelineTitle(range, timelineScale);

    return (
        <>
            <div className="flex shrink-0 flex-col gap-2 border-b border-line px-3 py-2 sm:px-4">
                <CalendarToolbar
                    title={title}
                    view={view}
                    activeFilterCount={activeFilterCount}
                    onViewChange={viewState.setView}
                    scale={view === "grid" ? gridScale : timelineScale}
                    scales={view === "grid" ? GRID_SCALES : TIMELINE_SCALES}
                    onScaleChange={(value) => (view === "grid" ? viewState.setGridScale(value as typeof gridScale) : viewState.setTimelineScale(value as TimelineScale))}
                    onToday={() => viewState.setDate(today)}
                    onPrevious={() => movePeriod(-1)}
                    onNext={() => movePeriod(1)}
                >
                    {view === "grid" && <ColorModeSelect value={colorMode} modes={COLOR_MODES} onChange={setColorMode} />}
                    <Popover
                        label="Фильтры календаря"
                        trigger={
                            <>
                                <Filter size={13} aria-hidden="true" />
                                <span>Фильтры</span>
                                {activeFilterCount > 0 && <span className="font-mono text-[10px] text-accent">· {activeFilterCount}</span>}
                            </>
                        }
                        triggerClassName={hasFilters ? "border-accent-border text-accent" : undefined}
                    >
                        <div className="mb-3 flex items-center justify-between gap-3">
                            <p className="text-[11px] font-semibold tracking-[0.08em] text-muted uppercase">Видимые задачи</p>
                            {hasFilters && (
                                <button type="button" onClick={resetFilters} className="min-h-11 px-2 text-[12px] text-muted hover:text-accent lg:min-h-0 lg:px-0 lg:text-[11px]">
                                    Сбросить
                                </button>
                            )}
                        </div>
                        <div className="flex flex-col gap-2">
                            <Select aria-label="Фильтр по статусу" value={statusFilter} onChange={(event) => setFilter("status", event.target.value)}>
                                <option value="">Все статусы</option>
                                {TASK_STATUS_ORDER.map((status) => (
                                    <option key={status} value={status}>
                                        {TASK_STATUS_LABELS[status]}
                                    </option>
                                ))}
                            </Select>
                            <Select aria-label="Фильтр по приоритету" value={priorityFilter} onChange={(event) => setFilter("priority", event.target.value)}>
                                <option value="">Все приоритеты</option>
                                {PRIORITY_ORDER.map((priority: TaskPriority) => (
                                    <option key={priority} value={priority}>
                                        {PRIORITY_LABELS[priority]}
                                    </option>
                                ))}
                            </Select>
                            <Select aria-label="Фильтр по проекту" value={projectFilter} onChange={(event) => setFilter("project", event.target.value)}>
                                <option value="">Все проекты и разовые</option>
                                {(projectsQuery.data ?? []).map((project) => (
                                    <option key={project.id} value={project.id}>
                                        {project.key} · {project.name}
                                    </option>
                                ))}
                            </Select>
                        </div>
                    </Popover>
                    <IcsSubscribePopover />
                    <IconButton
                        size="sm"
                        variant={isPanelOpen ? "secondary" : "ghost"}
                        label={isPanelOpen ? "Скрыть задачи без срока" : "Показать задачи без срока"}
                        title={isPanelOpen ? "Скрыть панель «Без срока»" : "Панель «Без срока»: задачи без дат — перетащите на день"}
                        aria-pressed={isPanelOpen}
                        onClick={() => setPanelOpen(!isPanelOpen)}
                    >
                        {isPanelOpen ? <PanelRightClose size={14} aria-hidden="true" /> : <PanelRightOpen size={14} aria-hidden="true" />}
                    </IconButton>
                </CalendarToolbar>
                <CalendarPulseChips chips={chips} />
            </div>
            <DndContext
                sensors={sensors}
                collisionDetection={view === "grid" ? gridCollision : rectIntersection}
                onDragStart={handleDragStart}
                onDragEnd={handleDragEnd}
                onDragCancel={() => setActiveTask(null)}
            >
                <div className="relative flex min-h-0 flex-1 [@media(max-height:500px)]:min-h-[180px] [@media(max-height:500px)]:shrink-0">
                    <div className={view === "grid" ? "scrollbar-thin min-w-0 flex-1 overflow-auto" : "flex min-w-0 flex-1 flex-col p-2 sm:p-3"} data-calendar-body={view}>
                        {error && <ErrorMessage message={(error as Error).message} />}
                        {calendarQuery.isPending && !error && (
                            <div role="status" aria-label="Загрузка календаря" className="h-full p-3">
                                <Skeleton className="h-full min-h-[420px] w-full" />
                            </div>
                        )}
                        {calendarQuery.data && view === "grid" && (
                            <>
                                {tasks.length === 0 && gridScale !== "day" && (
                                    <p className="px-3 pt-2 text-[12px] text-muted">
                                        На вас в этом периоде задач со сроком нет. Кликните по дню, чтобы поставить задачу, или откройте год — там видны все сроки.
                                    </p>
                                )}
                                <CalendarGridView
                                    scale={gridScale}
                                    date={date}
                                    tasks={tasks}
                                    milestones={calendarQuery.data.milestones}
                                    env={gridEnv}
                                    isPhone={isPhone}
                                    onSwipe={movePeriod}
                                />
                            </>
                        )}
                        {calendarQuery.data && view === "timeline" && (
                            <TimelineGrid
                                days={days}
                                range={range}
                                scale={timelineScale}
                                tasks={timeline.tasks}
                                stagesById={timeline.stagesById}
                                milestones={calendarQuery.data.milestones}
                                dependencies={[]}
                                scenarioChanges={[]}
                                selectedDate={date}
                                onSelectDate={(value, scale) => viewState.setGridScale(scale, value)}
                                onOpenTask={setSelectedTaskId}
                                onScheduleTask={(task) => setDeadlineTask(taskById.get(task.id) ?? null)}
                                onResizeTask={(task, edge, delta) => {
                                    const original = taskById.get(task.id);
                                    const resized = original ? resizeTaskInterval(original, edge, delta) : null;
                                    if (original && resized) changeDates(original, resized);
                                }}
                            />
                        )}
                    </div>
                    {isPanelOpen && (
                        <CalendarSidePanel<"unscheduled">
                            active="unscheduled"
                            onActiveChange={() => undefined}
                            onClose={() => setPanelOpen(false)}
                            tabs={[
                                {
                                    id: "unscheduled",
                                    label: "Без срока",
                                    title: "Ваши задачи без плановых дат — перетащите на день, чтобы назначить срок",
                                    count: unscheduledTasks.length,
                                    content: (
                                        <UnscheduledList
                                            tasks={unscheduledTasks}
                                            canDrag
                                            hasMore={unscheduledQuery.hasNextPage}
                                            isLoadingMore={unscheduledQuery.isFetchingNextPage}
                                            onOpenTask={setSelectedTaskId}
                                            onScheduleTask={setDeadlineTask}
                                            onLoadMore={() => void unscheduledQuery.fetchNextPage()}
                                        />
                                    ),
                                },
                            ]}
                        />
                    )}
                </div>
                <DragOverlay>
                    {activeTask && (
                        <div className="w-64">
                            <CalendarTask task={activeTask} stageName={activeTask.source?.name ?? undefined} onOpen={() => undefined} />
                        </div>
                    )}
                </DragOverlay>
            </DndContext>
            {deadlineTask && (
                <DeadlineDialog
                    key={deadlineTask.id}
                    task={deadlineTask as unknown as CalendarTaskModel}
                    isSaving={datesMutation.isPending}
                    onClose={() => setDeadlineTask(null)}
                    onSave={(startDate, dueDate) => {
                        changeDates(deadlineTask, { startDate, dueDate });
                        setDeadlineTask(null);
                    }}
                />
            )}
            {createDate !== null && (
                <CreatePersonalTaskDialog
                    key={createDate}
                    initialDueDate={createDate}
                    onClose={() => setCreateDate(null)}
                    onCreated={(task) => {
                        setCreateDate(null);
                        queryClient.invalidateQueries({ queryKey: ["me", "calendar"] });
                        setSelectedTaskId(task.id);
                    }}
                />
            )}
        </>
    );
}

function PortfolioCalendarView() {
    const navigate = useNavigate();
    const [searchParams, setSearchParams] = useSearchParams();
    const today = localToday();
    const scale: TimelineScale = searchParams.get("scale") === "year" ? "year" : "quarter";
    const date = normalizeDate(searchParams.get("date"), today);
    const range = useMemo(() => timelineRange(date, scale), [date, scale]);
    const { holidays } = useHolidays(range);
    const days = useMemo(() => buildTimelineDays(range, today, holidays), [holidays, range, today]);
    const query = useQuery({
        queryKey: queryKeys.portfolioCalendar(`${range.dateFrom}:${range.dateTo}:${today}`),
        queryFn: () =>
            api.get<PortfolioCalendar>(`${endpoints.portfolioCalendar()}?${new URLSearchParams({ date_from: range.dateFrom, date_to: range.dateTo, today })}`),
        placeholderData: (previous) => previous,
    });

    function update(next: { scale?: TimelineScale; date?: string }) {
        const params = new URLSearchParams(searchParams);
        params.set("tab", "portfolio");
        params.set("scale", next.scale ?? scale);
        params.set("date", next.date ?? date);
        setSearchParams(params, { replace: true });
    }

    useCalendarHotkeys((action) => {
        if (action === "prev") update({ date: shiftTimelineAnchor(date, scale, -1) });
        else if (action === "next") update({ date: shiftTimelineAnchor(date, scale, 1) });
        else if (action === "today") update({ date: today });
        else update({ scale: action === "year" ? "year" : "quarter" });
    });

    const projects = query.data?.projects ?? [];
    const overdue = projects.reduce((sum, project) => sum + project.overdue_tasks, 0);
    const late = projects.filter((project) => project.due_date !== null && project.due_date < today && project.status !== "COMPLETED" && project.status !== "ARCHIVED").length;
    const chips: PulseChip[] = [
        { id: "projects", label: `${projects.length} ${plural(projects.length, "проект", "проекта", "проектов")}`, title: "Проекты, которые вам видны", tone: "muted" },
        ...(overdue > 0 ? [{ id: "overdue", label: `${overdue} просроченных задач`, title: "Сумма просроченных задач по проектам", tone: "danger" as const }] : []),
        ...(late > 0 ? [{ id: "late", label: `${late} с прошедшим сроком проекта`, title: "Срок проекта прошёл, а проект не завершён", tone: "warn" as const }] : []),
    ];

    return (
        <>
            <div className="flex shrink-0 flex-col gap-2 border-b border-line px-3 py-2 sm:px-4">
                <CalendarToolbar
                    title={timelineTitle(range, scale)}
                    scale={scale}
                    scales={PORTFOLIO_SCALES.map((item) => (item.value === "quarter" ? { ...item, hotkey: "М / M" } : item))}
                    onScaleChange={(value) => update({ scale: value })}
                    onToday={() => update({ date: today })}
                    onPrevious={() => update({ date: shiftTimelineAnchor(date, scale, -1) })}
                    onNext={() => update({ date: shiftTimelineAnchor(date, scale, 1) })}
                />
                <CalendarPulseChips chips={chips} />
            </div>
            <div className="flex min-h-0 flex-1 flex-col p-2 sm:p-3 [@media(max-height:500px)]:min-h-[180px] [@media(max-height:500px)]:shrink-0">
                {query.error && <ErrorMessage message={(query.error as Error).message} />}
                {query.isPending && !query.error && <Skeleton className="h-full min-h-[320px] w-full" />}
                {query.data && (
                    <PortfolioTimeline
                        projects={projects}
                        days={days}
                        range={range}
                        scale={scale}
                        onOpenProject={(project) => navigate(`/projects/${project.key}/calendar?view=timeline&scale=${scale}&date=${date}`)}
                    />
                )}
            </div>
        </>
    );
}
