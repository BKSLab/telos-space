import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
    applyEdgeChanges,
    applyNodeChanges,
    Background,
    BackgroundVariant,
    Controls,
    MarkerType,
    MiniMap,
    ReactFlow,
    ReactFlowProvider,
    useConnection,
    useReactFlow,
    type Connection,
    type Edge,
    type EdgeChange,
    type Node,
    type NodeChange,
    type NodeMouseHandler,
    type OnNodeDrag,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Hand, Maximize2, MousePointer2, Network, Plus, Search, X } from "lucide-react";
import { cn } from "@/lib/cn";
import type { Project, ProjectStage, TaskCompact, TaskDependency, WbsNode } from "@/lib/types";
import { dueTone } from "@/lib/dates";
import {
    buildWbsGraph,
    layoutWbsGraph,
    parseGraphNodeId,
    sectionNodeId,
    TASK_NODE_SIZE,
    taskNodeId,
    type WbsLayoutMode,
} from "@/lib/wbsLayout";
import {
    buildWbsTree,
    collectAncestorIds,
    collectSubtreeIds,
    flattenTree,
    isFloatingTask,
    resolveSectionDrop,
    type SectionDropZone,
    type WbsTreeNode,
} from "@/lib/wbsTree";
import { isDraftNodeId } from "@/lib/wbsSuggestion";
import { POOL_DROP_ATTRIBUTE, TASK_DRAG_TYPE } from "@/components/wbs/TaskPool";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { EmptyState } from "@/components/ui/States";
import { LinkEdge } from "@/components/wbs/edges/LinkEdge";
import {
    DEPENDENCY_COLOR,
    EDGE_ACCENT_COLOR,
    EDGE_ACCENT_WIDTH,
    EDGE_COLOR,
    EDGE_WIDTH,
} from "@/components/wbs/edges/edgeStyle";
import { ProjectNode } from "@/components/wbs/nodes/ProjectNode";
import { SectionNode } from "@/components/wbs/nodes/SectionNode";
import { TaskCard } from "@/components/wbs/nodes/TaskCard";
import { TaskNode } from "@/components/wbs/nodes/TaskNode";
import { useMediaQuery } from "@/lib/useMediaQuery";

/** Пороги semantic zoom (§34 ТЗ): подобраны так, чтобы текст оставался читаемым. */
const DETAIL_FULL_ZOOM = 0.75;
const DETAIL_COMPACT_ZOOM = 0.45;
/** Раскладка успевает пересчитаться до того, как мы центрируем результат поиска. */
const FOCUS_DELAY_MS = 280;
/** Смещение курсора, до которого перетаскивание считается кликом. */
const CLICK_TOLERANCE_PX = 4;

const NODE_TYPES = { project: ProjectNode, section: SectionNode, task: TaskNode };
const EDGE_TYPES = { link: LinkEdge };

/** React Flow отдаёт мышь или касание — берём координаты указателя из любого. */
function pointerPosition(event: MouseEvent | TouchEvent): { x: number; y: number } | null {
    if ("clientX" in event) {
        return { x: event.clientX, y: event.clientY };
    }
    const touch = event.changedTouches[0] ?? event.touches[0];
    return touch === undefined ? null : { x: touch.clientX, y: touch.clientY };
}

/** Экранный прямоугольник в координатах окна. */
interface ScreenRect {
    x: number;
    y: number;
    width: number;
    height: number;
}

/** Список задач находится вне canvas, поэтому его границы ищем в документе. */
function poolRect(): DOMRect | null {
    return document.querySelector(`[${POOL_DROP_ATTRIBUTE}]`)?.getBoundingClientRect() ?? null;
}

/**
 * Насколько карточка заехала на список задач по горизонтали.
 *
 * Считаем по самой карточке, а не по курсору: пользователь тащит карточку и
 * смотрит на неё, а взял он её за любой край.
 */
function poolOverlap(rect: ScreenRect): number {
    const pool = poolRect();
    if (pool === null) {
        return 0;
    }
    const overlapX = Math.min(rect.x + rect.width, pool.right) - Math.max(rect.x, pool.left);
    const overlapY = Math.min(rect.y + rect.height, pool.bottom) - Math.max(rect.y, pool.top);
    return overlapX > 0 && overlapY > 0 ? overlapX : 0;
}

/**
 * Карточка считается принесённой в список. Случайное касание краем не должно
 * уводить задачу из структуры, поэтому нужен заметный заход.
 */
function overlapsPool(rect: ScreenRect): boolean {
    return poolOverlap(rect) >= Math.min(rect.width * 0.3, 70);
}

/** Куда пользователь кладёт задачу: в раздел, на холст или обратно в пул. */
export interface TaskPlacement {
    wbsNodeId: number | null;
    beforeTaskId?: number | null;
    canvasX?: number | null;
    canvasY?: number | null;
}

interface SectionDropState {
    movedId: number;
    targetId: number;
    zone: SectionDropZone;
}

type TaskDropState =
    | { kind: "section"; taskId: number; nodeId: number; beforeTaskId: number | null }
    | { kind: "canvas"; taskId: number }
    | { kind: "pool"; taskId: number };

