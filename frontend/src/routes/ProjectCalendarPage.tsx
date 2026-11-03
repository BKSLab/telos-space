import { useEffect, useMemo, useRef, useState } from "react";
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
import {
    useInfiniteQuery,
    useMutation,
    useQuery,
    useQueryClient,
    type InfiniteData,
} from "@tanstack/react-query";
import { AlertTriangle, Beaker, CalendarClock, Diamond, Filter, GitMerge, Inbox, Maximize2, PanelRightClose, PanelRightOpen, X } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { CalendarPulseChips, type PulseChip } from "@/components/calendar/CalendarPulseChips";
import { CalendarRecentChanges } from "@/components/calendar/CalendarRecentChanges";
import { CalendarSidePanel } from "@/components/calendar/CalendarSidePanel";
import { CalendarTask } from "@/components/calendar/CalendarTask";
import { CalendarToolbar, Segmented } from "@/components/calendar/CalendarToolbar";
import { ColorModeSelect } from "@/components/calendar/ColorModeSelect";
import { DeadlineDialog } from "@/components/calendar/DeadlineDialog";
import { DependencyDialog } from "@/components/calendar/DependencyDialog";
import { DependencyPanel } from "@/components/calendar/DependencyPanel";
import { MilestoneDialog } from "@/components/calendar/MilestoneDialog";
import { MilestonePanel } from "@/components/calendar/MilestonePanel";
import { ScenarioPanel } from "@/components/calendar/ScenarioPanel";
import { ScheduleTools } from "@/components/calendar/schedule/ScheduleTools";
import { useGanttSchedule } from "@/components/calendar/schedule/useGanttSchedule";
import { TimelineGrid } from "@/components/calendar/TimelineGrid";
import { TimelineGroupSwitch, WorkloadLegend } from "@/components/calendar/TimelineWorkload";
import { TIMELINE_GROUP_BY, type TimelineGroupBy } from "@/lib/timelineGroups";
import type { ProjectWorkload, WorkloadPerson } from "@/lib/workload";
import { UnscheduledList } from "@/components/calendar/UnscheduledList";
import { exportGanttPdf, exportGanttPng } from "@/components/calendar/gantt/exportGantt";
import { GanttColumnsMenu } from "@/components/calendar/gantt/GanttColumnsMenu";
import { GanttExportMenu, type GanttExportFormat, type GanttExportScope } from "@/components/calendar/gantt/GanttExportMenu";
import { GanttSheet } from "@/components/calendar/gantt/GanttSheet";
import { fetchProjectCalendarRange } from "@/components/calendar/gantt/projectCalendarData";
import { SplitCalendarLayout } from "@/components/calendar/gantt/SplitCalendarLayout";
import { TimelineLegend } from "@/components/calendar/gantt/TimelineLegend";
import { CalendarGridView, type GridEnv } from "@/components/calendar/grid/CalendarGridView";
import { gridGrabDate, shortDate } from "@/components/calendar/grid/gridHelpers";
import { CreateTaskDialog } from "@/components/tasks/CreateTaskDialog";
import { Button, IconButton } from "@/components/ui/Button";
import { Select } from "@/components/ui/Field";
import { Popover } from "@/components/ui/Popover";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import {
    buildTimelineDays,
    dateFromDropId,
    isTimelineDropId,
    localToday,
    moveTaskInterval,
    rescheduleCalendarTask,
    resizeTaskInterval,
    timelineRange,
    timelineTitle,
    type TimelineRange,
    type TimelineScale,
} from "@/lib/calendar";
import {
    GRID_SCALES,
    TIMELINE_SCALES,
    gridRange,
    gridTitle,
    shiftTaskDates,
    type ColorMode,
    type GridScale,
} from "@/lib/calendarGrid";
import {
    clampLabelWidth,
    exportFileName,
    fitTimelineScale,
    ganttColumnsWidth,
    nextTimelineScale,
    normalizeGanttColumns,
    projectSpan,
    rangeDays,
    stretchDayWidth,
    unionRange,
    type GanttColumn,
} from "@/lib/ganttView";
import { api, endpoints, queryKeys } from "@/lib/api";
import { plural } from "@/lib/plural";
import { useToast } from "@/lib/toast";
import type {
    CalendarGridTask,
    CalendarTask as CalendarTaskModel,
    ProjectCalendar,
    ProjectMilestone,
    ProjectMilestoneInput,
    ProjectStage,
    ScenarioApplyResult,
    ScenarioChangeInput,
    ScenarioPreview,
    Task,
    TaskDependency,
    TaskDependencyInput,
    TaskPriority,
    UnscheduledTasksPage,
} from "@/lib/types";
import { PRIORITY_LABELS, PRIORITY_ORDER } from "@/lib/types";
import {
    readStored,
    useCalendarHotkeys,
    useCalendarViewState,
    useHolidays,
    useMediaQuery,
    useStoredChoice,
    useStoredFlag,
    writeStored,
} from "@/lib/useCalendarView";
import { useProjectOutlet } from "@/lib/useProjectOutlet";
import { useUiStore } from "@/stores/ui";

const FILTER_NAMES = ["stage", "priority", "assignee", "wbs"] as const;
const COLOR_MODES: ColorMode[] = ["status", "stage", "assignee"];
type PanelTab = "milestones" | "dependencies" | "changes" | "unscheduled";
/** «Рядом» (сетка + шкала) показывается только на экране от 1280 px. */
const WIDE_QUERY = "(min-width: 1280px)";
const SPLIT_GRID_SCALES = GRID_SCALES.filter((item) => item.value === "week" || item.value === "month");
const LABEL_WIDTH_KEY = "tt.calendar.project.gantt.labelWidth";
const COLUMNS_KEY = "tt.calendar.project.gantt.columns";

/** Сетка — точное попадание указателя в день; шкала — прежнее пересечение полосы с колонкой. */
const gridCollision: CollisionDetection = (args) => {
    const hits = pointerWithin(args);
    return hits.length > 0 ? hits : rectIntersection(args);
};

/** «Рядом»: полоса шкалы ищет день только среди дней шкалы, остальное — как в сетке. */
const splitCollision: CollisionDetection = (args) => {
    if ((args.active.data.current as { dragKind?: string } | undefined)?.dragKind === "interval") {
        return rectIntersection({
            ...args,
            droppableContainers: args.droppableContainers.filter((container) => isTimelineDropId(container.id)),
        });
    }
    return gridCollision(args);
};

interface ExportJob {
    format: GanttExportFormat;
    range: TimelineRange;
    scale: TimelineScale;
    data: ProjectCalendar;
}

interface TaskDatesVariables {
    taskId: number;
    dueDate: string | null;
    startDate?: string | null;
}