export interface CanvasHandlers {
    onToggleCollapse: (nodeId: number) => void;
    onRename: (nodeId: number, title: string) => void;
    onCancelRename: () => void;
    onOpenSectionMenu: (nodeId: number, anchor: { x: number; y: number }) => void;
    onOpenTaskMenu: (taskId: number, anchor: { x: number; y: number }) => void;
    onOpenTask: (taskId: number) => void;
    onPlaceTask: (taskId: number, placement: TaskPlacement) => void;
    /** Стрелка между задачами задаёт последовательность работ. */
    onCreateDependency: (predecessorTaskId: number, successorTaskId: number) => void;
    onRemoveDependency: (dependencyId: number) => void;
    /** Указатель с карточкой над списком задач: пул подсвечивается как цель. */
    onPoolHover: (isOver: boolean) => void;
    onMoveSection: (nodeId: number, parentId: number | null, beforeId: number | null) => void;
    onAddRootSection: () => void;
}

interface StructureCanvasProps {
    project: Project;
    nodes: WbsNode[];
    tasks: TaskCompact[];
    stages: ProjectStage[];
    collapsed: Set<number>;
    layoutMode: WbsLayoutMode;
    editingNodeId: number | null;
    selectedTaskId: number | null;
    focusNodeId?: number | null;
    /** Задача, которую сейчас тащат из пула. */
    draggingTask: TaskCompact | null;
    /** Зависимости задач проекта: последовательность работ поверх структуры. */
    dependencies: TaskDependency[];
    handlers: CanvasHandlers;
}

export function StructureCanvas(props: StructureCanvasProps) {
    return (
        <ReactFlowProvider>
            <CanvasInner {...props} />
        </ReactFlowProvider>
    );
}

function CanvasInner({
    project,
    nodes,
    tasks,
    stages,
    collapsed,
    layoutMode,
    editingNodeId,
    selectedTaskId,
    focusNodeId,
    draggingTask,
    dependencies,
    handlers,
}: StructureCanvasProps) {
    const compact = useMediaQuery("(max-width: 1023px)");
    const [touchEditing, setTouchEditing] = useState(false);
    const canDragNodes = !compact || touchEditing;
    const { fitView, setCenter, getZoom, screenToFlowPosition, flowToScreenPosition } =
        useReactFlow();
    // Пока от раздела тянут стрелку, карточки задач подсвечиваются как цели.
    const isConnecting = useConnection((connection) => connection.inProgress);
    const [flowNodes, setFlowNodes] = useState<Node[]>([]);
    const [flowEdges, setFlowEdges] = useState<Edge[]>([]);
    const focusedSection = useRef<number | null>(null);
    useEffect(() => {
        if (!focusNodeId || focusedSection.current === focusNodeId) return;
        const target = flowNodes.find((node) => node.id === sectionNodeId(focusNodeId));
        if (!target) return;
        focusedSection.current = focusNodeId;
        void setCenter(target.position.x + (target.width ?? 240) / 2, target.position.y + (target.height ?? 120) / 2, { zoom: 0.9, duration: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 200 });
    }, [flowNodes, focusNodeId, setCenter]);
    const [zoom, setZoom] = useState(1);
    const [dropTargetId, setDropTargetId] = useState<number | null>(null);
    const [sectionDrop, setSectionDrop] = useState<SectionDropState | null>(null);
    const [taskDrop, setTaskDrop] = useState<TaskDropState | null>(null);
    /**
     * Карточку у края холста обрезает область React Flow, поэтому над
     * списком задач за курсором едет её двойник поверх всей страницы.
     */
    const [poolGhost, setPoolGhost] = useState<{
        rect: ScreenRect;
        zoom: number;
        task: TaskCompact;
        isTarget: boolean;
    } | null>(null);
    const [search, setSearch] = useState("");
    const [isSearchOpen, setSearchOpen] = useState(false);
    const layoutRequestRef = useRef(0);
    /** Последняя раскладка: по ней узел возвращается на место после неудачного переноса. */
    const layoutPositionsRef = useRef(new Map<string, { x: number; y: number }>());
    const layoutModeRef = useRef(layoutMode);
    const dragOriginRef = useRef<{ x: number; y: number } | null>(null);
    const dragMovedRef = useRef(false);

    const stagesById = useMemo(() => new Map(stages.map((stage) => [stage.id, stage])), [stages]);
    const isOverdue = useCallback(
        (task: TaskCompact) => dueTone(task.due_date, task.is_done) === "danger",
        [],
    );
    const tree = useMemo(() => buildWbsTree(nodes, tasks, isOverdue), [nodes, tasks, isOverdue]);

    const detail: "full" | "compact" | "minimal" =
        zoom >= DETAIL_FULL_ZOOM ? "full" : zoom >= DETAIL_COMPACT_ZOOM ? "compact" : "minimal";
    const showTasks = detail !== "minimal";

    const graph = useMemo(
        () =>
            buildWbsGraph({
                roots: tree.roots,
                collapsed,
                showTasks,
                floatingTasks: tree.floating,
                dependencies,
            }),
        [tree.roots, tree.floating, collapsed, showTasks, dependencies],
    );

    /**
     * Раскладка пересчитывается при смене топологии, режима или координат
     * карточек на холсте — hover, выделение и открытие панели задачи её не
     * трогают (§43 ТЗ).
     */
    const topologyKey = useMemo(
        () =>
            [
                layoutMode,
                graph.nodes.map((node) => node.id).join(","),
                graph.edges.map((edge) => edge.id).join(","),
                tree.floating.map((task) => `${task.id}:${task.canvas_x}:${task.canvas_y}`).join(","),
            ].join("|"),
        [graph.nodes, graph.edges, layoutMode, tree.floating],
    );

    useEffect(() => {
        const requestId = ++layoutRequestRef.current;
        let cancelled = false;

        layoutWbsGraph(graph, layoutMode)
            .then(({ positions }) => {
                if (cancelled || requestId !== layoutRequestRef.current) {
                    return;
                }
                layoutPositionsRef.current = positions;
                setFlowNodes(
                    graph.nodes.map((node) => {
                        const parsed = parseGraphNodeId(node.id);
                        const isDraft =
                            parsed?.kind === "section" && isDraftNodeId(parsed.nodeId);
                        return {
                            id: node.id,
                            type: node.kind,
                            position: positions.get(node.id) ?? { x: 0, y: 0 },
                            width: node.width,
                            height: node.height,
                            draggable: node.kind !== "project" && !isDraft,
                            selectable: node.kind !== "project",
                            // Удаление узлов идёт через меню и диалог
                            // подтверждения, а не клавишей Delete.
                            deletable: false,
                            data: {},
                        };
                    }),
                );
                setFlowEdges(
                    graph.edges.map((edge) => {
                        const target = parseGraphNodeId(edge.target);
                        const source = parseGraphNodeId(edge.source);
                        const isAttachment = edge.kind === "attachment";
                        const isDependency = edge.kind === "dependency";
                        const isDraft =
                            source?.kind === "section" && isDraftNodeId(source.nodeId);
                        // Последовательность работ идёт слева направо, а связи
                        // структуры — вдоль выбранной раскладки.
                        const structureHandles = {
                            sourceHandle: layoutMode === "vertical" ? "bottom" : "right",
                            targetHandle: layoutMode === "vertical" ? "top" : "left",
                        };
                        return {
                            id: edge.id,
                            source: edge.source,
                            target: edge.target,
                            ...(isDependency
                                ? { sourceHandle: "right", targetHandle: "left" }
                                : structureHandles),
                            type: isAttachment || isDependency ? "link" : "smoothstep",
                            markerEnd:
                                isAttachment || isDependency
                                    ? {
                                          type: MarkerType.ArrowClosed,
                                          width: 14,
                                          height: 14,
                                          color: isDependency ? DEPENDENCY_COLOR : EDGE_COLOR,
                                      }
                                    : undefined,
                            selectable: (isAttachment && !isDraft) || isDependency,
                            deletable: (isAttachment && !isDraft) || isDependency,
                            data:
                                isAttachment && target?.kind === "task"
                                    ? { tone: "attachment", taskId: target.taskId, isDraft }
                                    : isDependency
                                      ? {
                                            tone: "dependency",
                                            dependencyId: edge.dependencyId,
                                            isDraft: false,
                                        }
                                      : undefined,
                        };
                    }),
                );
                // Смена режима полностью меняет геометрию — возвращаем граф в кадр.
                if (layoutModeRef.current !== layoutMode) {
                    layoutModeRef.current = layoutMode;
                    window.setTimeout(() => fitView({ duration: 240, padding: 0.15 }), 60);
                }
            })
            .catch(() => undefined);

        return () => {
            cancelled = true;
        };
        // graph пересобирается вместе с topologyKey, поэтому его достаточно.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [topologyKey]);

    /** Возвращает узел туда, где его нарисовала раскладка. */
    const restorePosition = useCallback((nodeId: string) => {
        const position = layoutPositionsRef.current.get(nodeId);
        setFlowNodes((current) =>
            position === undefined
                ? [...current]
                : current.map((node) => (node.id === nodeId ? { ...node, position } : node)),
        );
    }, []);

    /** Открепление задачи оставляет карточку там, где она сейчас нарисована. */
    const detachTask = useCallback(
        (taskId: number) => {
            const current = flowNodes.find((node) => node.id === taskNodeId(taskId));
            handlers.onPlaceTask(taskId, {
                wbsNodeId: null,
                canvasX: current?.position.x ?? null,
                canvasY: current?.position.y ?? null,
            });
        },
        [flowNodes, handlers],
    );

    /**
     * Данные узлов обновляются отдельно от раскладки: изменение прогресса или
     * выделения не должно перезапускать layout.
     *
     * Узлы, которых уже нет в данных, отбрасываются: между удалением раздела и
     * пересчётом раскладки они ещё живут в `flowNodes`, и отрисовать их нечем.
     */
    const decoratedNodes = useMemo<Node[]>(
        () =>
            flowNodes.flatMap((node): Node[] => {
                const parsed = parseGraphNodeId(node.id);
                if (parsed?.kind === "project") {
                    return [{ ...node, data: { project, progress: tree.total } }];
                }
                if (parsed?.kind === "section") {
                    const section = tree.byId.get(parsed.nodeId);
                    if (section === undefined) {
                        return [];
                    }
                    const graphNode = graph.nodes.find((item) => item.id === node.id);
                    const isSectionTarget = sectionDrop?.targetId === parsed.nodeId;
                    return [
                        {
                            ...node,
                            draggable: node.draggable !== false && canDragNodes,
                            data: {
                                section,
                                isCollapsed: collapsed.has(parsed.nodeId),
                                hiddenSections: graphNode?.hiddenSections ?? 0,
                                hiddenTasks: graphNode?.hiddenTasks ?? 0,
                                isDropTarget:
                                    dropTargetId === parsed.nodeId ||
                                    (taskDrop?.kind === "section" &&
                                        taskDrop.nodeId === parsed.nodeId) ||
                                    (isSectionTarget && sectionDrop?.zone === "inside"),
                                dropZone: isSectionTarget ? sectionDrop.zone : null,
                                isEditing: editingNodeId === parsed.nodeId,
                                isDraft: isDraftNodeId(parsed.nodeId),
                                detail,
                                onToggleCollapse: handlers.onToggleCollapse,
                                onRename: handlers.onRename,
                                onCancelRename: handlers.onCancelRename,
                                onOpenMenu: handlers.onOpenSectionMenu,
                            },
                        },
                    ];
                }
                if (parsed?.kind === "task") {
                    const task = tasks.find((item) => item.id === parsed.taskId);
                    if (task === undefined) {
                        return [];
                    }
                    return [
                        {
                            ...node,
                            draggable: node.draggable !== false && canDragNodes,
                            selected: task.id === selectedTaskId,
                            data: {
                                task,
                                stage: stagesById.get(task.stage_id),
                                detail,
                                isFloating: isFloatingTask(task),
                                isConnecting,
                                isDraft:
                                    task.wbs_node_id !== null && isDraftNodeId(task.wbs_node_id),
                                onContextMenu: handlers.onOpenTaskMenu,
                            },
                        },
                    ];
                }
                return [];
            }),
        [
            flowNodes,
            graph.nodes,
            tree,
            project,
            tasks,
            stagesById,
            collapsed,
            dropTargetId,
            sectionDrop,
            taskDrop,
            editingNodeId,
            selectedTaskId,
            detail,
            isConnecting,
            handlers,
            canDragNodes,
        ],
    );

    const selectedPathEdgeIds = useMemo(() => {
        const ids = new Set<string>();
        if (selectedTaskId === null) return ids;
        let target = taskNodeId(selectedTaskId);
        for (;;) {
            const parentEdge = flowEdges.find((edge) => edge.target === target);
            if (!parentEdge) break;
            ids.add(parentEdge.id);
            target = parentEdge.source;
        }
        return ids;
    }, [flowEdges, selectedTaskId]);

    /** Ребро без обоих концов React Flow отрисовать не может. */
    const visibleEdges = useMemo(() => {
        const present = new Set(decoratedNodes.map((node) => node.id));
        return flowEdges
            .filter((edge) => present.has(edge.source) && present.has(edge.target))
            .map((edge) => {
                // Связи, которые провёл пользователь, рисует собственный edge:
                // он же даёт крестик, которым связь разрывают.
                if (edge.type !== "link") {
                    return {
                        ...edge,
                        style: selectedPathEdgeIds.has(edge.id)
                            ? {
                                  stroke: EDGE_ACCENT_COLOR,
                                  strokeWidth: EDGE_ACCENT_WIDTH,
                                  opacity: 1,
                              }
                            : { stroke: EDGE_COLOR, strokeWidth: EDGE_WIDTH, opacity: 1 },
                    };
                }
                const data = edge.data as {
                    tone: string;
                    taskId?: number;
                    dependencyId?: number;
                };
                const isDependency = data.tone === "dependency";
                return {
                    ...edge,
                    data: {
                        ...data,
                        isOnSelectedPath: !isDependency && selectedPathEdgeIds.has(edge.id),
                        removeLabel: isDependency
                            ? "Удалить последовательность задач"
                            : "Убрать задачу из раздела",
                        onRemove: () =>
                            isDependency
                                ? handlers.onRemoveDependency(data.dependencyId as number)
                                : detachTask(data.taskId as number),
                    },
                };
            });
    }, [decoratedNodes, flowEdges, selectedPathEdgeIds, detachTask, handlers]);

    /**
     * Раздел под курсором — цель сброса раздела (§26 ТЗ). Верхняя и нижняя
     * четверть узла означают вставку рядом, середина — внутрь.
     */
    const findSectionAt = useCallback(
        (
            clientX: number,
            clientY: number,
            excludeIds?: Set<number>,
        ): { section: WbsTreeNode; zone: SectionDropZone } | null => {
            const point = screenToFlowPosition({ x: clientX, y: clientY });
            for (const node of flowNodes) {
                const parsed = parseGraphNodeId(node.id);
                if (parsed?.kind !== "section" || excludeIds?.has(parsed.nodeId)) {
                    continue;
                }
                const width = node.width ?? 0;
                const height = node.height ?? 0;
                const inside =
                    point.x >= node.position.x &&
                    point.x <= node.position.x + width &&
                    point.y >= node.position.y &&
                    point.y <= node.position.y + height;
                if (!inside) {
                    continue;
                }
                const section = tree.byId.get(parsed.nodeId);
                if (section === undefined) {
                    return null;
                }
                const offset = (point.y - node.position.y) / Math.max(height, 1);
                const zone: SectionDropZone =
                    offset < 0.25 ? "before" : offset > 0.75 ? "after" : "inside";
                return { section, zone };
            }
            return null;
        },
        [flowNodes, screenToFlowPosition, tree.byId],
    );

    /** Задача под курсором вместе с половиной карточки — она задаёт порядок. */
    const findTaskAt = useCallback(
        (
            clientX: number,
            clientY: number,
            excludeTaskId: number,
        ): { task: TaskCompact; isAbove: boolean } | null => {
            const point = screenToFlowPosition({ x: clientX, y: clientY });
            for (const node of flowNodes) {
                const parsed = parseGraphNodeId(node.id);
                if (parsed?.kind !== "task" || parsed.taskId === excludeTaskId) {
                    continue;
                }
                const width = node.width ?? 0;
                const height = node.height ?? 0;
                const inside =
                    point.x >= node.position.x &&
                    point.x <= node.position.x + width &&
                    point.y >= node.position.y &&
                    point.y <= node.position.y + height;
                if (!inside) {
                    continue;
                }
                const task = tasks.find((item) => item.id === parsed.taskId);
                if (task === undefined || task.wbs_node_id === null) {
                    return null;
                }
                return { task, isAbove: point.y - node.position.y < height / 2 };
            }
            return null;
        },
        [flowNodes, screenToFlowPosition, tasks],
    );

    /**
     * Куда попадёт задача, если отпустить её здесь.
     *
     * Карточка над другой задачей встаёт рядом с ней и наследует её раздел,
     * над разделом — уходит в конец его списка, над пустым холстом — остаётся
     * вне структуры.
     */
    const resolveTaskDrop = useCallback(
        (
            taskId: number,
            clientX: number,
            clientY: number,
            /** Экранные границы перетаскиваемой карточки, если её тащат по холсту. */
            cardRect?: ScreenRect,
        ): TaskDropState => {
            if (cardRect !== undefined && overlapsPool(cardRect)) {
                return { kind: "pool", taskId };
            }
            const neighbour = findTaskAt(clientX, clientY, taskId);
            // Соседство с задачей из черновика ничего не значит: её раздела
            // в проекте ещё нет.
            if (
                neighbour !== null &&
                neighbour.task.wbs_node_id !== null &&
                !isDraftNodeId(neighbour.task.wbs_node_id)
            ) {
                const siblings = tree.byId.get(neighbour.task.wbs_node_id)?.tasks ?? [];
                const index = siblings.findIndex((item) => item.id === neighbour.task.id);
                const beforeTaskId = neighbour.isAbove
                    ? neighbour.task.id
                    : (siblings[index + 1]?.id ?? null);
                return {
                    kind: "section",
                    taskId,
                    nodeId: neighbour.task.wbs_node_id,
                    beforeTaskId: beforeTaskId === taskId ? null : beforeTaskId,
                };
            }
            const hit = findSectionAt(clientX, clientY);
            if (hit !== null && !isDraftNodeId(hit.section.node.id)) {
                return { kind: "section", taskId, nodeId: hit.section.node.id, beforeTaskId: null };
            }
            return { kind: "canvas", taskId };
        },
        [findSectionAt, findTaskAt, tree.byId],
    );

    const handleDragOver = useCallback(
        (event: React.DragEvent) => {
            if (!event.dataTransfer.types.includes(TASK_DRAG_TYPE)) {
                return;
            }
            event.preventDefault();
            event.dataTransfer.dropEffect = "move";
            const hit = findSectionAt(event.clientX, event.clientY);
            setDropTargetId(
                hit === null || isDraftNodeId(hit.section.node.id) ? null : hit.section.node.id,
            );
        },
        [findSectionAt],
    );

    /**
     * Сброс из пула: на раздел — в структуру, на пустое место — просто на холст.
     * Второе и позволяет сначала разложить карточки, а связать их потом.
     */
    const handleDrop = useCallback(
        (event: React.DragEvent) => {
            event.preventDefault();
            setDropTargetId(null);
            // Идентификатор берём из dataTransfer: состояние React к моменту
            // drop может быть ещё не обновлено после dragstart.
            const taskId = Number(event.dataTransfer.getData(TASK_DRAG_TYPE));
            if (!Number.isFinite(taskId) || taskId === 0) {
                return;
            }
            const drop = resolveTaskDrop(taskId, event.clientX, event.clientY);
            if (drop.kind === "section") {
                handlers.onPlaceTask(taskId, {
                    wbsNodeId: drop.nodeId,
                    beforeTaskId: drop.beforeTaskId,
                });
                return;
            }
            if (drop.kind === "canvas") {
                const point = screenToFlowPosition({ x: event.clientX, y: event.clientY });
                handlers.onPlaceTask(taskId, {
                    wbsNodeId: null,
                    canvasX: point.x,
                    canvasY: point.y,
                });
            }
        },
        [handlers, resolveTaskDrop, screenToFlowPosition],
    );

    /**
     * Выделение и перетаскивание живут во внутреннем состоянии React Flow,
     * поэтому изменения нужно применять самим: без этого нельзя выделить
     * стрелку, чтобы разорвать привязку.
     */
    const handleNodesChange = useCallback((changes: NodeChange[]) => {
        setFlowNodes((current) => applyNodeChanges(changes, current));
    }, []);

    const handleEdgesChange = useCallback((changes: EdgeChange[]) => {
        setFlowEdges((current) => applyEdgeChanges(changes, current));
    }, []);

    /** Границы узла на экране: положение на холсте с учётом панорамы и масштаба. */
    const nodeScreenRect = useCallback(
        (node: Node): ScreenRect => {
            const topLeft = flowToScreenPosition(node.position);
            const zoom = getZoom();
            return {
                x: topLeft.x,
                y: topLeft.y,
                width: (node.measured?.width ?? node.width ?? TASK_NODE_SIZE.width) * zoom,
                height: (node.measured?.height ?? node.height ?? TASK_NODE_SIZE.height) * zoom,
            };
        },
        [flowToScreenPosition, getZoom],
    );

    const handleNodeDragStart: OnNodeDrag = useCallback((event) => {
        dragOriginRef.current = pointerPosition(event);
        dragMovedRef.current = false;
    }, []);

    const handleNodeDrag: OnNodeDrag = useCallback(
        (event, node) => {
            const pointer = pointerPosition(event);
            const origin = dragOriginRef.current;
            if (pointer !== null && origin !== null) {
                dragMovedRef.current =
                    dragMovedRef.current ||
                    Math.abs(pointer.x - origin.x) > CLICK_TOLERANCE_PX ||
                    Math.abs(pointer.y - origin.y) > CLICK_TOLERANCE_PX;
            }
            const parsed = parseGraphNodeId(node.id);
            if (parsed?.kind === "task") {
                const cardRect = nodeScreenRect(node);
                const drop =
                    pointer === null
                        ? null
                        : resolveTaskDrop(parsed.taskId, pointer.x, pointer.y, cardRect);
                setTaskDrop(drop);
                // Список задач подсвечивается как цель, а сама карточка
                // показывается двойником поверх страницы: настоящий узел за
                // границу холста не выйдет, его там обрезает.
                const overPool = drop?.kind === "pool";
                handlers.onPoolHover(overPool);
                // Двойник появляется с первого же касания панели: карточка не
                // должна нырять под неё даже краем — она лежит сверху, как на
                // столе. Подсветка цели включается позже, по заметному заходу.
                const dragged = tasks.find((item) => item.id === parsed.taskId);
                setPoolGhost(
                    poolOverlap(cardRect) > 0 && dragged !== undefined
                        ? { rect: cardRect, zoom: getZoom(), task: dragged, isTarget: overPool }
                        : null,
                );
                return;
            }
            if (parsed?.kind !== "section") {
                return;
            }
            // Перенос внутрь собственного потомка создал бы цикл — такие цели исключаем.
            const forbidden = collectSubtreeIds(nodes, parsed.nodeId);
            const hit = pointer === null ? null : findSectionAt(pointer.x, pointer.y, forbidden);
            setSectionDrop(
                hit === null || isDraftNodeId(hit.section.node.id)
                    ? null
                    : { movedId: parsed.nodeId, targetId: hit.section.node.id, zone: hit.zone },
            );
        },
        [nodes, tasks, findSectionAt, resolveTaskDrop, handlers, nodeScreenRect, getZoom],
    );

    const handleNodeDragStop: OnNodeDrag = useCallback(
        (event, node) => {
            const parsed = parseGraphNodeId(node.id);
            const pendingTask = taskDrop;
            const pendingSection = sectionDrop;
            setTaskDrop(null);
            setSectionDrop(null);
            handlers.onPoolHover(false);
            setPoolGhost(null);

            if (parsed?.kind === "task") {
                if (!dragMovedRef.current) {
                    // Карточку не двигали: это клик, а не перенос.
                    restorePosition(node.id);
                    return;
                }
                const pointer = pointerPosition(event);
                const drop =
                    pendingTask ??
                    (pointer === null
                        ? null
                        : resolveTaskDrop(
                              parsed.taskId,
                              pointer.x,
                              pointer.y,
                              nodeScreenRect(node),
                          ));
                if (drop === null) {
                    restorePosition(node.id);
                    return;
                }
                if (drop.kind === "section") {
                    handlers.onPlaceTask(parsed.taskId, {
                        wbsNodeId: drop.nodeId,
                        beforeTaskId: drop.beforeTaskId,
                    });
                    return;
                }
                if (drop.kind === "pool") {
                    handlers.onPlaceTask(parsed.taskId, { wbsNodeId: null });
                    return;
                }
                handlers.onPlaceTask(parsed.taskId, {
                    wbsNodeId: null,
                    canvasX: node.position.x,
                    canvasY: node.position.y,
                });
                return;
            }

            if (parsed?.kind !== "section" || pendingSection === null) {
                // Без валидной цели узел возвращается туда, где его нарисовала раскладка.
                restorePosition(node.id);
                return;
            }
            const target = resolveSectionDrop(
                nodes,
                pendingSection.targetId,
                pendingSection.zone,
                pendingSection.movedId,
            );
            if (target === null) {
                restorePosition(node.id);
                return;
            }
            handlers.onMoveSection(pendingSection.movedId, target.parentId, target.beforeId);
        },
        [nodes, sectionDrop, taskDrop, handlers, resolveTaskDrop, restorePosition, nodeScreenRect],
    );

    /**
     * Смысл новой стрелки задаёт её начало: от раздела — привязка задачи к
     * структуре, от задачи — последовательность работ.
     */
    const handleConnect = useCallback(
        (connection: Connection) => {
            const source = parseGraphNodeId(connection.source);
            const target = parseGraphNodeId(connection.target);
            if (target?.kind !== "task") {
                return;
            }
            if (source?.kind === "section" && !isDraftNodeId(source.nodeId)) {
                handlers.onPlaceTask(target.taskId, { wbsNodeId: source.nodeId });
                return;
            }
            if (source?.kind === "task" && source.taskId !== target.taskId) {
                handlers.onCreateDependency(source.taskId, target.taskId);
            }
        },
        [handlers],
    );

    const isValidConnection = useCallback((connection: Connection | Edge) => {
        const source = parseGraphNodeId(connection.source);
        const target = parseGraphNodeId(connection.target);
        if (target?.kind !== "task") {
            return false;
        }
        if (source?.kind === "section") {
            return !isDraftNodeId(source.nodeId);
        }
        return source?.kind === "task" && source.taskId !== target.taskId;
    }, []);

    const handleEdgesDelete = useCallback(
        (edges: Edge[]) => {
            for (const edge of edges) {
                (edge.data as { onRemove?: () => void } | undefined)?.onRemove?.();
            }
        },
        [],
    );

    const handleNodeClick: NodeMouseHandler = useCallback(
        (_event, node) => {
            const parsed = parseGraphNodeId(node.id);
            if (parsed?.kind === "task" && !dragMovedRef.current) {
                handlers.onOpenTask(parsed.taskId);
            }
        },
        [handlers],
    );

    /** Поиск раскрывает предков найденного элемента и центрирует его (§36 ТЗ). */
    const searchResults = useMemo(() => {
        const query = search.trim().toLowerCase();
        if (query === "") {
            return [];
        }
        const sections = flattenTree(tree.roots)
            .filter(
                (item) =>
                    item.node.title.toLowerCase().includes(query) || item.number.startsWith(query),
            )
            .map((item) => ({
                id: sectionNodeId(item.node.id),
                label: `${item.number} ${item.node.title}`,
                nodeId: item.node.id,
                kind: "section" as const,
            }));
        const matchedTasks = tasks
            .filter(
                (task) =>
                    task.wbs_node_id !== null &&
                    (task.title.toLowerCase().includes(query) ||
                        task.key.toLowerCase().includes(query)),
            )
            .map((task) => ({
                id: taskNodeId(task.id),
                label: `${task.key} ${task.title}`,
                nodeId: task.wbs_node_id as number,
                kind: "task" as const,
            }));
        return [...sections, ...matchedTasks].slice(0, 8);
    }, [search, tree.roots, tasks]);

    const focusResult = useCallback(
        (result: { id: string; nodeId: number; kind: "section" | "task" }) => {
            const toExpand =
                result.kind === "section"
                    ? collectAncestorIds(nodes, result.nodeId)
                    : [result.nodeId, ...collectAncestorIds(nodes, result.nodeId)];
            for (const id of toExpand) {
                if (collapsed.has(id)) {
                    handlers.onToggleCollapse(id);
                }
            }
            window.setTimeout(() => {
                const target = flowNodes.find((node) => node.id === result.id);
                if (target) {
                    setCenter(
                        target.position.x + (target.width ?? 0) / 2,
                        target.position.y + (target.height ?? 0) / 2,
                        { zoom: Math.max(getZoom(), DETAIL_FULL_ZOOM), duration: 320 },
                    );
                }
            }, FOCUS_DELAY_MS);
            setSearchOpen(false);
            setSearch("");
        },
        [nodes, collapsed, flowNodes, handlers, setCenter, getZoom],
    );

    if (nodes.length === 0 && tree.floating.length === 0) {
        return (
            <div className="flex h-full items-center justify-center p-6">
                <EmptyState
                    title="Структура пока пустая"
                    description="Добавьте раздел или перетащите задачу из списка прямо на холст — связать её с разделом можно позже."
                    icon={<Network size={24} />}
                    action={
                        <Button
                            variant="primary"
                            icon={<Plus size={15} />}
                            onClick={handlers.onAddRootSection}
                        >
                            Создать раздел
                        </Button>
                    }
                />
            </div>
        );
    }

    return (
        <div
            className="wbs-canvas material-mineral relative h-full min-h-0 w-full"
            onDragOver={handleDragOver}
            onDrop={handleDrop}
            onDragLeave={() => setDropTargetId(null)}
        >
            <ReactFlow
                nodes={decoratedNodes}
                edges={visibleEdges}
                nodeTypes={NODE_TYPES}
                edgeTypes={EDGE_TYPES}
                onNodesChange={handleNodesChange}
                onEdgesChange={handleEdgesChange}
                onNodeClick={handleNodeClick}
                onNodeDragStart={handleNodeDragStart}
                onNodeDrag={handleNodeDrag}
                onNodeDragStop={handleNodeDragStop}
                onConnect={handleConnect}
                isValidConnection={isValidConnection}
                onEdgesDelete={handleEdgesDelete}
                onMove={(_event, viewport) => setZoom(viewport.zoom)}
                nodesConnectable={canDragNodes}
                zoomOnPinch
                // Пул стоит у левого края: авто-панорамирование при
                // подтаскивании карточки к нему уводило бы структуру вправо.
                autoPanOnNodeDrag={false}
                connectionRadius={130}
                minZoom={0.25}
                maxZoom={1.8}
                fitView
                proOptions={{ hideAttribution: true }}
                className="bg-transparent"
            >
                <Background
                    variant={BackgroundVariant.Dots}
                    gap={22}
                    size={1}
                    color="var(--color-border-subtle)"
                />
                <Controls
                    showInteractive={false}
                    className="!overflow-hidden !rounded-[var(--radius-control)] !border-line-subtle !bg-floating !shadow-panel [&_button]:!border-line-subtle [&_button]:!bg-floating [&_button]:!fill-secondary hover:[&_button]:!bg-hover"
                />
                <MiniMap
                    pannable
                    zoomable
                    ariaLabel="Обзорная карта структуры"
                    className="!rounded-[var(--radius-control)] !border !border-line-subtle !bg-floating !shadow-panel"
                    maskColor="var(--surface-void)"
                    nodeColor={(node) =>
                        node.type === "project"
                            ? project.color
                            : node.type === "section"
                              ? "var(--color-accent)"
                              : "var(--surface-pressed)"
                    }
                />
            </ReactFlow>

            <div className="wbs-canvas-tools pointer-events-none absolute top-3 right-3 left-3 flex justify-between gap-2">
                <div className="wbs-canvas-search pointer-events-auto flex flex-col gap-1.5">
                    {isSearchOpen ? (
                        <div className="wbs-search-panel glass w-72 rounded-md p-1.5 shadow-panel">
                            <div className="flex items-center gap-1">
                            <Input
                                autoFocus
                                value={search}
                                aria-label="Поиск по структуре"
                                placeholder="Раздел или задача"
                                onChange={(event) => setSearch(event.target.value)}
                                onKeyDown={(event) => {
                                    if (event.key === "Escape") {
                                        setSearch("");
                                        setSearchOpen(false);
                                    }
                                    if (event.key === "Enter" && searchResults.length > 0) {
                                        focusResult(searchResults[0]);
                                    }
                                }}
                            />
                            <Button variant="ghost" size="sm" aria-label="Закрыть поиск" icon={<X size={16} />} onClick={() => { setSearchOpen(false); setSearch(""); }} />
                            </div>
                            {searchResults.length > 0 && (
                                <ul className="mt-1 flex flex-col">
                                    {searchResults.map((result) => (
                                        <li key={result.id}>
                                            <button
                                                type="button"
                                                onClick={() => focusResult(result)}
                                                className="w-full truncate rounded-sm px-2 py-1 text-left text-[12px] text-secondary hover:bg-hover hover:text-primary"
                                            >
                                                {result.label}
                                            </button>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </div>
                    ) : (
                        <Button
                            size="sm"
                            icon={<Search size={13} />}
                            className="glass"
                            onClick={() => setSearchOpen(true)}
                        >
                            Поиск
                        </Button>
                    )}
                </div>

                <div className="pointer-events-auto flex items-center gap-1.5">
                    {compact && <Button size="sm" className="glass" aria-label="Перемещение объектов" aria-pressed={touchEditing} icon={touchEditing ? <MousePointer2 size={14} /> : <Hand size={14} />} onClick={() => setTouchEditing(value => !value)}>{touchEditing ? "Правка" : "Холст"}</Button>}
                    <span
                        className={cn(
                            "wbs-zoom-label glass rounded-md px-2 py-1 font-mono text-[11px] text-muted",
                            detail === "minimal" && "text-warning",
                        )}
                        title={
                            detail === "minimal"
                                ? "Задачи скрыты: показана архитектура проекта"
                                : "Масштаб"
                        }
                    >
                        {Math.round(zoom * 100)}%
                    </span>
                    <Button
                        size="sm"
                        className="glass"
                        icon={<Maximize2 size={13} />}
                        onClick={() => fitView({ duration: 240, padding: 0.15 })}
                    >
                        Вся структура
                    </Button>
                </div>
            </div>

            {poolGhost !== null && (
                // Это та же самая карточка, просто нарисованная поверх страницы:
                // узел React Flow за границу холста не выйдет, его там обрезает.
                <div
                    aria-hidden="true"
                    style={{
                        left: poolGhost.rect.x,
                        top: poolGhost.rect.y,
                        width: TASK_NODE_SIZE.width,
                        height: TASK_NODE_SIZE.height,
                        transform: `scale(${poolGhost.zoom})`,
                        transformOrigin: "top left",
                    }}
                    className="pointer-events-none fixed z-50"
                >
                    <TaskCard
                        task={poolGhost.task}
                        stage={stagesById.get(poolGhost.task.stage_id)}
                        detail={detail}
                        isFloating={isFloatingTask(poolGhost.task)}
                        isDraft={false}
                        isSelected={poolGhost.isTarget}
                    />
                </div>
            )}

            <CanvasHint
                isConnecting={isConnecting}
                sectionDrop={sectionDrop}
                taskDrop={taskDrop}
                draggingTask={draggingTask}
                dropTargetId={dropTargetId}
                tree={tree}
            />
        </div>
    );
}

interface CanvasHintProps {
    isConnecting: boolean;
    sectionDrop: SectionDropState | null;
    taskDrop: TaskDropState | null;
    draggingTask: TaskCompact | null;
    dropTargetId: number | null;
    tree: ReturnType<typeof buildWbsTree>;
}

/** Подсказка внизу холста: что произойдёт, если отпустить сейчас. */
function CanvasHint({
    isConnecting,
    sectionDrop,
    taskDrop,
    draggingTask,
    dropTargetId,
    tree,
}: CanvasHintProps) {
    const title = (nodeId: number) => tree.byId.get(nodeId)?.node.title ?? "";
    let message: string | null = null;

    if (isConnecting) {
        message = "Отпустите на карточке задачи — она привяжется к разделу";
    } else if (sectionDrop !== null) {
        const target = title(sectionDrop.targetId);
        message =
            sectionDrop.zone === "inside"
                ? `Сделать подразделом: ${target}`
                : sectionDrop.zone === "before"
                  ? `Поставить перед: ${target}`
                  : `Поставить после: ${target}`;
    } else if (taskDrop !== null) {
        message =
            taskDrop.kind === "section"
                ? `Поместить в: ${title(taskDrop.nodeId)}`
                : taskDrop.kind === "pool"
                  ? "Вернуть в список задач"
                  : "Оставить на холсте вне структуры";
    } else if (draggingTask !== null) {
        message =
            dropTargetId === null
                ? `${draggingTask.key}: на раздел — в структуру, на холст — просто положить`
                : `Поместить в: ${title(dropTargetId)}`;
    } else if (tree.floating.length > 0) {
        // Подсказка живёт ровно до тех пор, пока на холсте есть
        // непривязанные карточки: дальше она только мешает.
        message =
            "Синяя точка снизу раздела — привязать задачу; жёлтая справа у карточки — задать очерёдность";
    }

    if (message === null) {
        return null;
    }
    return (
        <div className="pointer-events-none absolute bottom-3 left-1/2 -translate-x-1/2">
            <p className="glass rounded-md px-3 py-1.5 text-[12px] text-secondary shadow-panel">
                {message}
            </p>
        </div>
    );
}