export function ProjectCalendarPage() {
    const project = useProjectOutlet();
    const access = { canEdit: true };
    const queryClient = useQueryClient();
    const toast = useToast();
    const setSelectedTaskId = useUiStore((state) => state.setSelectedTaskId);
    const [searchParams, setSearchParams] = useSearchParams();
    const today = localToday();
    const isPhone = useMediaQuery("(max-width: 1023px)");
    const isTouch = useMediaQuery("(pointer: coarse)");
    const isWide = useMediaQuery(WIDE_QUERY);
    const isPresent = searchParams.get("present") === "1";
    const viewState = useCalendarViewState(
        isPhone ? "tt.calendar.project.view.phone" : "tt.calendar.project.view",
        { view: "grid", gridScale: isPhone ? "week" : "month", timelineScale: "month" },
        today,
    );
    // «Рядом» на узком экране не помещается — падаем на сетку, адрес не трогаем.
    const view = viewState.view === "split" && !isWide ? "grid" : viewState.view;
    const isSplit = view === "split";
    const showGrid = view === "grid" || isSplit;
    const showTimeline = view === "timeline" || isSplit;
    const { gridScale, timelineScale, date, fit } = viewState;
    const [labelWidth, setLabelWidth] = useState(() => clampLabelWidth(readStored<number>(LABEL_WIDTH_KEY)));
    const [ganttColumns, setGanttColumns] = useState<GanttColumn[]>(() => normalizeGanttColumns(readStored(COLUMNS_KEY)));
    const [exportJob, setExportJob] = useState<ExportJob | null>(null);
    const [isExporting, setExporting] = useState(false);
    const exportSheetRef = useRef<HTMLDivElement>(null);
    const timelineAreaRef = useRef<HTMLDivElement>(null);
    const [colorMode, setColorMode] = useStoredChoice<ColorMode>("tt.calendar.project.color", COLOR_MODES, "status");
    // Группировка шкалы: ?group=assignee из ссылки главнее сохранённого выбора.
    const [storedGroupBy, setStoredGroupBy] = useStoredChoice<TimelineGroupBy>("tt.calendar.project.group", TIMELINE_GROUP_BY, "wbs");
    const [isPanelOpen, setPanelOpen] = useStoredFlag("tt.calendar.project.panel", false);
    const [panelTab, setPanelTab] = useState<PanelTab>("milestones");
    const [createDate, setCreateDate] = useState<string | null>(null);
    const [activeTask, setActiveTask] = useState<CalendarGridTask | null>(null);
    const [deadlineTask, setDeadlineTask] = useState<CalendarTaskModel | null>(null);
    const [milestoneDialog, setMilestoneDialog] = useState<"new" | ProjectMilestone | null>(null);
    const [isDependencyDialogOpen, setDependencyDialogOpen] = useState(false);
    const [isScenarioMode, setScenarioMode] = useState(false);
    const [scenarioInputs, setScenarioInputs] = useState<ScenarioChangeInput[]>([]);
    const [scenarioPreview, setScenarioPreview] = useState<ScenarioPreview | null>(null);
    const scenarioRevision = useRef(0);
    const stageFilter = searchParams.get("stage") ?? "";
    const priorityFilter = searchParams.get("priority") ?? "";
    const assigneeFilter = searchParams.get("assignee") ?? "";
    const wbsFilter = searchParams.get("wbs") ?? "";
    // Ссылка на веху (?milestone= из агента) открывает панель на вкладке «Вехи».
    const showPanel = isPanelOpen || searchParams.has("milestone");
    const needSpan = isPresent || (fit && showTimeline);
    const milestonesQuery = useQuery({
        queryKey: queryKeys.milestones(project.id),
        queryFn: () => api.get<ProjectMilestone[]>(endpoints.projectMilestones(project.id)),
    });
    const tasksQuery = useQuery({
        queryKey: queryKeys.tasks(project.id),
        queryFn: () => api.get<Task[]>(endpoints.projectTasks(project.id)),
        enabled: showPanel || isDependencyDialogOpen || needSpan,
    });
    const span = useMemo(
        () => projectSpan({ project, tasks: tasksQuery.data ?? [], milestones: milestonesQuery.data ?? [] }),
        [milestonesQuery.data, project, tasksQuery.data],
    );
    const timelineRangeValue = useMemo(
        () => (needSpan && span ? span : timelineRange(date, timelineScale)),
        [date, needSpan, span, timelineScale],
    );
    const range = useMemo(() => {
        if (isPresent || view === "timeline") return timelineRangeValue;
        const gridValue = gridRange(gridScale, date);
        return view === "split" ? unionRange(gridValue, timelineRangeValue) : gridValue;
    }, [date, gridScale, isPresent, timelineRangeValue, view]);
    const { holidays, unknownYears } = useHolidays(range);
    const days = useMemo(() => buildTimelineDays(timelineRangeValue, today, holidays), [holidays, timelineRangeValue, today]);
    const sensors = useSensors(
        useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
        useSensor(KeyboardSensor),
    );

    const commonParams = useMemo(() => {
        const params = new URLSearchParams({ today });
        if (stageFilter) params.set("stage_id", stageFilter);
        if (priorityFilter) params.set("priority", priorityFilter);
        if (assigneeFilter) params.set("assignee", assigneeFilter);
        if (wbsFilter) params.set("wbs_node_id", wbsFilter);
        return params;
    }, [assigneeFilter, priorityFilter, stageFilter, wbsFilter, today]);
    const rangeParams = useMemo(() => {
        const params = new URLSearchParams(commonParams);
        params.set("date_from", range.dateFrom);
        params.set("date_to", range.dateTo);
        return params;
    }, [commonParams, range]);
    const groupParam = searchParams.get("group");
    const groupBy: TimelineGroupBy =
        groupParam !== null && (TIMELINE_GROUP_BY as readonly string[]).includes(groupParam)
            ? (groupParam as TimelineGroupBy)
            : storedGroupBy;
    const calendarKey = queryKeys.calendar(project.id, rangeParams.toString());
    const unscheduledKey = queryKeys.calendarUnscheduled(project.id, commonParams.toString());

    const calendarQuery = useQuery({
        queryKey: calendarKey,
        // Весь срок проекта может быть длиннее года — запрос режется на куски (projectCalendarData).
        queryFn: () => fetchProjectCalendarRange(project.id, commonParams, range),
        placeholderData: (previous) => previous,
        enabled: !needSpan || !tasksQuery.isPending,
    });
    const unscheduledQuery = useInfiniteQuery<UnscheduledTasksPage>({
        queryKey: unscheduledKey,
        initialPageParam: null,
        queryFn: ({ pageParam }) => {
            const params = new URLSearchParams(commonParams);
            params.set("limit", "50");
            if (pageParam !== null) params.set("cursor", String(pageParam));
            return api.get<UnscheduledTasksPage>(
                `${endpoints.projectCalendarUnscheduled(project.id)}?${params.toString()}`,
            );
        },
        getNextPageParam: (page) => page.next_cursor ?? undefined,
    });
    // Загрузка людей нужна только шкале с группировкой по исполнителям. Ключ под
    // ["projects", id, "calendar"] — сбрасывается вместе с календарём после переноса дат.
    const workloadQuery = useQuery({
        queryKey: [...queryKeys.calendar(project.id, "workload"), timelineRangeValue.dateFrom, timelineRangeValue.dateTo],
        queryFn: () =>
            api.get<ProjectWorkload>(
                `${endpoints.projectCalendarWorkload(project.id)}?date_from=${timelineRangeValue.dateFrom}&date_to=${timelineRangeValue.dateTo}`,
            ),
        enabled: showTimeline && groupBy === "assignee",
        placeholderData: (previous) => previous,
    });
    const workloadByName = useMemo(
        () => new Map<string, WorkloadPerson>((workloadQuery.data?.people ?? []).map((person) => [person.name, person])),
        [workloadQuery.data],
    );
    const stagesQuery = useQuery({
        queryKey: queryKeys.stages(project.id),
        queryFn: () => api.get<ProjectStage[]>(endpoints.projectStages(project.id)),
        enabled: createDate !== null,
    });

    const deadlineMutation = useMutation({
        mutationFn: ({ taskId, dueDate, startDate }: TaskDatesVariables) =>
            api.patch<Task>(endpoints.task(taskId), {
                due_date: dueDate,
                ...(startDate !== undefined && { start_date: startDate }),
            }),
        onMutate: async ({ taskId, dueDate, startDate }: TaskDatesVariables) => {
            await Promise.all([
                queryClient.cancelQueries({ queryKey: calendarKey }),
                queryClient.cancelQueries({ queryKey: unscheduledKey }),
            ]);
            const previousCalendar = queryClient.getQueryData<ProjectCalendar>(calendarKey);
            const previousUnscheduled =
                queryClient.getQueryData<InfiniteData<UnscheduledTasksPage>>(unscheduledKey);
            const sourceTask =
                previousCalendar?.tasks.find((task) => task.id === taskId) ??
                previousUnscheduled?.pages
                    .flatMap((page) => page.items)
                    .find((task) => task.id === taskId);
            if (sourceTask) {
                const changed = rescheduleCalendarTask(
                    sourceTask,
                    dueDate,
                    today,
                    startDate === undefined ? sourceTask.start_date : startDate,
                );
                queryClient.setQueryData<ProjectCalendar>(calendarKey, (old) => {
                    if (!old) return old;
                    const intervalStart = changed.start_date ?? changed.due_date;
                    const intervalEnd = changed.due_date ?? changed.start_date;
                    const willBeVisible =
                        intervalStart !== null &&
                        intervalEnd !== null &&
                        intervalEnd >= old.range.date_from &&
                        intervalStart <= old.range.date_to;
                    const wasUnscheduled =
                        sourceTask.start_date === null && sourceTask.due_date === null;
                    const isUnscheduled =
                        changed.start_date === null && changed.due_date === null;
                    const tasks = old.tasks.filter((task) => task.id !== taskId);
                    if (willBeVisible) tasks.push(changed);
                    return {
                        ...old,
                        tasks,
                        summary: {
                            ...old.summary,
                            overdue:
                                old.summary.overdue -
                                Number(sourceTask.is_overdue) +
                                Number(changed.is_overdue),
                            due_soon:
                                old.summary.due_soon -
                                Number(sourceTask.is_due_soon) +
                                Number(changed.is_due_soon),
                            unscheduled:
                                old.summary.unscheduled +
                                Number(isUnscheduled) -
                                Number(wasUnscheduled),
                            drifted:
                                old.summary.drifted -
                                Number(
                                    sourceTask.drift_days !== null &&
                                        sourceTask.drift_days !== 0,
                                ) +
                                Number(changed.drift_days !== null && changed.drift_days !== 0),
                        },
                    };
                });
                queryClient.setQueryData<InfiniteData<UnscheduledTasksPage>>(
                    unscheduledKey,
                    (old) => {
                        if (!old) return old;
                        const pages = old.pages.map((page) => ({
                            ...page,
                            items: page.items.filter((task) => task.id !== taskId),
                        }));
                        if (
                            changed.start_date === null &&
                            changed.due_date === null &&
                            pages[0]
                        ) {
                            pages[0] = { ...pages[0], items: [changed, ...pages[0].items] };
                        }
                        return { ...old, pages };
                    },
                );
            }
            return { previousCalendar, previousUnscheduled };
        },
        onError: (error, _variables, context) => {
            if (context?.previousCalendar) {
                queryClient.setQueryData(calendarKey, context.previousCalendar);
            }
            if (context?.previousUnscheduled) {
                queryClient.setQueryData(unscheduledKey, context.previousUnscheduled);
            }
            toast.error(`Не удалось изменить даты: ${(error as Error).message}`);
        },
        onSuccess: () => toast.success("Плановые даты задачи обновлены."),
        onSettled: (_data, _error, variables) => {
            queryClient.invalidateQueries({ queryKey: ["projects", project.id] });
            queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
            queryClient.invalidateQueries({ queryKey: queryKeys.task(variables.taskId) });
            queryClient.invalidateQueries({ queryKey: queryKeys.taskActivity(variables.taskId) });
        },
    });
    const baselineMutation = useMutation({
        mutationFn: (taskId: number) => api.post<Task>(endpoints.taskBaseline(taskId)),
        onSuccess: (_task, taskId) => {
            toast.success("Базовый план задачи зафиксирован.");
            queryClient.invalidateQueries({ queryKey: ["projects", project.id] });
            queryClient.invalidateQueries({ queryKey: queryKeys.task(taskId) });
            queryClient.invalidateQueries({ queryKey: queryKeys.taskActivity(taskId) });
        },
        onError: (error) =>
            toast.error(`Не удалось зафиксировать baseline: ${(error as Error).message}`),
    });
    const refreshMilestones = () => {
        queryClient.invalidateQueries({ queryKey: queryKeys.milestones(project.id) });
        queryClient.invalidateQueries({
            queryKey: ["projects", project.id, "calendar"],
        });
    };
    const createMilestoneMutation = useMutation({
        mutationFn: (data: ProjectMilestoneInput) =>
            api.post<ProjectMilestone>(endpoints.projectMilestones(project.id), data),
        onSuccess: () => {
            setMilestoneDialog(null);
            refreshMilestones();
            toast.success("Веха создана.");
        },
        onError: (error) => toast.error(`Не удалось создать веху: ${(error as Error).message}`),
    });
    const updateMilestoneMutation = useMutation({
        mutationFn: ({ milestoneId, data }: { milestoneId: number; data: ProjectMilestoneInput }) =>
            api.patch<ProjectMilestone>(
                endpoints.projectMilestone(project.id, milestoneId),
                data,
            ),
        onSuccess: () => {
            setMilestoneDialog(null);
            refreshMilestones();
            toast.success("Веха обновлена.");
        },
        onError: (error) => toast.error(`Не удалось обновить веху: ${(error as Error).message}`),
    });
    const deleteMilestoneMutation = useMutation({
        mutationFn: (milestoneId: number) =>
            api.delete<void>(endpoints.projectMilestone(project.id, milestoneId)),
        onSuccess: () => {
            refreshMilestones();
            toast.success("Веха удалена.");
        },
        onError: (error) => toast.error(`Не удалось удалить веху: ${(error as Error).message}`),
    });
    const isMilestoneSaving =
        createMilestoneMutation.isPending ||
        updateMilestoneMutation.isPending ||
        deleteMilestoneMutation.isPending;
    const createDependencyMutation = useMutation({
        mutationFn: (data: TaskDependencyInput) =>
            api.post<TaskDependency>(endpoints.projectTaskDependencies(project.id), data),
        onSuccess: () => {
            setDependencyDialogOpen(false);
            queryClient.invalidateQueries({
                queryKey: ["projects", project.id, "calendar"],
            });
            toast.success("Зависимость создана.");
        },
        onError: (error) =>
            toast.error(`Не удалось создать зависимость: ${(error as Error).message}`),
    });
    const deleteDependencyMutation = useMutation({
        mutationFn: (dependencyId: number) =>
            api.delete<void>(endpoints.projectTaskDependency(project.id, dependencyId)),
        onSuccess: () => {
            queryClient.invalidateQueries({
                queryKey: ["projects", project.id, "calendar"],
            });
            toast.success("Зависимость удалена.");
        },
        onError: (error) =>
            toast.error(`Не удалось удалить зависимость: ${(error as Error).message}`),
    });
    const isDependencySaving =
        createDependencyMutation.isPending || deleteDependencyMutation.isPending;
    const schedule = useGanttSchedule({
        projectId: project.id,
        dependencies: calendarQuery.data?.dependencies ?? [],
        calendarTasks: calendarQuery.data?.tasks ?? [],
        projectTasks: tasksQuery.data,
        createDependency: (data) => createDependencyMutation.mutate(data),
    });
    const previewScenarioMutation = useMutation({
        mutationFn: ({ changes }: { revision: number; changes: ScenarioChangeInput[] }) =>
            api.post<ScenarioPreview>(endpoints.projectCalendarScenarioPreview(project.id), {
                changes,
            }),
        onSuccess: (preview, variables) => {
            if (variables.revision === scenarioRevision.current) {
                setScenarioPreview(preview);
            }
        },
        onError: (error, variables) => {
            if (variables.revision === scenarioRevision.current) {
                setScenarioPreview(null);
                toast.error(`Не удалось рассчитать сценарий: ${(error as Error).message}`);
            }
        },
    });
    const applyScenarioMutation = useMutation({
        mutationFn: (preview: ScenarioPreview) =>
            api.post<ScenarioApplyResult>(endpoints.projectCalendarScenarioApply(project.id), {
                changes: preview.changes.map((change) => ({
                    task_id: change.task_id,
                    start_date: change.proposed.start_date,
                    due_date: change.proposed.due_date,
                    expected_updated_at: change.expected_updated_at,
                })),
            }),
        onSuccess: (result) => {
            scenarioRevision.current += 1;
            setScenarioMode(false);
            setScenarioInputs([]);
            setScenarioPreview(null);
            queryClient.invalidateQueries({ queryKey: ["projects", project.id] });
            queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
            for (const taskId of result.task_ids) {
                queryClient.invalidateQueries({ queryKey: queryKeys.task(taskId) });
                queryClient.invalidateQueries({ queryKey: queryKeys.taskActivity(taskId) });
            }
            toast.success(`Сценарий применён: ${result.applied_count} задач.`);
        },
        onError: (error) =>
            toast.error(`Не удалось применить сценарий: ${(error as Error).message}`),
    });

    const stagesById = useMemo(
        () => new Map((calendarQuery.data?.stages ?? []).map((stage) => [stage.id, stage])),
        [calendarQuery.data?.stages],
    );
    const unscheduledTasks = useMemo(
        () => unscheduledQuery.data?.pages.flatMap((page) => page.items) ?? [],
        [unscheduledQuery.data?.pages],
    );

    function setViewParam(name: string, value: string) {
        const next = new URLSearchParams(searchParams);
        if (value) next.set(name, value);
        else next.delete(name);
        setSearchParams(next, { replace: true });
    }

    const movePeriod = viewState.shift;

    function resetFilters() {
        const next = new URLSearchParams(searchParams);
        for (const name of FILTER_NAMES) next.delete(name);
        setSearchParams(next, { replace: true });
    }

    function togglePanel(open: boolean, tab?: PanelTab) {
        if (tab) setPanelTab(tab);
        setPanelOpen(open);
        if (!open && searchParams.has("milestone")) setViewParam("milestone", "");
    }

    function changeView(nextView: "grid" | "timeline" | "split") {
        if (nextView === "grid" && isScenarioMode) stopScenario();
        viewState.setView(nextView);
    }

    /** Переход в сетку (клик по дню шкалы, клавиша «Д») гасит «Что, если»: сценарий живёт только на шкале. */
    function openGrid(scale: GridScale, value?: string) {
        // «Рядом»: день не открывается отдельно — общая дата переходит на него, шкала прокручивается.
        if (isSplit && scale === "day" && value) {
            viewState.focusDate(value);
            return;
        }
        if (isScenarioMode && !(isSplit && (scale === "week" || scale === "month"))) stopScenario();
        viewState.setGridScale(scale, value);
    }

    function changeLabelWidth(width: number) {
        const value = clampLabelWidth(width);
        setLabelWidth(value);
        writeStored(LABEL_WIDTH_KEY, value);
    }

    function changeGanttColumns(columns: GanttColumn[]) {
        setGanttColumns(columns);
        writeStored(COLUMNS_KEY, columns);
    }

    async function loadProjectSpan(): Promise<TimelineRange | null> {
        const tasks =
            tasksQuery.data ??
            (await queryClient.fetchQuery<Task[]>({
                queryKey: queryKeys.tasks(project.id),
                queryFn: () => api.get<Task[]>(endpoints.projectTasks(project.id)),
            }));
        return projectSpan({ project, tasks, milestones: milestonesQuery.data ?? [] });
    }

    /** «Уместить проект»: весь срок в самом крупном масштабе, который влезает в ширину шкалы. */
    async function toggleFit() {
        if (fit) {
            viewState.setFit(false);
            return;
        }
        try {
            const projectRange = await loadProjectSpan();
            if (!projectRange) {
                toast.error("У проекта и его задач нет дат — уместить нечего.");
                return;
            }
            const available = (timelineAreaRef.current?.clientWidth ?? window.innerWidth) - labelWidth - ganttColumnsWidth(ganttColumns) - 24;
            viewState.setFit(true, fitTimelineScale(rangeDays(projectRange), available));
        } catch (fitError) {
            toast.error(`Не удалось получить задачи проекта: ${(fitError as Error).message}`);
        }
    }

    function zoomTimeline(direction: number) {
        const next = nextTimelineScale(timelineScale, direction);
        if (next !== timelineScale) viewState.setTimelineScale(next);
    }

    function setPresent(on: boolean) {
        const next = new URLSearchParams(window.location.search);
        if (on) next.set("present", "1");
        else next.delete("present");
        setSearchParams(next, { replace: true });
    }

    /** Выгрузка: лист рисуется вне экрана в натуральную ширину, снимается и сразу убирается. */
    async function startExport(format: GanttExportFormat, scope: GanttExportScope) {
        setExporting(true);
        try {
            let exportRange: TimelineRange | null = timelineRangeValue;
            let scale = timelineScale;
            if (scope === "project") {
                exportRange = await loadProjectSpan();
                if (!exportRange) {
                    toast.error("У проекта и его задач нет дат — выгружать нечего.");
                    setExporting(false);
                    return;
                }
                // Картинка до ~3200 px по ширине шкалы: читаемо и на экране, и на листе.
                scale = fitTimelineScale(rangeDays(exportRange), 3200);
            }
            const rangeForExport = exportRange;
            const params = new URLSearchParams(commonParams);
            params.set("date_from", rangeForExport.dateFrom);
            params.set("date_to", rangeForExport.dateTo);
            const data = await queryClient.fetchQuery({
                queryKey: queryKeys.calendar(project.id, params.toString()),
                queryFn: () => fetchProjectCalendarRange(project.id, commonParams, rangeForExport),
            });
            setExportJob({ format, range: rangeForExport, scale, data });
        } catch (exportError) {
            toast.error(`Не удалось выгрузить Гант: ${(exportError as Error).message}`);
            setExporting(false);
        }
    }

    useEffect(() => {
        if (!exportJob) return;
        let cancelled = false;
        // Два кадра: лист смонтирован и разложен, шрифты применены.
        const frame = window.requestAnimationFrame(() =>
            window.requestAnimationFrame(async () => {
                const node = exportSheetRef.current;
                try {
                    if (!node) throw new Error("лист не отрисовался");
                    await document.fonts?.ready;
                    const fileName = exportFileName(project.key, today, exportJob.format);
                    if (exportJob.format === "png") await exportGanttPng(node, fileName);
                    else await exportGanttPdf(node, fileName);
                    if (!cancelled) toast.success(`Гант выгружен: ${fileName}`);
                } catch (exportError) {
                    if (!cancelled) toast.error(`Не удалось выгрузить Гант: ${(exportError as Error).message}`);
                } finally {
                    if (!cancelled) {
                        setExportJob(null);
                        setExporting(false);
                    }
                }
            }),
        );
        return () => {
            cancelled = true;
            window.cancelAnimationFrame(frame);
        };
        // Снимок запускается один раз на задание выгрузки.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [exportJob]);

    useEffect(() => {
        if (!isPresent) return;
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape") setPresent(false);
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
        // setPresent читает актуальный адрес сам.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isPresent]);

    useCalendarHotkeys((action) => {
        if (action === "prev") movePeriod(-1);
        else if (action === "next") movePeriod(1);
        else if (action === "today") viewState.setDate(today);
        else if (view === "timeline" && action !== "day") viewState.setTimelineScale(action);
        else if (isSplit && action === "year") viewState.setTimelineScale("year");
        else openGrid(action);
    }, !isPresent);

    function taskWithScenario<T extends CalendarGridTask>(task: T): T {
        const change = scenarioPreview?.changes.find((item) => item.task_id === task.id);
        return change
            ? {
                  ...task,
                  start_date: change.proposed.start_date,
                  due_date: change.proposed.due_date,
              }
            : task;
    }

    function stopScenario() {
        scenarioRevision.current += 1;
        setScenarioMode(false);
        setScenarioInputs([]);
        setScenarioPreview(null);
        setDeadlineTask(null);
    }

    function requestScenarioPreview(changes: ScenarioChangeInput[]) {
        if (changes.length === 0) {
            scenarioRevision.current += 1;
            setScenarioPreview(null);
            return;
        }
        const revision = scenarioRevision.current + 1;
        scenarioRevision.current = revision;
        previewScenarioMutation.mutate({ revision, changes });
    }

    function changeDates(task: CalendarGridTask, dates: Omit<TaskDatesVariables, "taskId">) {
        const startDate = dates.startDate === undefined ? task.start_date : dates.startDate;
        if (task.due_date === dates.dueDate && task.start_date === startDate) {
            return;
        }
        if (isScenarioMode) {
            const original =
                calendarQuery.data?.tasks.find((item) => item.id === task.id) ??
                unscheduledTasks.find((item) => item.id === task.id);
            const remaining = scenarioInputs.filter((item) => item.task_id !== task.id);
            const next =
                original?.start_date === startDate && original.due_date === dates.dueDate
                    ? remaining
                    : [...remaining, { task_id: task.id, start_date: startDate, due_date: dates.dueDate }];
            setScenarioInputs(next);
            requestScenarioPreview(next);
            if (isPhone) togglePanel(true);
            return;
        }
        const save = () => deadlineMutation.mutate({ taskId: task.id, ...dates });
        // Шкала: перенос с конфликтом у зависящих задач — спросить «сдвинуть и N следующих?».
        if (view === "timeline" && schedule.proposeShift(task, startDate, dates.dueDate, save)) return;
        save();
    }

    function handleDragStart(event: DragStartEvent) {
        setActiveTask((event.active.data.current?.task as CalendarGridTask | undefined) ?? null);
    }

    function handleDragEnd(event: DragEndEvent) {
        const data = event.active.data.current as
            | { task?: CalendarGridTask; dragKind?: string; date?: string }
            | undefined;
        const sourceTask = data?.task ?? null;
        const targetDate = event.over ? dateFromDropId(event.over.id) : null;
        setActiveTask(null);
        if (!sourceTask || !targetDate) return;
        const task = taskWithScenario(sourceTask);
        if (data?.dragKind === "grid" && data.date) {
            const shifted = shiftTaskDates(task, gridGrabDate(event.active.id, data.date), targetDate);
            if (shifted) changeDates(task, shifted);
        } else if (data?.dragKind === "interval") {
            const moved = moveTaskInterval(task, targetDate);
            changeDates(task, { startDate: moved.startDate, dueDate: moved.dueDate });
        } else if (data?.dragKind === "unscheduled") {
            // В сетке задача без дат получает срок; на шкале — однодневный интервал, как раньше.
            const onTimeline = event.over !== null && isTimelineDropId(event.over.id);
            changeDates(task, onTimeline ? { startDate: targetDate, dueDate: targetDate } : { startDate: null, dueDate: targetDate });
        } else {
            changeDates(task, { dueDate: targetDate });
        }
    }

    function resizeTask(task: CalendarTaskModel, edge: "start" | "end", deltaDays: number) {
        const effectiveTask = taskWithScenario(task);
        const resized = resizeTaskInterval(effectiveTask, edge, deltaDays);
        if (resized) {
            changeDates(effectiveTask, { startDate: resized.startDate, dueDate: resized.dueDate });
        }
    }

    const error = calendarQuery.error ?? unscheduledQuery.error;
    const hasFilters = FILTER_NAMES.some((name) => searchParams.has(name));
    const activeFilterCount = FILTER_NAMES.filter((name) => searchParams.has(name)).length;
    const calendarTasks = useMemo(() => calendarQuery.data?.tasks ?? [], [calendarQuery.data?.tasks]);
    const summary = calendarQuery.data?.summary;
    const gridTasks = useMemo(
        () => (scenarioPreview ? calendarTasks.map((task) => taskWithScenario(task)) : calendarTasks),
        // taskWithScenario читает только scenarioPreview.
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [calendarTasks, scenarioPreview],
    );
    const nextMilestone = (milestonesQuery.data ?? [])
        .filter((milestone) => milestone.status === "PLANNED" && milestone.due_date >= today)
        .sort((first, second) => first.due_date.localeCompare(second.due_date))[0];
    const dueInPeriod = calendarTasks.filter(
        (task) => task.due_date !== null && task.due_date >= range.dateFrom && task.due_date <= range.dateTo,
    ).length;

    const chips: PulseChip[] = [
        {
            id: "period",
            label: `${dueInPeriod} ${plural(dueInPeriod, "срок", "срока", "сроков")} в периоде`,
            title: "Задач со сроком в показанном периоде (с учётом фильтров)",
            tone: "muted",
            icon: <CalendarClock size={12} aria-hidden="true" />,
        },
        ...(summary && summary.overdue > 0
            ? [{ id: "overdue", label: `${summary.overdue} просрочено`, title: "Незавершённые задачи с прошедшим сроком", tone: "danger" as const, icon: <AlertTriangle size={12} aria-hidden="true" /> }]
            : []),
        ...(summary && summary.due_soon > 0
            ? [{ id: "soon", label: `${summary.due_soon} на 7 дней`, title: "Срок в ближайшие 7 дней", tone: "warn" as const }]
            : []),
        ...(summary && summary.unscheduled > 0
            ? [{ id: "unscheduled", label: `${summary.unscheduled} без срока`, title: "Открыть список задач без срока — их можно перетащить на день", icon: <Inbox size={12} aria-hidden="true" />, onClick: () => togglePanel(true, "unscheduled") }]
            : []),
        ...(summary && summary.drifted > 0
            ? [{ id: "drift", label: `${summary.drifted} с отклонением от плана`, title: "Сроки разошлись с зафиксированным базовым планом (baseline)", tone: "info" as const }]
            : []),
        ...(summary && summary.dependency_risks > 0
            ? [{ id: "deps", label: `${summary.dependency_risks} риск. зависимостей`, title: "Задача начинается раньше, чем закончится та, от которой она зависит", tone: "danger" as const, icon: <GitMerge size={12} aria-hidden="true" />, onClick: () => togglePanel(true, "dependencies") }]
            : []),
        ...(nextMilestone
            ? [{
                  id: "milestone",
                  label: `ближайшая веха ${shortDate(nextMilestone.due_date)}`,
                  title: `«${nextMilestone.title}» — показать этот день`,
                  tone: "info" as const,
                  icon: <Diamond size={11} aria-hidden="true" />,
                  onClick: () => viewState.setDate(nextMilestone.due_date),
              }]
            : []),
    ];

    const gridEnv: GridEnv = {
        today,
        holidays,
        colorMode,
        stagesById,
        canDrag: () => access.canEdit && !isPhone && !isTouch,
        showSource: false,
        onOpenTask: setSelectedTaskId,
        onScheduleTask: access.canEdit ? (task) => setDeadlineTask(taskWithScenario(task as CalendarTaskModel)) : undefined,
        onCreate: access.canEdit ? setCreateDate : undefined,
        onOpenPeriod: (scale, value) => openGrid(scale, value),
    };

    const title =
        view === "timeline"
            ? fit && needSpan && span
                ? `Весь срок проекта · ${shortDate(span.dateFrom)} — ${shortDate(span.dateTo)}`
                : timelineTitle(range, timelineScale)
            : gridTitle(gridScale, date);
    const isLoading = calendarQuery.isPending || unscheduledQuery.isPending;
    const timelineTasks = useMemo(
        () =>
            isSplit
                ? calendarTasks.filter((task) => {
                      const from = task.start_date ?? task.due_date;
                      const to = task.due_date ?? task.start_date;
                      return from !== null && to !== null && to >= timelineRangeValue.dateFrom && from <= timelineRangeValue.dateTo;
                  })
                : calendarTasks,
        [calendarTasks, isSplit, timelineRangeValue],
    );

    // «Рядом» грузит период сетки и шкалы вместе — вехи вне шкалы на ней не рисуем.
    const timelineMilestones = (calendarQuery.data?.milestones ?? []).filter(
        (milestone) => milestone.due_date >= timelineRangeValue.dateFrom && milestone.due_date <= timelineRangeValue.dateTo,
    );

    if (isPresent) {
        const sheet = stretchDayWidth(rangeDays(timelineRangeValue), window.innerWidth - labelWidth - ganttColumnsWidth(ganttColumns) - 64);
        return (
            <div className="fixed inset-0 z-[70] flex flex-col bg-surface" data-gantt-present="" aria-label="Режим показа Ганта">
                <IconButton
                    label="Закрыть режим показа"
                    title="Закрыть режим показа — Esc"
                    size="sm"
                    variant="ghost"
                    className="absolute top-3 right-3 z-10 opacity-60 hover:opacity-100"
                    onClick={() => setPresent(false)}
                >
                    <X size={14} aria-hidden="true" />
                </IconButton>
                <div className="scrollbar-thin min-h-0 flex-1 overflow-auto">
                    {error && <ErrorMessage message={(error as Error).message} />}
                    {calendarQuery.data && !tasksQuery.isPending ? (
                        <GanttSheet
                            projectName={project.name}
                            range={timelineRangeValue}
                            scale={sheet.scale}
                            dayWidth={sheet.dayWidth}
                            data={calendarQuery.data}
                            colorMode={colorMode}
                            columns={ganttColumns}
                            groupBy={groupBy}
                            labelWidth={labelWidth}
                            today={today}
                        />
                    ) : (
                        <div role="status" aria-label="Загрузка Ганта" className="h-full p-6">
                            <Skeleton className="h-full w-full" />
                        </div>
                    )}
                </div>
            </div>
        );
    }

    const gridContent = calendarQuery.data && (
        <>
            {gridTasks.length === 0 && gridScale !== "day" && (
                <p className="px-3 pt-2 text-[12px] text-muted">
                    В этом периоде задач со сроком нет.{" "}
                    {access.canEdit
                        ? "Кликните по дню, чтобы создать задачу, или перетащите задачу из панели «Без срока»."
                        : "Листайте период стрелками ← → или откройте год."}
                </p>
            )}
            <CalendarGridView
                scale={gridScale}
                date={date}
                tasks={gridTasks}
                milestones={calendarQuery.data.milestones}
                env={gridEnv}
                isPhone={isPhone}
                onSwipe={movePeriod}
            />
        </>
    );
    const timelineContent = calendarQuery.data && (
        <div ref={timelineAreaRef} className="flex min-h-0 flex-1 flex-col" data-timeline-area="">
            {groupBy === "assignee" && <WorkloadLegend truncated={workloadQuery.data?.truncated} />}
            <div className="min-h-0 flex-1">
                <TimelineGrid
                    wbsNodes={calendarQuery.data?.wbs_nodes ?? []}
                    groupBy={groupBy}
                    workload={groupBy === "assignee" ? workloadByName : undefined}
                    days={days}
                    range={timelineRangeValue}
                    scale={timelineScale}
                    tasks={timelineTasks}
                    stagesById={stagesById}
                    milestones={timelineMilestones}
                    dependencies={calendarQuery.data.dependencies}
                    scenarioChanges={scenarioPreview?.changes ?? []}
                    selectedDate={date}
                    onSelectDate={(value, scale) => openGrid(scale, value)}
                    onOpenTask={setSelectedTaskId}
                    onScheduleTask={(task) => setDeadlineTask(taskWithScenario(task))}
                    onResizeTask={resizeTask}
                    colorMode={colorMode}
                    labelWidth={labelWidth}
                    onLabelWidthChange={changeLabelWidth}
                    columns={ganttColumns}
                    focusDate={isSplit ? date : undefined}
                    onZoom={zoomTimeline}
                    criticalMode={schedule.criticalMode}
                    onLinkTasks={access.canEdit ? schedule.linkTasks : undefined}
                />
            </div>
            <TimelineLegend mode={colorMode} tasks={timelineTasks} stagesById={stagesById} />
        </div>
    );

    return (
        <div className="instrument-surface flex h-full min-h-0 flex-col [@media(max-height:500px)]:overflow-y-auto [@media(max-height:500px)]:overscroll-contain">
            <div className="flex shrink-0 flex-col gap-2 border-b border-line px-3 py-2 sm:px-4">
                <CalendarToolbar
                    title={title}
                    view={view}
                    activeFilterCount={activeFilterCount}
                    onViewChange={changeView}
                    allowSplit={isWide}
                    scale={view === "timeline" ? timelineScale : gridScale}
                    scales={view === "timeline" ? TIMELINE_SCALES : isSplit ? SPLIT_GRID_SCALES : GRID_SCALES}
                    onScaleChange={(value) =>
                        view === "timeline"
                            ? viewState.setTimelineScale(value as TimelineScale)
                            : viewState.setGridScale(value as GridScale)
                    }
                    extraScale={
                        isSplit ? (
                            <span className="flex items-center gap-1 text-[11px] text-muted" title="Масштаб шкалы (Ганта) внизу; Ctrl+колесо над шкалой делает то же">
                                Шкала
                                <Segmented
                                    label="Масштаб шкалы"
                                    value={timelineScale}
                                    onChange={viewState.setTimelineScale}
                                    options={TIMELINE_SCALES.map((item) => ({ value: item.value, label: item.label, title: `Шкала: ${item.label.toLowerCase()}` }))}
                                />
                            </span>
                        ) : undefined
                    }
                    onToday={() => viewState.setDate(today)}
                    onPrevious={() => movePeriod(-1)}
                    onNext={() => movePeriod(1)}
                >
                    {showTimeline && (
                        <Button
                            size="sm"
                            variant={isScenarioMode ? "secondary" : "ghost"}
                            icon={<Beaker size={13} aria-hidden="true" />}
                            className={isScenarioMode ? "border-ai-border bg-ai-soft text-ai-blue" : undefined}
                            title="Режим «Что, если»: двигайте задачи — увидите последствия по зависимостям, ничего не сохраняя до «Применить»"
                            onClick={() => {
                                if (isScenarioMode) stopScenario();
                                else {
                                    setScenarioMode(true);
                                    togglePanel(true);
                                }
                            }}
                        >
                            {isScenarioMode ? "Сценарий активен" : "Что, если"}
                        </Button>
                    )}
                    {showTimeline && (
                        <Button
                            size="sm"
                            variant={fit ? "secondary" : "ghost"}
                            icon={<Maximize2 size={13} aria-hidden="true" />}
                            aria-pressed={fit}
                            title={
                                fit
                                    ? "Вернуться к периоду вокруг выбранной даты"
                                    : "Уместить проект: весь срок на шкале в подходящем масштабе. Масштаб меняет и Ctrl+колесо над шкалой"
                            }
                            onClick={() => void toggleFit()}
                        >
                            Уместить проект
                        </Button>
                    )}
                    {showTimeline && <GanttColumnsMenu value={ganttColumns} onChange={changeGanttColumns} />}
                    <ColorModeSelect value={colorMode} modes={COLOR_MODES} onChange={setColorMode} />
                    {showTimeline && (
                        <ScheduleTools
                            criticalMode={schedule.criticalMode}
                            onCriticalModeChange={schedule.setCriticalMode}
                            onFixPlan={access.canEdit && !isScenarioMode ? schedule.fixProjectPlan : undefined}
                            isFixingPlan={schedule.isFixingPlan}
                        />
                    )}
                    {showTimeline && (
                        <TimelineGroupSwitch
                            value={groupBy}
                            onChange={(value) => {
                                setStoredGroupBy(value);
                                setViewParam("group", value === "wbs" ? "" : value);
                            }}
                        />
                    )}
                    <Popover
                        label="Фильтры календаря"
                        trigger={
                            <>
                                <Filter size={13} aria-hidden="true" />
                                <span>Фильтры</span>
                                {activeFilterCount > 0 && (
                                    <span className="font-mono text-[10px] text-accent">· {activeFilterCount}</span>
                                )}
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
                            <Select aria-label="Фильтр календаря по стадии" value={stageFilter} onChange={(event) => setViewParam("stage", event.target.value)}>
                                <option value="">Все стадии</option>
                                {calendarQuery.data?.stages.map((stage) => (
                                    <option key={stage.id} value={stage.id}>
                                        {stage.name}
                                    </option>
                                ))}
                            </Select>
                            <Select aria-label="Фильтр календаря по приоритету" value={priorityFilter} onChange={(event) => setViewParam("priority", event.target.value)}>
                                <option value="">Все приоритеты</option>
                                {PRIORITY_ORDER.map((priority: TaskPriority) => (
                                    <option key={priority} value={priority}>
                                        {PRIORITY_LABELS[priority]}
                                    </option>
                                ))}
                            </Select>
                            <Select aria-label="Фильтр календаря по исполнителю" value={assigneeFilter} onChange={(event) => setViewParam("assignee", event.target.value)}>
                                <option value="">Все исполнители</option>
                                {calendarQuery.data?.assignees.map((assignee) => (
                                    <option key={assignee} value={assignee}>
                                        {assignee}
                                    </option>
                                ))}
                            </Select>
                            <Select aria-label="Фильтр календаря по ИСР" value={wbsFilter} onChange={event => setViewParam("wbs", event.target.value)}><option value="">Все разделы ИСР</option>{calendarQuery.data?.wbs_nodes.map(node => <option key={node.id} value={node.id}>{node.title}</option>)}</Select>
                        </div>
                    </Popover>
                    <GanttExportMenu busy={isExporting} onExport={(format, scope) => void startExport(format, scope)} onPresent={() => setPresent(true)} />
                    <IconButton
                        size="sm"
                        variant={showPanel ? "secondary" : "ghost"}
                        label={showPanel ? "Скрыть панель" : "Показать панель"}
                        title={showPanel ? "Скрыть панель вех, зависимостей, изменений и задач без срока" : "Панель: вехи, зависимости, недавние переносы и задачи без срока"}
                        aria-pressed={showPanel}
                        onClick={() => togglePanel(!showPanel)}
                    >
                        {showPanel ? <PanelRightClose size={14} aria-hidden="true" /> : <PanelRightOpen size={14} aria-hidden="true" />}
                    </IconButton>
                </CalendarToolbar>
                <CalendarPulseChips chips={chips} />
            </div>

            <DndContext
                sensors={access.canEdit ? sensors : []}
                collisionDetection={view === "grid" ? gridCollision : isSplit ? splitCollision : rectIntersection}
                onDragStart={handleDragStart}
                onDragEnd={handleDragEnd}
                onDragCancel={() => setActiveTask(null)}
            >
                <div className="relative flex min-h-0 flex-1 [@media(max-height:500px)]:min-h-[180px] [@media(max-height:500px)]:shrink-0">
                    <div
                        className={view === "grid" ? "scrollbar-thin min-w-0 flex-1 overflow-auto" : "flex min-w-0 flex-1 flex-col p-2 sm:p-3"}
                        data-calendar-body={view}
                    >
                        {error && <ErrorMessage message={(error as Error).message} />}
                        {unknownYears.length > 0 && showGrid && gridScale !== "day" && (
                            <p className="px-3 pt-2 text-[11px] text-muted" title="Праздники берутся из производственного календаря сервиса">
                                Праздники на {unknownYears.join(", ")} год не загружены — отмечены только суббота и воскресенье.
                            </p>
                        )}
                        {isLoading && (
                            <div role="status" aria-label="Загрузка календаря" className="h-full p-3">
                                <Skeleton className="h-full min-h-[420px] w-full" />
                            </div>
                        )}
                        {!isLoading && view === "grid" && gridContent}
                        {!isLoading && view === "timeline" && timelineContent}
                        {!isLoading && isSplit && calendarQuery.data && <SplitCalendarLayout top={gridContent} bottom={timelineContent} />}
                    </div>
                    {(showPanel || (isScenarioMode && !isPhone)) && calendarQuery.data && (
                        <CalendarSidePanel<PanelTab>
                            active={searchParams.has("milestone") ? "milestones" : panelTab}
                            onActiveChange={(tab) => {
                                if (searchParams.has("milestone")) setViewParam("milestone", "");
                                setPanelTab(tab);
                            }}
                            onClose={() => (isScenarioMode && !isPhone ? stopScenario() : togglePanel(false))}
                            top={
                                isScenarioMode ? (
                                    <ScenarioPanel
                                        preview={scenarioPreview}
                                        isPreviewing={previewScenarioMutation.isPending}
                                        isApplying={applyScenarioMutation.isPending}
                                        onCancel={stopScenario}
                                        onApply={() => {
                                            if (
                                                scenarioPreview &&
                                                window.confirm(`Применить изменения к ${scenarioPreview.changes.length} задачам?`)
                                            ) {
                                                applyScenarioMutation.mutate(scenarioPreview);
                                            }
                                        }}
                                    />
                                ) : undefined
                            }
                            tabs={[
                                {
                                    id: "milestones",
                                    label: "Вехи",
                                    title: "Контрольные точки проекта — видны ромбом в сетке и линией на шкале",
                                    count: milestonesQuery.data?.length,
                                    content: (
                                        <MilestonePanel
                                            milestones={milestonesQuery.data ?? []}
                                            projectDueDate={calendarQuery.data.project.due_date}
                                            isLoading={milestonesQuery.isPending}
                                            error={(milestonesQuery.error as Error | null) ?? null}
                                            isSaving={isMilestoneSaving}
                                            onCreate={access.canEdit ? () => setMilestoneDialog("new") : undefined}
                                            onEdit={setMilestoneDialog}
                                            onDelete={(milestone) => {
                                                if (window.confirm(`Удалить веху «${milestone.title}»?`)) {
                                                    deleteMilestoneMutation.mutate(milestone.id);
                                                }
                                            }}
                                        />
                                    ),
                                },
                                {
                                    id: "dependencies",
                                    label: "Зависимости",
                                    title: "Какая задача ждёт завершения другой (стрелки на шкале)",
                                    count: calendarQuery.data.dependencies.length,
                                    content: (
                                        <DependencyPanel
                                            dependencies={calendarQuery.data.dependencies}
                                            tasks={tasksQuery.data ?? []}
                                            isSaving={isDependencySaving}
                                            onCreate={access.canEdit ? () => setDependencyDialogOpen(true) : undefined}
                                            onDelete={(dependency) => {
                                                if (window.confirm("Удалить зависимость задач?")) {
                                                    deleteDependencyMutation.mutate(dependency.id);
                                                }
                                            }}
                                        />
                                    ),
                                },
                                {
                                    id: "changes",
                                    label: "Изменения",
                                    title: "Недавние переносы сроков задач",
                                    count: calendarQuery.data.recent_changes.length,
                                    content:
                                        calendarQuery.data.recent_changes.length === 0 ? (
                                            <p className="py-6 text-center text-[12px] text-muted">Сроки в последнее время не переносили.</p>
                                        ) : (
                                            <CalendarRecentChanges changes={calendarQuery.data.recent_changes} onOpenTask={setSelectedTaskId} />
                                        ),
                                },
                                {
                                    id: "unscheduled",
                                    label: "Без срока",
                                    title: "Задачи без плановых дат — перетащите на день, чтобы назначить срок",
                                    count: summary?.unscheduled ?? unscheduledTasks.length,
                                    content: (
                                        <UnscheduledList
                                            tasks={unscheduledTasks}
                                            stagesById={stagesById}
                                            canDrag={access.canEdit}
                                            hasMore={unscheduledQuery.hasNextPage}
                                            isLoadingMore={unscheduledQuery.isFetchingNextPage}
                                            onOpenTask={setSelectedTaskId}
                                            onScheduleTask={(task) => setDeadlineTask(task as CalendarTaskModel)}
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
                            <CalendarTask
                                task={activeTask}
                                stageName={activeTask.stage_id === null ? undefined : stagesById.get(activeTask.stage_id)?.name}
                                onOpen={() => undefined}
                            />
                        </div>
                    )}
                </DragOverlay>
            </DndContext>
            {exportJob && (
                <div aria-hidden="true" style={{ position: "fixed", left: -100_000, top: 0, pointerEvents: "none" }}>
                    <GanttSheet
                        ref={exportSheetRef}
                        projectName={project.name}
                        range={exportJob.range}
                        scale={exportJob.scale}
                        data={exportJob.data}
                        colorMode={colorMode}
                        columns={ganttColumns}
                        groupBy={groupBy}
                        labelWidth={labelWidth}
                        today={today}
                    />
                </div>
            )}
            {deadlineTask && (
                <DeadlineDialog
                    key={`${deadlineTask.id}:${deadlineTask.start_date ?? "none"}:${deadlineTask.due_date ?? "none"}`}
                    task={deadlineTask}
                    isSaving={deadlineMutation.isPending || baselineMutation.isPending}
                    onClose={() => setDeadlineTask(null)}
                    onSave={(startDate, dueDate) => {
                        changeDates(deadlineTask, { startDate, dueDate });
                        setDeadlineTask(null);
                    }}
                    onFixBaseline={
                        isScenarioMode
                            ? undefined
                            : () => {
                                  baselineMutation.mutate(deadlineTask.id);
                                  setDeadlineTask(null);
                              }
                    }
                />
            )}
            {milestoneDialog && (
                <MilestoneDialog
                    wbsNodes={calendarQuery.data?.wbs_nodes ?? []}
                    key={milestoneDialog === "new" ? "new" : milestoneDialog.id}
                    milestone={milestoneDialog === "new" ? null : milestoneDialog}
                    initialDueDate={project.due_date ?? today}
                    isSaving={isMilestoneSaving}
                    onClose={() => setMilestoneDialog(null)}
                    onSave={(data) => {
                        if (milestoneDialog === "new") {
                            createMilestoneMutation.mutate(data);
                        } else {
                            updateMilestoneMutation.mutate({ milestoneId: milestoneDialog.id, data });
                        }
                    }}
                />
            )}
            {schedule.dialog}
            {isDependencyDialogOpen && (
                <DependencyDialog
                    tasks={tasksQuery.data ?? []}
                    isLoading={tasksQuery.isPending}
                    isSaving={createDependencyMutation.isPending}
                    onClose={() => setDependencyDialogOpen(false)}
                    onSave={(data) => createDependencyMutation.mutate(data)}
                />
            )}
            {createDate !== null && (
                <CreateTaskDialog
                    key={createDate}
                    projectId={project.id}
                    stages={stagesQuery.data ?? []}
                    isOpen
                    initialDueDate={createDate}
                    onClose={() => setCreateDate(null)}
                    onCreated={(task) => {
                        setCreateDate(null);
                        queryClient.invalidateQueries({ queryKey: ["projects", project.id, "calendar"] });
                        setSelectedTaskId(task.id);
                    }}
                />
            )}
        </div>
    );
}
