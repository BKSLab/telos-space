import "@/components/board/board.css";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { BoardActions } from "@/components/board/BoardActions";
import { BoardSearch } from "@/components/board/BoardSearch";
import { BoardProjectDialog } from "@/components/board/BoardProjectDialog";
import { alignObjects, distributeObjects, type Alignment, type DistributionAxis } from "@/lib/board/layout";
import { type BoardGrouping, expandGroupSelection, lockedSelection, pruneGrouping, groupSelection, ungroupSelection, setSelectionLocked, assertRigidGroupMovement } from "@/lib/board/group";
import { createSelectionTemplate, instantiateTemplate } from "@/lib/board/template";
import { decodeMetadata, loadEditorMetadata, createProjectFromBoard } from "@/lib/board/metadata";
import { editorBatchState, editorSignature, emptyEditor, insertionObjects, assertEditableChanges, EDITOR_HISTORY_KEY, type LocalEditorValue, type BoardHistoryValue } from "@/lib/board/editorState";
import { exportSVG, exportPNG, resolveExportPalette } from "@/lib/board/export";
import { newUuid } from "@/lib/uuid";
import {
    useCallback,
    useEffect,
    useLayoutEffect,
    useMemo,
    useRef,
    useState,
    type PointerEvent as ReactPointerEvent,
} from "react";
import { Maximize, Minus, Plus, Search, Shapes } from "lucide-react";
import { ApiError, api, endpoints, queryKeys } from "@/lib/api";
import { ANCHORS, anchorPoint, findAnchorTarget, type AnchorNode } from "@/lib/board/anchors";
import {
    boxFromDrag,
    constrainAngle,
    isBoxVisible,
    unionBoxes,
    type Box,
} from "@/lib/board/geometry";
import { BoardHistory } from "@/lib/board/history";
import { objectsInMarquee, toggleSelection } from "@/lib/board/hit";
import { pendingBoardSaves, SaveQueue } from "@/lib/board/saveQueue";
import {
    anchorNodes,
    connectorPoints,
    connectorsAttachedTo,
    containedKeys,
    duplicateShapes,
    IdRegistry,
    isConnector,
    maxZ,
    nodeLookup,
    objectBox,
    reorderObjects,
    sceneFromContent,
    shapeFromServer,
    SHAPE_LIMITS,
    sortedObjects,
    STICKER_LIMITS,
    stickerObject,
    syncConnectors,
    withConnectorGeometry,
    type LocalRef,
    type Scene,
    type SceneObject,
    type ShapeObject,
    type StickerObject,
} from "@/lib/board/scene";
import { snapMove, snapPoint, snapResize, resizeBox, RESIZE_HANDLES, SNAP_SCREEN_PX, type Guide, type ResizeHandle } from "@/lib/board/snapping";
import {
    STICKER_NODE_HEIGHT,
    STICKER_NODE_WIDTH,
    type BoardSticker,
    type BoardStickerInput,
} from "@/lib/board/stickers";
import {
    defaultStyle,
    isConnectorKind,
    FONT_SIZE_PX,
    type BatchResponse,
    type BoardContent,
    type Point,
    type Board,
    type ShapeKind,
    type ShapeStyle,
} from "@/lib/board/types";
import { useToast } from "@/lib/toast";
import { sceneSignature } from "@/lib/board/sceneSignature";
import { reconcileRemoteScene } from "@/lib/board/remoteScene";
import { changeConnectorEndpoint, editConnectorBend, type ConnectorAction } from "@/lib/board/connectorEditing";
import type { Project, ProjectMember, Task, ProjectCreate } from "@/lib/types";
import { useUiStore } from "@/stores/ui";
import { applyConnectorDom, applyNodeDom } from "@/components/board/boardDom";
import { ConnectorView, ShapeView, StickerView } from "@/components/board/BoardObjects";
import { BoardPropsPanel } from "@/components/board/BoardPropsPanel";
import { BoardToolbar } from "@/components/board/BoardToolbar";
import { TOOLS, type BoardTool } from "@/components/board/boardTools";
import { BoardStickerDialog } from "@/components/board/BoardStickerDialog";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { ErrorMessage } from "@/components/ui/States";
import { wheelAction } from "@/lib/board/wheel";
import { useMediaQuery } from "@/lib/useMediaQuery";

interface View {
    x: number;
    y: number;
    zoom: number;
}

const MIN_ZOOM = 0.2;
const MAX_ZOOM = 3;
const DRAG_THRESHOLD = 3;
const ANCHOR_RADIUS_PX = 12;
const DOUBLE_CLICK_MS = 350;
const PASTE_OFFSET = 24;
const OVERLAY_Z = 2_000_000_000;

/** Где стоял холст у каждой доски — возвращаемся туда же при переключении вкладок. */
const savedViews = new Map<number, View>();
/** Keep a failed-save warning through the existing conflict reload. */
const saveErrors = new Map<number, string>();

/** Буфер обмена хранится отдельно для каждой доски. */
let boardClipboard: { boardId: number; objects: SceneObject[]; grouping?: BoardGrouping } | null = null;

type Gesture =
    | { type: "pan"; pointerId: number; startClient: Point; origin: View }
    | { type: "pinch"; ids: number[]; startDistance: number; startCenter: Point; origin: View }
    | {
          type: "move";
          pointerId: number;
          startClient: Point;
          moved: boolean;
          nodeOrigins: Map<string, Box>;
          connectorOrigins: Map<string, Point[]>;
          attached: string[];
          selBox: Box;
          others: Box[];
          clickedKey: string;
          delta: Point;
      }
    | { type: "resize"; pointerId: number; startClient: Point; key: string; handle: ResizeHandle; origin: Box; others: Box[]; attached: string[]; box: Box; moved: boolean }
    | { type: "marquee"; pointerId: number; start: Point; rect: Box; additive: boolean }
    | { type: "draw"; pointerId: number; kind: ShapeKind; start: Point; box: Box; moved: boolean }
    | { type: "connector"; pointerId: number; kind: ShapeKind; start: Point; startRef: LocalRef | null; end: Point; endRef: LocalRef | null; moved: boolean }
    | { type: "endpoint"; pointerId: number; key: string; end: "start" | "end"; ref: LocalRef | null; point: Point; moved: boolean }
    | { type: "bend"; pointerId: number; key: string; index: number; insert: boolean; origin: Point; point: Point; moved: boolean };

type StickerEditor = { mode: "create"; position: { canvas_x: number; canvas_y: number } } | { mode: "edit"; sticker: BoardSticker; key: string } | null;

interface Editing {
    key: string;
    isNew: boolean;
}

export interface BoardEditorProps {
    /** null — свободная (личная) доска: без задач и участников проекта. */
    project: Project | null;
    board: Board;
    content: BoardContent;
    members: ProjectMember[];
    tasks: Task[];
    tasksLoading: boolean;
    tasksError: Error | null;
    readOnly: boolean;
    readOnlyHint?: string;
    onRetryTasks: () => void;
    /** Перезагрузить содержимое доски с сервера (после 409 или ошибки сохранения). */
    onReload: () => void;
}

function errorMessage(error: unknown, fallback: string): string {
    return error instanceof Error && error.message ? error.message : fallback;
}

function isTextInput(target: EventTarget | null): boolean {
    if (!(target instanceof HTMLElement)) return false;
    return Boolean(target.closest("input, textarea, select, [contenteditable='true'], [role='dialog'], [role='search']"));
}

function initialView(scene: Scene): View {
    const box = unionBoxes([...scene.values()].map(objectBox));
    if (!box) return { x: 88, y: 40, zoom: 1 };
    return { x: 88 - box.x, y: 48 - box.y, zoom: 1 };
}

function nodeKind(object: SceneObject): ShapeKind | "sticker" {
    return object.type === "sticker" ? "sticker" : object.kind;
}

/**
 * Редактор доски: один «мир» с transform translate/scale, в нём каждый объект —
 * абсолютный элемент с z-index = z_index (общая шкала стикеров и фигур).
 * Во время жестов React и сцена не трогаются: элементы двигаются напрямую
 * в requestAnimationFrame, фиксация — по отпусканию. Сохранение — очередь
 * пакетов /batch (lib/board/saveQueue.ts). Подробно — docs/boards-drawing.md.
 */
export function BoardEditor({
    project,
    board,
    content,
    members,
    tasks,
    tasksLoading,
    tasksError,
    readOnly,
    readOnlyHint,
    onRetryTasks,
    onReload,
}: BoardEditorProps) {
    const toast = useToast();
    const compact = useMediaQuery("(max-width: 1023px)");
    const projectId = project?.id ?? null;
    const setSelectedTaskId = useUiStore((state) => state.setSelectedTaskId);
    const setBoardSelection = useUiStore((state) => state.setBoardSelection);
    const [registry] = useState(() => new IdRegistry());
    const [scene, setScene] = useState<Scene>(() => sceneFromContent(content, registry));
    const sceneRef = useRef(scene);
    const queryClient = useQueryClient();
    const navigate = useNavigate();
    const [localEditor, setLocalEditor] = useState<LocalEditorValue>(emptyEditor);
    const editorRef = useRef(localEditor);
    const [editorReady, setEditorReady] = useState(false);
    const editorReadyRef = useRef(false);
    const [editorError, setEditorError] = useState<string | null>(null);
    const metadataRevisionRef = useRef(board.revision);
    const sentEditorRef = useRef<LocalEditorValue | null>(null);
    const [actionsOpen, setActionsOpen] = useState(false);
    const [searchOpen, setSearchOpen] = useState(false);
    const [mobilePropsOpen, setMobilePropsOpen] = useState(false);
    const [projectDialog, setProjectDialog] = useState(false);
    const interactionReadOnly = readOnly || !editorReady;

    const [selection, setSelection] = useState<string[]>([]);
    const selectionRef = useRef(selection);
    const [tool, setTool] = useState<BoardTool>("select");
    const [toolLocked, setToolLocked] = useState(false);
    const [view, setView] = useState<View>(() => savedViews.get(board.id) ?? initialView(scene));
    const viewRef = useRef(view);
    const [size, setSize] = useState({ width: 1200, height: 800 });
    const [editing, setEditing] = useState<Editing | null>(null);
    const [labelDraft, setLabelDraft] = useState("");
    const [stickerEditor, setStickerEditor] = useState<StickerEditor>(null);
    const [stickerError, setStickerError] = useState<string | null>(null);
    const [stickerSaving, setStickerSaving] = useState(false);
    const [deleteRequest, setDeleteRequest] = useState<{ shapeKeys: string[]; stickerKeys: string[] } | null>(null);
    const [deleting, setDeleting] = useState(false);
    const [deleteError, setDeleteError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [saveState, setSaveState] = useState<"saving" | "saved" | "error">(() => saveErrors.has(board.id) ? "error" : "saved");
    const [historyFlags, setHistoryFlags] = useState({ canUndo: false, canRedo: false });
    const [spaceHeld, setSpaceHeld] = useState(false);

    const viewportRef = useRef<HTMLDivElement>(null);
    const worldRef = useRef<HTMLDivElement>(null);
    const guidesRef = useRef<SVGSVGElement>(null);
    const marqueeRef = useRef<HTMLDivElement>(null);
    const ghostShapeRef = useRef<HTMLDivElement>(null);
    const ghostConnectorRef = useRef<HTMLDivElement>(null);
    const anchorsRef = useRef<HTMLDivElement>(null);
    const elementsRef = useRef(new Map<string, HTMLElement>());
    const gestureRef = useRef<Gesture | null>(null);
    const frameRef = useRef<number | null>(null);
    const pendingMoveRef = useRef<{ clientX: number; clientY: number; shiftKey: boolean; altKey: boolean } | null>(null);
    const touchesRef = useRef(new Map<number, Point>());
    const lastClickRef = useRef<{ key: string | null; time: number; point: Point } | null>(null);
    const pasteCountRef = useRef(0);
    const commitViewTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const [history] = useState(() => new BoardHistory<BoardHistoryValue>(100));
    const failureRef = useRef<(error: unknown) => void>(() => undefined);
    const [queue] = useState(
        () =>
            new SaveQueue({
                getScene: () => sceneRef.current,
                registry,
                getEditorState: (creates) => {
                    sentEditorRef.current = structuredClone(editorRef.current);
                    return editorBatchState(sceneRef.current, registry, editorRef.current, metadataRevisionRef.current, creates);
                },
                send: (ops, editorState) => api.post<BatchResponse>(endpoints.boardBatch(projectId, board.id), { ops, ...(editorState ? {editor_state: editorState} : {}) }),
                onResponse: (response, snapshots) => {
                    saveErrors.delete(board.id);
                    const next = new Map(sceneRef.current);
                    for (const shape of response.shapes) {
                        const key = registry.keyFor(shape.id);
                        const current = next.get(key), sent = snapshots.get(key);
                        if (current && sent && sceneSignature(new Map([[key, current]])) === sceneSignature(new Map([[key, sent]]))) {
                            next.set(key, shapeFromServer(shape, registry));
                        }
                    }
                    for (const sticker of response.stickers) {
                        const key = registry.keyForSticker(sticker.id);
                        const current = next.get(key);
                        if (current?.type === "sticker") {
                            const sent = snapshots.get(key);
                            if (sent && sceneSignature(new Map([[key, current]])) === sceneSignature(new Map([[key, sent]]))) {
                                next.set(key, stickerObject(sticker, key));
                                continue;
                            }
                            next.set(key, { ...current, id: sticker.id, sticker: {
                                ...current.sticker, id: sticker.id, revision: sticker.revision,
                                created_by_user_id: sticker.created_by_user_id,
                                created_by_username_snapshot: sticker.created_by_username_snapshot,
                                created_by_display_name_snapshot: sticker.created_by_display_name_snapshot,
                                created_at: sticker.created_at, updated_at: sticker.updated_at,
                            } });
                        }
                    }
                    sceneRef.current = next;
                    setScene(next);
                    if (response.editor_state) {
                        metadataRevisionRef.current = response.editor_state.revision;
                        updateBoardRevision(response.editor_state.revision);
                        if (sentEditorRef.current && editorSignature(editorRef.current) === editorSignature(sentEditorRef.current)) {
                            setEditorValue({type: "editor", ...decodeMetadata(response.editor_state.metadata, registry)});
                        }
                    }
                },
                onFailure: (error) => failureRef.current(error),
                onStateChange: ({ pending }) => setSaveState(pending ? "saving" : saveErrors.has(board.id) ? "error" : "saved"),
            }),
    );

    function setEditorValue(value: LocalEditorValue) {
        editorRef.current = value;
        setLocalEditor(value);
    }
    function updateBoardRevision(revision: number) {
        const key = projectId === null ? queryKeys.freeBoards : queryKeys.projectBoards(projectId);
        queryClient.setQueryData<Board[]>(key, current => current?.map(item => item.id === board.id ? {...item, revision} : item));
    }
    async function readConsistentBoard() {
        for (let attempt = 0; attempt < 3; attempt += 1) {
            const [scene, metadata] = await Promise.all([
                api.get<BoardContent>(endpoints.boardContent(projectId, board.id)),
                loadEditorMetadata(projectId, board.id),
            ]);
            if (scene.board.revision === metadata.revision) return {scene, metadata};
        }
        throw new Error("Доска изменилась во время загрузки. Повторите обновление.");
    }
    useEffect(() => {
        let active = true;
        void readConsistentBoard().then(({scene: checkedScene, metadata: reply}) => {
            if (!active) return;
            const freshScene = sceneFromContent(checkedScene, registry);
            for (const key of sceneRef.current.keys()) if (!freshScene.has(key)) registry.unbind(key);
            sceneRef.current = freshScene;
            setScene(freshScene);
            queue.reset();
            metadataRevisionRef.current = reply.revision;
            updateBoardRevision(reply.revision);
            setEditorValue({type: "editor", ...decodeMetadata(reply.metadata, registry)});
            editorReadyRef.current = true;
            setEditorReady(true);
        }).catch(error => {
            if (active) setEditorError(errorMessage(error, "Не удалось загрузить доску."));
        });
        return () => { active = false; };
    // Редактор перемонтируется при смене доски; callbacks читают её стабильные refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [projectId, board.id, registry]);

    const externalRefreshRef = useRef<(active: () => boolean) => Promise<void>>(async () => undefined);
    useEffect(() => {
        externalRefreshRef.current = async function refresh(active) {
            if (!editorReadyRef.current || queue.pending || gestureRef.current || editing || stickerEditor || deleteRequest || busy || projectDialog) return;
            const {scene: remote, metadata: remoteEditor} = await readConsistentBoard();
            if (externalRefreshRef.current !== refresh || !active() || queue.pending || gestureRef.current || editing || stickerEditor || deleteRequest || busy) return;
            const next = sceneFromContent(remote, registry);
            const nextEditor: LocalEditorValue = {type: "editor", ...decodeMetadata(remoteEditor.metadata, registry)};
            if (sceneSignature(next) === sceneSignature(sceneRef.current) && editorSignature(nextEditor) === editorSignature(editorRef.current)) {
                sceneRef.current = next;
                setScene(next);
                metadataRevisionRef.current = remoteEditor.revision;
                updateBoardRevision(remoteEditor.revision);
                setEditorValue(nextEditor);
                queue.reset();
                return;
            }
            const {base, changes} = reconcileRemoteScene(sceneRef.current, next);
            sceneRef.current = base;
            for (const [key, replacement] of changes) if (!replacement) registry.unbind(key);
            commit(changes, { record: !readOnly, save: false, editor: nextEditor });
            metadataRevisionRef.current = remoteEditor.revision;
            updateBoardRevision(remoteEditor.revision);
            queue.reset();
        };
    });
    useEffect(() => {
        let active = true;
        let refreshing = false;
        const timer = setInterval(() => {
            if (refreshing || document.visibilityState === "hidden") return;
            refreshing = true;
            void externalRefreshRef.current(() => active).catch(() => undefined).finally(() => { refreshing = false; });
        }, 5000);
        return () => { active = false; clearInterval(timer); };
    }, [board.id]);

    const tasksById = useMemo(() => new Map(tasks.map((task) => [task.id, task])), [tasks]);
    const selectedSet = useMemo(() => new Set(selection), [selection]);
    useEffect(() => {
        const completeSelection = [...expandGroupSelection(selection, localEditor.groups)];
        const refs = completeSelection.map(key => {
            const object = scene.get(key);
            const id = registry.idOf(key);
            return object && id !== undefined ? {type: object.type, id} : null;
        });
        // Never send a partial group or temporary local IDs to the agent.
        const objects = saveState !== "saving" && refs.length <= 200 && refs.every(item => item !== null)
            ? refs.filter((item): item is {type: "shape" | "sticker"; id: number} => item !== null) : [];
        setBoardSelection({boardId: board.id, objects});
    }, [board.id, registry, scene, selection, localEditor.groups, setBoardSelection, saveState]);
    useEffect(() => () => {
        if (useUiStore.getState().boardSelection?.boardId === board.id) setBoardSelection(null);
    }, [board.id, setBoardSelection]);

    useEffect(() => {
        failureRef.current = (error: unknown) => {
            const conflict = error instanceof ApiError && error.status === 409;
            saveErrors.set(board.id, conflict ? "Доска обновлена после одновременной правки. Повторите последнее действие." : errorMessage(error, "Повторите последнее действие."));
            setSaveState("error");
            toast.error(conflict
                ? "Доску одновременно изменил другой участник — загружена свежая версия. Повторите последнее действие."
                : `Изменения доски не сохранились: ${errorMessage(error, "ошибка сервера")}. Доска перезагружена.`);
            onReload();
        };
    });

    // Очередь живёт с редактором; при уходе с доски досылаем всё, что накопилось.
    useEffect(() => {
        queue.activate();
        const onBeforeUnload = (event: BeforeUnloadEvent) => {
            if (!queue.pending) return;
            void queue.flushNow().catch(() => undefined);
            event.preventDefault();
        };
        window.addEventListener("beforeunload", onBeforeUnload);
        return () => {
            window.removeEventListener("beforeunload", onBeforeUnload);
            const done = queue.dispose().catch(() => undefined);
            pendingBoardSaves.set(board.id, done);
            void done.finally(() => {
                if (pendingBoardSaves.get(board.id) === done) pendingBoardSaves.delete(board.id);
            });
        };
    }, [queue, board.id]);

    // ─── Холст: вид (сдвиг и масштаб) ────────────────────────────────────────

    const applyView = useCallback(() => {
        const { x, y, zoom } = viewRef.current;
        if (worldRef.current) worldRef.current.style.transform = `translate(${x}px, ${y}px) scale(${zoom})`;
        const viewport = viewportRef.current;
        if (viewport) {
            viewport.style.setProperty("--board-zoom", String(zoom));
            viewport.style.backgroundPosition = `${x}px ${y}px`;
            viewport.style.backgroundSize = `${32 * zoom}px ${32 * zoom}px`;
        }
    }, []);

    const commitView = useCallback(() => {
        const next = { ...viewRef.current };
        savedViews.set(board.id, next);
        setView(next);
    }, [board.id]);

    const scheduleCommitView = useCallback(() => {
        if (commitViewTimerRef.current) clearTimeout(commitViewTimerRef.current);
        commitViewTimerRef.current = setTimeout(commitView, 150);
    }, [commitView]);

    useLayoutEffect(() => {
        applyView();
    }, [applyView]);

    useEffect(() => {
        const viewport = viewportRef.current;
        if (!viewport) return;
        const observer = new ResizeObserver(([entry]) => {
            setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
        });
        observer.observe(viewport);
        return () => observer.disconnect();
    }, []);

    const zoomAt = useCallback((factor: number, screen: Point) => {
        const current = viewRef.current;
        const zoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, current.zoom * factor));
        const worldX = (screen[0] - current.x) / current.zoom;
        const worldY = (screen[1] - current.y) / current.zoom;
        viewRef.current = { zoom, x: screen[0] - worldX * zoom, y: screen[1] - worldY * zoom };
        applyView();
    }, [applyView]);

    // Колесо — как в Miro (lib/board/wheel.ts): колесо и щипок — масштаб у курсора,
    // Shift+колесо и горизонтальный жест тачпада — прокрутка вбок.
    useEffect(() => {
        const viewport = viewportRef.current;
        if (!viewport) return;
        const onWheel = (event: WheelEvent) => {
            if (isTextInput(event.target) && !(event.target as HTMLElement).closest(".board-editing")) return;
            // Длинный текст стикера листается колесом, только если ему есть куда листаться.
            const scrollable = (event.target as HTMLElement).closest<HTMLElement>(".project-sticker-card__body, .project-sticker-card__tasks");
            if (scrollable && scrollable.scrollHeight > scrollable.clientHeight && !event.ctrlKey && !event.metaKey) return;
            event.preventDefault();
            const action = wheelAction(event);
            if (action.type === "zoom") {
                const rect = viewport.getBoundingClientRect();
                zoomAt(action.factor, [event.clientX - rect.left, event.clientY - rect.top]);
            } else {
                viewRef.current = { ...viewRef.current, x: viewRef.current.x - action.dx, y: viewRef.current.y - action.dy };
                applyView();
            }
            scheduleCommitView();
        };
        viewport.addEventListener("wheel", onWheel, { passive: false });
        return () => viewport.removeEventListener("wheel", onWheel);
    }, [applyView, scheduleCommitView, zoomAt]);

    function zoomBy(factor: number) {
        zoomAt(factor, [size.width / 2, size.height / 2]);
        commitView();
    }

    function fitToContent() {
        const box = unionBoxes([...sceneRef.current.values()].map(objectBox));
        if (!box) {
            viewRef.current = { x: 88, y: 40, zoom: 1 };
        } else {
            const pad = 64;
            const zoom = Math.max(MIN_ZOOM, Math.min(1.5, (size.width - pad * 2 - 56) / Math.max(1, box.w), (size.height - pad * 2) / Math.max(1, box.h)));
            viewRef.current = {
                zoom,
                x: (size.width + 56) / 2 - (box.x + box.w / 2) * zoom,
                y: size.height / 2 - (box.y + box.h / 2) * zoom,
            };
        }
        applyView();
        commitView();
    }

    function focusSearchObject(key: string) {
        const object = sceneRef.current.get(key);
        if (!object || gestureRef.current) return;
        setSelectionBoth([key]);
        const keys = expandGroupSelection([key], editorRef.current.groups);
        const box = unionBoxes([...keys].map(item => sceneRef.current.get(item)).filter((item): item is SceneObject => Boolean(item)).map(objectBox));
        if (!box) return;
        const narrow = size.width < 640;
        const availableW = Math.max(120, size.width - (narrow ? 48 : 380));
        const availableH = Math.max(120, size.height - (narrow ? Math.min(300, size.height * 0.4) : 96));
        const zoom = Math.max(MIN_ZOOM, Math.min(viewRef.current.zoom, availableW / Math.max(1, box.w), availableH / Math.max(1, box.h)));
        viewRef.current = {
            zoom,
            x: (narrow ? size.width / 2 : 56 + availableW / 2) - (box.x + box.w / 2) * zoom,
            y: (narrow ? size.height - availableH / 2 : size.height / 2) - (box.y + box.h / 2) * zoom,
        };
        applyView();
        commitView();
    }

    function toWorld(clientX: number, clientY: number): Point {
        const rect = viewportRef.current?.getBoundingClientRect();
        const { x, y, zoom } = viewRef.current;
        return [(clientX - (rect?.left ?? 0) - x) / zoom, (clientY - (rect?.top ?? 0) - y) / zoom];
    }

    // ─── Фиксация изменений: сцена, история, очередь сохранения ─────────────

    function syncHistoryFlags() {
        setHistoryFlags({ canUndo: history.canUndo, canRedo: history.canRedo });
    }

    function setSelectionBoth(next: string[]) {
        const expanded = [...expandGroupSelection(next, editorRef.current.groups)].filter(key => sceneRef.current.has(key));
        selectionRef.current = expanded;
        setSelection(expanded);
    }

    /**
     * Применяет изменения объектов. Стрелки, прицепленные к изменённым узлам,
     * пересчитываются в том же шаге. record — положить шаг в историю,
     * save — отправить на сервер (стикеры, созданные/удалённые диалогом, уже сохранены).
     */
    function commit(updates: Map<string, SceneObject | null>, options: { record?: boolean; save?: boolean; editor?: LocalEditorValue } = {}) {
        const { record = true, save = true } = options;
        const previous = sceneRef.current;
        const next: Scene = new Map(previous);
        const before = new Map<string, SceneObject | null>();
        const after = new Map<string, SceneObject | null>();
        const changedNodes = new Set<string>();
        for (const [key, value] of updates) {
            const old = previous.get(key) ?? null;
            before.set(key, old);
            after.set(key, value);
            if (value === null) next.delete(key);
            else next.set(key, value);
            const probe = value ?? old;
            if (probe && !isConnector(probe)) changedNodes.add(key);
        }
        for (const [key, connector] of syncConnectors(next, changedNodes)) {
            if (!before.has(key)) before.set(key, previous.get(key) ?? null);
            after.set(key, connector);
            next.set(key, connector);
        }
        if (save) {
            try {
                if (readOnly || !editorReadyRef.current) throw new Error("Доска пока недоступна для редактирования.");
                assertEditableChanges(previous, next, options.editor ?? editorRef.current, after.keys());
            } catch (error) {
                toast.error(errorMessage(error, "Объекты заблокированы."));
                restoreDom([...after.keys()], previous);
                return false;
            }
        }
        const previousEditor = editorRef.current;
        const editor: LocalEditorValue = {...(options.editor ?? previousEditor), ...pruneGrouping(options.editor ?? previousEditor, next)};
        const editorChanged = editorSignature(editor) !== editorSignature(previousEditor);
        sceneRef.current = next;
        setScene(next);
        setEditorValue(editor);
        if (record) {
            const historyBefore = new Map<string, BoardHistoryValue | null>(before);
            const historyAfter = new Map<string, BoardHistoryValue | null>(after);
            if (editorChanged) {
                historyBefore.set(EDITOR_HISTORY_KEY, structuredClone(previousEditor));
                historyAfter.set(EDITOR_HISTORY_KEY, structuredClone(editor));
            }
            history.push({ before: historyBefore, after: historyAfter });
            syncHistoryFlags();
        }
        if (save) {
            queue.markDirty(after.keys());
            if (editorChanged) queue.markEditorDirty();
        }
        const alive = selectionRef.current.filter((key) => next.has(key));
        const normalized = [...expandGroupSelection(alive, editor.groups)].filter(key => next.has(key));
        if (normalized.length !== selectionRef.current.length || normalized.some((key, index) => key !== selectionRef.current[index])) setSelectionBoth(normalized);
        restoreDom([...after.keys()], next);
        return true;
    }

    /** Возвращает DOM объектов к зафиксированному состоянию (после жеста или его отмены). */
    function restoreDom(keys: string[], source: Scene = sceneRef.current) {
        for (const key of keys) {
            const element = elementsRef.current.get(key);
            const object = source.get(key);
            if (!element || !object) continue;
            if (object.type === "shape" && isConnectorKind(object.kind)) {
                if (object.points) applyConnectorDom(element, object.points, object.style);
            } else {
                applyNodeDom(element, objectBox(object), nodeKind(object), object.type === "shape" ? object.style.stroke_width : 2);
            }
            element.style.transform = "";
        }
    }

    function applyHistory(snapshots: Map<string, BoardHistoryValue | null>) {
        const metadata = snapshots.get(EDITOR_HISTORY_KEY);
        const objects = new Map<string, SceneObject | null>();
        for (const [key, value] of snapshots) {
            if (key !== EDITOR_HISTORY_KEY && value?.type !== "editor") objects.set(key, value as SceneObject | null);
        }
        if (!commit(objects, {record: false, ...(metadata?.type === "editor" ? {editor: metadata} : {})})) return false;
        const restored = [...objects].filter(([key, value]) => value !== null && sceneRef.current.has(key)).map(([key]) => key);
        setSelectionBoth(restored.length ? restored : selectionRef.current.filter(key => sceneRef.current.has(key)));
        return true;
    }
    const undo = () => {
        const snapshots = history.undo();
        if (snapshots && !applyHistory(snapshots)) history.redo();
        syncHistoryFlags();
    };
    const redo = () => {
        const snapshots = history.redo();
        if (snapshots && !applyHistory(snapshots)) history.undo();
        syncHistoryFlags();
    };

    // ─── Оверлеи жестов (прямой DOM) ────────────────────────────────────────

    function showGuides(guides: Guide[]) {
        const svg = guidesRef.current;
        if (!svg) return;
        svg.innerHTML = guides
            .map((guide) => guide.axis === "x"
                ? `<line x1="${guide.pos}" y1="${guide.from - 16}" x2="${guide.pos}" y2="${guide.to + 16}" />`
                : `<line x1="${guide.from - 16}" y1="${guide.pos}" x2="${guide.to + 16}" y2="${guide.pos}" />`)
            .join("");
    }

    function showAnchors(node: AnchorNode | null, active: string | null) {
        const container = anchorsRef.current;
        if (!container) return;
        if (!node) {
            container.hidden = true;
            return;
        }
        container.hidden = false;
        container.querySelectorAll<HTMLElement>("[data-anchor]").forEach((dot) => {
            const anchor = dot.dataset.anchor as (typeof ANCHORS)[number];
            const [x, y] = anchorPoint(node.box, anchor);
            dot.style.left = `${x}px`;
            dot.style.top = `${y}px`;
            dot.toggleAttribute("data-active", anchor === active);
        });
    }

    function hideOverlays() {
        showGuides([]);
        showAnchors(null, null);
        if (marqueeRef.current) marqueeRef.current.hidden = true;
        if (ghostShapeRef.current) ghostShapeRef.current.hidden = true;
        if (ghostConnectorRef.current) ghostConnectorRef.current.hidden = true;
        viewportRef.current?.classList.remove("is-gesturing");
    }

    function othersFor(exclude: Set<string>): Box[] {
        const boxes: Box[] = [];
        for (const object of sceneRef.current.values()) {
            if (exclude.has(object.key) || isConnector(object)) continue;
            boxes.push(objectBox(object));
        }
        return boxes;
    }

    function refFromHit(hit: ReturnType<typeof findAnchorTarget>): LocalRef | null {
        if (!hit) return null;
        return { type: hit.node.kind === "sticker" ? "sticker" : "shape", key: hit.node.key, anchor: hit.anchor };
    }

    // ─── Жесты ──────────────────────────────────────────────────────────────

    function selectTool(next: BoardTool, lock = false) {
        setTool(next);
        setToolLocked(lock && next !== "select" && next !== "hand");
        showAnchors(null, null);
    }

    function finishTool() {
        if (!toolLocked) selectTool("select");
    }

    function beginGesture(gesture: Gesture, event: ReactPointerEvent<HTMLElement>) {
        gestureRef.current = gesture;
        if ("pointerId" in gesture) {
            try {
                viewportRef.current?.setPointerCapture(gesture.pointerId);
            } catch {
                // указатель уже отпущен — жест завершится сам
            }
        }
        event.preventDefault();
    }

    function startPinch() {
        const ids = [...touchesRef.current.keys()].slice(0, 2);
        if (ids.length < 2) return;
        cancelGesture();
        const [a, b] = ids.map((id) => touchesRef.current.get(id)!);
        const rect = viewportRef.current?.getBoundingClientRect();
        gestureRef.current = {
            type: "pinch",
            ids,
            startDistance: Math.max(1, Math.hypot(a[0] - b[0], a[1] - b[1])),
            startCenter: [(a[0] + b[0]) / 2 - (rect?.left ?? 0), (a[1] + b[1]) / 2 - (rect?.top ?? 0)],
            origin: { ...viewRef.current },
        };
        viewportRef.current?.classList.add("is-panning");
        for (const id of ids) {
            try { viewportRef.current?.setPointerCapture(id); } catch { /* Touch may already have ended. */ }
        }
    }

    function handleDoubleClick(key: string | null) {
        if (!ensureEditable(key ? [key] : [])) return;
        if (!key) return;
        const object = sceneRef.current.get(key);
        if (!object) return;
        if (object.type === "sticker") {
            setStickerError(null);
            setStickerEditor({ mode: "edit", sticker: object.sticker, key: object.key });
            return;
        }
        setSelectionBoth([key]);
        setLabelDraft(object.label);
        setEditing({ key, isNew: false });
    }

    function onPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
        const target = event.target as HTMLElement;
        if (target.closest(".board-toolbar, .board-props, .board-zoom, .board-editing, .board-actions")) return;
        if (event.pointerType === "touch") {
            touchesRef.current.set(event.pointerId, [event.clientX, event.clientY]);
            if (touchesRef.current.size >= 2) {
                startPinch();
                event.preventDefault();
                return;
            }
        }
        if (editing) return;
        // 0 — левая, 1 — колёсико, 2 — правая: колёсико и правая всегда двигают холст.
        if (event.button !== 0 && event.button !== 1 && event.button !== 2) return;
        const clientPoint: Point = [event.clientX, event.clientY];
        if (event.button === 1 || event.button === 2 || tool === "hand" || spaceHeld || interactionReadOnly) {
            if (interactionReadOnly && target.closest("button, a")) return;
            viewportRef.current?.classList.add("is-panning");
            beginGesture({ type: "pan", pointerId: event.pointerId, startClient: clientPoint, origin: { ...viewRef.current } }, event);
            return;
        }
        if (target.closest("button, a, input, textarea, [data-no-drag]")) return;

        const world = toWorld(event.clientX, event.clientY);
        const snapOn = !event.altKey;
        const handle = target.closest<HTMLElement>("[data-handle]")?.dataset.handle as ResizeHandle | undefined;
        const endpoint = target.closest<HTMLElement>("[data-endpoint]")?.dataset.endpoint as "start" | "end" | undefined;
        const key = target.closest<HTMLElement>("[data-key]")?.dataset.key ?? null;

        const bend = target.closest<HTMLElement>("[data-bend]")?.dataset.bend;
        const segment = target.closest<HTMLElement>("[data-segment]")?.dataset.segment;
        if ((bend !== undefined || segment !== undefined) && selectionRef.current.length === 1) {
            if (!ensureEditable(selectionRef.current)) return;
            const object = sceneRef.current.get(selectionRef.current[0]);
            const index = Number(bend ?? segment);
            const insert = segment !== undefined;
            const previousClick = lastClickRef.current;
            if (previousClick && previousClick.key === selectionRef.current[0]
                && performance.now() - previousClick.time < DOUBLE_CLICK_MS
                && Math.hypot(previousClick.point[0] - clientPoint[0], previousClick.point[1] - clientPoint[1]) < 1.5) {
                lastClickRef.current = null;
                handleDoubleClick(selectionRef.current[0]);
                event.preventDefault();
                return;
            }
            if (object?.type === "shape" && isConnectorKind(object.kind) && object.points && Number.isInteger(index)) {
                if (insert && object.points.length + (object.style.route === "orthogonal" ? 2 : 1) > 50) return;
                const origin: Point = insert ? [(object.points[index][0] + object.points[index + 1][0]) / 2, (object.points[index][1] + object.points[index + 1][1]) / 2] : object.points[index];
                lastClickRef.current = null;
                viewportRef.current?.classList.add("is-gesturing");
                beginGesture({type: "bend", pointerId: event.pointerId, key: object.key, index, insert, origin, point: origin, moved: false}, event);
            }
            return;
        }

        // Двойной клик — сами: захват указателя уводит dblclick с объекта.
        const now = performance.now();
        const last = lastClickRef.current;
        if (last && last.key === key && now - last.time < DOUBLE_CLICK_MS && Math.hypot(last.point[0] - clientPoint[0], last.point[1] - clientPoint[1]) < 6 && tool === "select") {
            lastClickRef.current = null;
            handleDoubleClick(key);
            event.preventDefault();
            return;
        }
        lastClickRef.current = { key, time: now, point: clientPoint };

        if (handle && selectionRef.current.length === 1) {
            if (!ensureEditable(selectionRef.current)) return;
            const object = sceneRef.current.get(selectionRef.current[0]);
            if (object && !isConnector(object)) {
                viewportRef.current?.classList.add("is-gesturing");
                beginGesture({
                    type: "resize",
                    pointerId: event.pointerId,
                    startClient: clientPoint,
                    key: object.key,
                    handle,
                    origin: objectBox(object),
                    others: othersFor(new Set([object.key])),
                    attached: connectorsAttachedTo(sceneRef.current, new Set([object.key])),
                    box: objectBox(object),
                    moved: false,
                }, event);
                return;
            }
        }
        if (endpoint && selectionRef.current.length === 1) {
            if (!ensureEditable(selectionRef.current)) return;
            const object = sceneRef.current.get(selectionRef.current[0]);
            if (object && object.type === "shape" && isConnectorKind(object.kind) && object.points) {
                viewportRef.current?.classList.add("is-gesturing");
                const points = object.points;
                beginGesture({
                    type: "endpoint",
                    pointerId: event.pointerId,
                    key: object.key,
                    end: endpoint,
                    ref: endpoint === "start" ? object.startRef : object.endRef,
                    point: endpoint === "start" ? points[0] : points[points.length - 1],
                    moved: false,
                }, event);
                return;
            }
        }

        if (tool === "sticker") {
            setStickerError(null);
            setStickerEditor({
                mode: "create",
                position: { canvas_x: Math.round(world[0] - STICKER_NODE_WIDTH / 2), canvas_y: Math.round(world[1] - STICKER_NODE_HEIGHT / 2) },
            });
            finishTool();
            event.preventDefault();
            return;
        }
        if (tool === "rect" || tool === "ellipse" || tool === "diamond" || tool === "text") {
            const start = snapOn ? snapPoint(world) : world;
            viewportRef.current?.classList.add("is-gesturing");
            beginGesture({ type: "draw", pointerId: event.pointerId, kind: tool, start, box: { x: start[0], y: start[1], w: 0, h: 0 }, moved: false }, event);
            return;
        }
        if (tool === "arrow" || tool === "line") {
            const hit = findAnchorTarget(world, anchorNodes(sceneRef.current), ANCHOR_RADIUS_PX / viewRef.current.zoom);
            const start = hit ? hit.point : snapOn ? snapPoint(world) : world;
            viewportRef.current?.classList.add("is-gesturing");
            beginGesture({ type: "connector", pointerId: event.pointerId, kind: tool, start, startRef: refFromHit(hit), end: start, endRef: null, moved: false }, event);
            return;
        }

        // Инструмент «выделение»
        if (key) {
            let nextSelection = selectionRef.current;
            if (event.shiftKey) nextSelection = toggleSelection(nextSelection, key);
            else if (!nextSelection.includes(key)) nextSelection = [key];
            if (nextSelection !== selectionRef.current) setSelectionBoth(nextSelection);
            nextSelection = selectionRef.current;
            if (!nextSelection.includes(key) || !ensureEditable(nextSelection)) return;
            const nodeOrigins = new Map<string, Box>();
            const connectorOrigins = new Map<string, Point[]>();
            for (const selectedKey of nextSelection) {
                const object = sceneRef.current.get(selectedKey);
                if (!object) continue;
                if (object.type === "shape" && isConnectorKind(object.kind)) connectorOrigins.set(selectedKey, object.points ?? []);
                else nodeOrigins.set(selectedKey, objectBox(object));
            }
            // Фигура — контейнер: везёт всё, что целиком внутри неё. Ctrl/⌘ — двигать только саму фигуру.
            if (!event.ctrlKey && !event.metaKey) {
                for (const innerKey of containedKeys(sceneRef.current, [...nodeOrigins.keys(), ...connectorOrigins.keys()])) {
                    const object = sceneRef.current.get(innerKey);
                    if (!object) continue;
                    if (object.type === "shape" && isConnectorKind(object.kind)) connectorOrigins.set(innerKey, object.points ?? []);
                    else nodeOrigins.set(innerKey, objectBox(object));
                }
            }
            const moving = expandGroupSelection([...nodeOrigins.keys(), ...connectorOrigins.keys()], editorRef.current.groups);
            if (!ensureEditable(moving)) return;
            try {
                for (const group of editorRef.current.groups) {
                    if (group.members.some(item => moving.has(item))) assertRigidGroupMovement(sceneRef.current, group.members);
                }
            } catch (error) { toast.error(errorMessage(error, "Группа не перемещена.")); return; }
            for (const member of moving) {
                const object = sceneRef.current.get(member);
                if (!object) continue;
                if (isConnector(object) && object.type === "shape") connectorOrigins.set(member, object.points ?? []);
                else nodeOrigins.set(member, objectBox(object));
            }
            const selBox = unionBoxes(nextSelection.map((item) => sceneRef.current.get(item)).filter((item): item is SceneObject => Boolean(item)).map(objectBox));
            if (!selBox) return;
            beginGesture({
                type: "move",
                pointerId: event.pointerId,
                startClient: clientPoint,
                moved: false,
                nodeOrigins,
                connectorOrigins,
                attached: connectorsAttachedTo(sceneRef.current, new Set(nodeOrigins.keys())).filter((item) => !moving.has(item)),
                selBox,
                others: othersFor(moving),
                clickedKey: key,
                delta: [0, 0],
            }, event);
            return;
        }
        if (event.pointerType === "touch") {
            beginGesture({ type: "pan", pointerId: event.pointerId, startClient: clientPoint, origin: { ...viewRef.current } }, event);
            return;
        }
        if (!event.shiftKey && selectionRef.current.length > 0) setSelectionBoth([]);
        viewportRef.current?.classList.add("is-gesturing");
        beginGesture({ type: "marquee", pointerId: event.pointerId, start: world, rect: { x: world[0], y: world[1], w: 0, h: 0 }, additive: event.shiftKey }, event);
    }

    function onPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
        if (event.pointerType === "touch" && touchesRef.current.has(event.pointerId)) {
            touchesRef.current.set(event.pointerId, [event.clientX, event.clientY]);
        }
        pendingMoveRef.current = { clientX: event.clientX, clientY: event.clientY, shiftKey: event.shiftKey, altKey: event.altKey };
        if (frameRef.current !== null) return;
        frameRef.current = requestAnimationFrame(() => {
            frameRef.current = null;
            const move = pendingMoveRef.current;
            if (move) processMove(move);
        });
    }

    /** Один кадр жеста: только прямой DOM, без setState. */
    function processMove(move: { clientX: number; clientY: number; shiftKey: boolean; altKey: boolean }) {
        const gesture = gestureRef.current;
        const zoom = viewRef.current.zoom;
        if (!gesture) {
            // Наведение с инструментом стрелки: подсвечиваем точки привязки под курсором.
            if (tool === "arrow" || tool === "line") {
                const hit = findAnchorTarget(toWorld(move.clientX, move.clientY), anchorNodes(sceneRef.current), ANCHOR_RADIUS_PX / zoom);
                showAnchors(hit?.node ?? null, hit?.anchor ?? null);
            }
            return;
        }
        const snapOn = !move.altKey;
        const threshold = SNAP_SCREEN_PX / zoom;
        switch (gesture.type) {
            case "pan": {
                viewRef.current = {
                    ...gesture.origin,
                    x: gesture.origin.x + move.clientX - gesture.startClient[0],
                    y: gesture.origin.y + move.clientY - gesture.startClient[1],
                };
                applyView();
                return;
            }
            case "pinch": {
                const [a, b] = gesture.ids.map((id) => touchesRef.current.get(id));
                if (!a || !b) return;
                const rect = viewportRef.current?.getBoundingClientRect();
                const center: Point = [(a[0] + b[0]) / 2 - (rect?.left ?? 0), (a[1] + b[1]) / 2 - (rect?.top ?? 0)];
                const distance = Math.max(1, Math.hypot(a[0] - b[0], a[1] - b[1]));
                const zoomNext = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, gesture.origin.zoom * (distance / gesture.startDistance)));
                const worldX = (gesture.startCenter[0] - gesture.origin.x) / gesture.origin.zoom;
                const worldY = (gesture.startCenter[1] - gesture.origin.y) / gesture.origin.zoom;
                viewRef.current = { zoom: zoomNext, x: center[0] - worldX * zoomNext, y: center[1] - worldY * zoomNext };
                applyView();
                return;
            }
            case "move": {
                const dxScreen = move.clientX - gesture.startClient[0];
                const dyScreen = move.clientY - gesture.startClient[1];
                if (!gesture.moved && Math.hypot(dxScreen, dyScreen) < DRAG_THRESHOLD) return;
                if (!gesture.moved) {
                    gesture.moved = true;
                    viewportRef.current?.classList.add("is-gesturing");
                }
                let dx = dxScreen / zoom;
                let dy = dyScreen / zoom;
                let guides: Guide[] = [];
                if (snapOn) {
                    const snapped = snapMove({ ...gesture.selBox, x: gesture.selBox.x + dx, y: gesture.selBox.y + dy }, gesture.others, threshold);
                    dx = snapped.box.x - gesture.selBox.x;
                    dy = snapped.box.y - gesture.selBox.y;
                    guides = snapped.guides;
                }
                gesture.delta = [dx, dy];
                const overrides = new Map<string, Box>();
                for (const [key, origin] of gesture.nodeOrigins) {
                    const box = { ...origin, x: origin.x + dx, y: origin.y + dy };
                    overrides.set(key, box);
                    const element = elementsRef.current.get(key);
                    if (element) element.style.transform = `translate(${dx}px, ${dy}px)`;
                }
                for (const [key] of gesture.connectorOrigins) {
                    const element = elementsRef.current.get(key);
                    if (element) element.style.transform = `translate(${dx}px, ${dy}px)`;
                }
                const lookup = nodeLookup(sceneRef.current, overrides);
                for (const key of gesture.attached) {
                    const object = sceneRef.current.get(key);
                    const element = elementsRef.current.get(key);
                    if (!object || object.type !== "shape" || !element) continue;
                    applyConnectorDom(element, connectorPoints(object, lookup), object.style);
                }
                showGuides(guides);
                return;
            }
            case "resize": {
                const dxScreen = move.clientX - gesture.startClient[0];
                const dyScreen = move.clientY - gesture.startClient[1];
                if (!gesture.moved && Math.hypot(dxScreen, dyScreen) < 1) return;
                gesture.moved = true;
                const object = sceneRef.current.get(gesture.key);
                if (!object) return;
                const limits = object.type === "sticker" ? STICKER_LIMITS : SHAPE_LIMITS;
                let box = resizeBox(gesture.origin, gesture.handle, dxScreen / zoom, dyScreen / zoom, limits, move.shiftKey);
                let guides: Guide[] = [];
                if (snapOn && !move.shiftKey) {
                    const snapped = snapResize(box, gesture.handle, gesture.others, threshold, limits);
                    box = snapped.box;
                    guides = snapped.guides;
                }
                gesture.box = box;
                const element = elementsRef.current.get(gesture.key);
                if (element) applyNodeDom(element, box, nodeKind(object), object.type === "shape" ? object.style.stroke_width : 2);
                const lookup = nodeLookup(sceneRef.current, new Map([[gesture.key, box]]));
                for (const key of gesture.attached) {
                    const connector = sceneRef.current.get(key);
                    const connectorElement = elementsRef.current.get(key);
                    if (!connector || connector.type !== "shape" || !connectorElement) continue;
                    applyConnectorDom(connectorElement, connectorPoints(connector, lookup), connector.style);
                }
                showGuides(guides);
                return;
            }
            case "marquee": {
                const current = toWorld(move.clientX, move.clientY);
                gesture.rect = boxFromDrag(gesture.start, current);
                const marquee = marqueeRef.current;
                if (marquee) {
                    marquee.hidden = false;
                    marquee.style.left = `${gesture.rect.x}px`;
                    marquee.style.top = `${gesture.rect.y}px`;
                    marquee.style.width = `${gesture.rect.w}px`;
                    marquee.style.height = `${gesture.rect.h}px`;
                }
                return;
            }
            case "draw": {
                const raw = toWorld(move.clientX, move.clientY);
                const current = snapOn ? snapPoint(raw) : raw;
                gesture.box = boxFromDrag(gesture.start, current, move.shiftKey);
                gesture.moved = gesture.moved || gesture.box.w > 2 || gesture.box.h > 2;
                const ghost = ghostShapeRef.current;
                if (ghost) {
                    ghost.hidden = false;
                    applyNodeDom(ghost, gesture.box, gesture.kind === "text" ? "rect" : gesture.kind, 2);
                }
                return;
            }
            case "bend": {
                const raw = toWorld(move.clientX, move.clientY);
                gesture.point = snapOn ? snapPoint(raw) : raw;
                gesture.moved = gesture.moved || Math.hypot(gesture.point[0] - gesture.origin[0], gesture.point[1] - gesture.origin[1]) * zoom > DRAG_THRESHOLD;
                if (!gesture.moved) return;
                const object = sceneRef.current.get(gesture.key);
                const element = elementsRef.current.get(gesture.key);
                if (object?.type !== "shape" || !element) return;
                const draft = editConnectorBend(object, gesture.index, gesture.point, gesture.insert);
                if (draft.points) applyConnectorDom(element, draft.points, draft.style);
                return;
            }
            case "connector":
            case "endpoint": {
                const raw = toWorld(move.clientX, move.clientY);
                const connectorKey = gesture.type === "endpoint" ? gesture.key : null;
                const hit = findAnchorTarget(raw, anchorNodes(sceneRef.current), ANCHOR_RADIUS_PX / zoom, connectorKey);
                const fixed = gesture.type === "connector"
                    ? gesture.start
                    : (() => {
                          const object = sceneRef.current.get(gesture.key) as ShapeObject;
                          const points = object.points ?? [];
                          return gesture.end === "start" ? points[points.length - 1] : points[0];
                      })();
                let point: Point = hit ? hit.point : snapOn ? snapPoint(raw) : raw;
                if (!hit && move.shiftKey) point = constrainAngle(fixed, raw);
                showAnchors(hit?.node ?? null, hit?.anchor ?? null);
                if (gesture.type === "connector") {
                    gesture.end = point;
                    gesture.endRef = refFromHit(hit);
                    gesture.moved = gesture.moved || Math.hypot(point[0] - gesture.start[0], point[1] - gesture.start[1]) > 4;
                    const ghost = ghostConnectorRef.current;
                    if (ghost && gesture.moved) {
                        ghost.hidden = false;
                        const style = defaultStyle(gesture.kind);
                        applyConnectorDom(ghost, connectorPoints({ points: [gesture.start, point], startRef: gesture.startRef, endRef: gesture.endRef, style }, nodeLookup(sceneRef.current)), style);
                    }
                } else {
                    gesture.moved = true;
                    gesture.point = point;
                    gesture.ref = refFromHit(hit);
                    const object = sceneRef.current.get(gesture.key) as ShapeObject;
                    const element = elementsRef.current.get(gesture.key);
                    const draft = endpointDraft(object, gesture.end, point, gesture.ref);
                    if (element && draft.points) applyConnectorDom(element, draft.points, draft.style);
                }
                return;
            }
        }
    }

    /** Стрелка с перенесённым концом: новая ссылка (или свободная точка) и пересчёт маршрута. */
    function endpointDraft(object: ShapeObject, end: "start" | "end", point: Point, ref: LocalRef | null): ShapeObject {
        const draft = changeConnectorEndpoint(object, end, point, ref);
        return withConnectorGeometry(draft, connectorPoints(draft, nodeLookup(sceneRef.current)));
    }

    function onPointerUp(event: ReactPointerEvent<HTMLDivElement>) {
        const gesture = gestureRef.current;
        if (gesture?.type === "pinch") {
            if (frameRef.current !== null) {
                cancelAnimationFrame(frameRef.current);
                frameRef.current = null;
                const move = pendingMoveRef.current;
                if (move && event.type !== "pointercancel") processMove(move);
            }
            pendingMoveRef.current = null;
        }
        touchesRef.current.delete(event.pointerId);
        if (!gesture) return;
        if (gesture.type === "pinch") {
            if (touchesRef.current.size < 2) {
                commitView();
                const remaining = [...touchesRef.current.entries()][0];
                gestureRef.current = remaining && event.type !== "pointercancel"
                    ? { type: "pan", pointerId: remaining[0], startClient: remaining[1], origin: { ...viewRef.current } }
                    : null;
                if (!gestureRef.current) viewportRef.current?.classList.remove("is-panning");
            }
            return;
        }
        if (gesture.pointerId !== event.pointerId) return;
        if (frameRef.current !== null) {
            cancelAnimationFrame(frameRef.current);
            frameRef.current = null;
            if (event.type !== "pointercancel") processMove({ clientX: event.clientX, clientY: event.clientY, shiftKey: event.shiftKey, altKey: event.altKey });
        }
        gestureRef.current = null;
        viewportRef.current?.classList.remove("is-panning");
        try {
            viewportRef.current?.releasePointerCapture(event.pointerId);
        } catch {
            // уже отпущен
        }
        hideOverlays();
        if (event.type === "pointercancel") {
            restoreGesture(gesture);
            return;
        }
        finishGesture(gesture, event.shiftKey);
    }

    function restoreGesture(gesture: Gesture) {
        if (gesture.type === "move") restoreDom([...gesture.nodeOrigins.keys(), ...gesture.connectorOrigins.keys(), ...gesture.attached]);
        if (gesture.type === "resize") restoreDom([gesture.key, ...gesture.attached]);
        if (gesture.type === "endpoint" || gesture.type === "bend") restoreDom([gesture.key]);
        if (gesture.type === "pan") commitView();
    }

    function cancelGesture() {
        const gesture = gestureRef.current;
        if (!gesture) return;
        gestureRef.current = null;
        if (frameRef.current !== null) {
            cancelAnimationFrame(frameRef.current);
            frameRef.current = null;
        }
        hideOverlays();
        restoreGesture(gesture);
    }

    function finishGesture(gesture: Gesture, shiftKey: boolean) {
        const scene = sceneRef.current;
        switch (gesture.type) {
            case "pan":
                commitView();
                return;
            case "move": {
                if (!gesture.moved) {
                    // Клик без перетаскивания по одному из выделенных — оставляем только его.
                    if (!shiftKey && selectionRef.current.length > 1) setSelectionBoth([gesture.clickedKey]);
                    return;
                }
                const [dx, dy] = gesture.delta;
                const moving = new Set([...gesture.nodeOrigins.keys(), ...gesture.connectorOrigins.keys()]);
                const updates = new Map<string, SceneObject | null>();
                for (const [key, origin] of gesture.nodeOrigins) {
                    const object = scene.get(key);
                    if (object) updates.set(key, { ...object, x: origin.x + dx, y: origin.y + dy });
                }
                for (const [key, origin] of gesture.connectorOrigins) {
                    const object = scene.get(key);
                    if (!object || object.type !== "shape") continue;
                    const points = origin.map(([x, y]) => [x + dx, y + dy] as Point);
                    // Конец, прицепленный к неподвижному узлу, отцепляется — стрелка переехала целиком.
                    const moved: ShapeObject = {
                        ...object,
                        points,
                        startRef: object.startRef && moving.has(object.startRef.key) ? object.startRef : null,
                        endRef: object.endRef && moving.has(object.endRef.key) ? object.endRef : null,
                    };
                    updates.set(key, withConnectorGeometry(moved, points));
                }
                commit(updates);
                return;
            }
            case "resize": {
                if (!gesture.moved) return;
                const object = scene.get(gesture.key);
                if (!object) return;
                const box = gesture.box;
                commit(new Map([[gesture.key, { ...object, x: box.x, y: box.y, w: Math.round(box.w * 10) / 10, h: Math.round(box.h * 10) / 10 }]]));
                return;
            }
            case "marquee": {
                const keys = objectsInMarquee(gesture.rect, [...scene.values()].map((object) => ({ key: object.key, box: objectBox(object) })));
                if (keys.length === 0 && !gesture.additive) return;
                setSelectionBoth(gesture.additive ? [...new Set([...selectionRef.current, ...keys])] : keys);
                return;
            }
            case "draw": {
                const kind = gesture.kind;
                let box = { ...gesture.box, w: Math.min(SHAPE_LIMITS.maxW, gesture.box.w), h: Math.min(SHAPE_LIMITS.maxH, gesture.box.h) };
                if (!gesture.moved || box.w < 4 || box.h < 4) {
                    const w = kind === "text" ? 200 : 160;
                    const h = kind === "text" ? 40 : kind === "ellipse" ? 120 : 100;
                    box = { x: gesture.start[0] - (kind === "text" ? 0 : w / 2), y: gesture.start[1] - (kind === "text" ? h / 2 : h / 2), w, h };
                }
                const key = registry.newKey();
                const shape: ShapeObject = {
                    type: "shape",
                    key,
                    kind,
                    x: box.x,
                    y: box.y,
                    w: box.w,
                    h: box.h,
                    z: maxZ(scene) + 1,
                    points: null,
                    startRef: null,
                    endRef: null,
                    style: defaultStyle(kind),
                    label: "",
                };
                commit(new Map([[key, shape]]));
                setSelectionBoth([key]);
                if (kind === "text") {
                    setLabelDraft("");
                    setEditing({ key, isNew: true });
                }
                finishTool();
                return;
            }
            case "connector": {
                if (!gesture.moved) return;
                const key = registry.newKey();
                const style = defaultStyle(gesture.kind);
                const base: ShapeObject = {
                    type: "shape",
                    key,
                    kind: gesture.kind,
                    x: 0,
                    y: 0,
                    w: 0,
                    h: 0,
                    z: maxZ(scene) + 1,
                    points: [gesture.start, gesture.end],
                    startRef: gesture.startRef,
                    endRef: gesture.endRef,
                    style,
                    label: "",
                };
                commit(new Map([[key, withConnectorGeometry(base, connectorPoints(base, nodeLookup(scene)))]]));
                setSelectionBoth([key]);
                finishTool();
                return;
            }
            case "bend": {
                if (!gesture.moved) return;
                const object = scene.get(gesture.key);
                if (object?.type !== "shape") return;
                const draft = editConnectorBend(object, gesture.index, gesture.point, gesture.insert);
                if (draft !== object) commit(new Map([[gesture.key, draft]]));
                return;
            }
            case "endpoint": {
                if (!gesture.moved) return;
                const object = scene.get(gesture.key);
                if (!object || object.type !== "shape") return;
                commit(new Map([[gesture.key, endpointDraft(object, gesture.end, gesture.point, gesture.ref)]]));
                return;
            }
        }
    }

    // ─── Команды над выделением ─────────────────────────────────────────────

    function connectorAction(action: ConnectorAction) {
        if (!ensureEditable(selectionRef.current)) return;
        const object = sceneRef.current.get(selectionRef.current[0]);
        if (!object || object.type !== "shape" || !isConnectorKind(object.kind)) return;
        const points = connectorPoints(object, nodeLookup(sceneRef.current));
        let draft: ShapeObject = {...object, points};
        if (action === "reverse") draft = {...draft, points: [...points].reverse(), startRef: object.endRef, endRef: object.startRef};
        else if (action === "reset-route") draft = {...draft, points: [points[0], points[points.length - 1]]};
        else if (action === "detach-start") draft = {...draft, startRef: null};
        else draft = {...draft, endRef: null};
        commit(new Map([[object.key, withConnectorGeometry(draft, connectorPoints(draft, nodeLookup(sceneRef.current)))]]));
    }

    function ensureEditable(keys: Iterable<string>): boolean {
        if (readOnly || !editorReadyRef.current) return false;
        if (lockedSelection(keys, editorRef.current)) {
            toast.error("Сначала разблокируйте выделенные объекты.");
            return false;
        }
        return true;
    }

    async function editorCommand(change: () => LocalEditorValue) {
        if (readOnly || !editorReadyRef.current || busy) return;
        setBusy(true);
        try {
            await queue.flushNow();
            if (!commit(new Map(), {editor: change()})) return;
            await queue.flushNow();
        } catch (error) { toast.error(errorMessage(error, "Не удалось сохранить настройки доски.")); }
        finally { setBusy(false); }
    }
    function alignSelection(mode: Alignment) {
        if (!ensureEditable(selectionRef.current)) return;
        try { commit(new Map(alignObjects(sceneRef.current, selectionRef.current, mode, editorRef.current))); }
        catch (error) { toast.error(errorMessage(error, "Выравнивание не выполнено.")); }
    }
    function distributeSelection(axis: DistributionAxis) {
        if (!ensureEditable(selectionRef.current)) return;
        try { commit(new Map(distributeObjects(sceneRef.current, selectionRef.current, axis, editorRef.current))); }
        catch (error) { toast.error(errorMessage(error, "Интервалы не изменены.")); }
    }
    function makeGroup(name: string) {
        const keys = [...selectionRef.current];
        void editorCommand(() => ({...editorRef.current, ...groupSelection(sceneRef.current, editorRef.current, keys, name, newUuid())}));
    }
    function removeGroup() {
        const keys = [...selectionRef.current];
        void editorCommand(() => ({...editorRef.current, ...ungroupSelection(editorRef.current, keys)}));
    }
    function toggleLock(locked: boolean) {
        const keys = [...selectionRef.current];
        void editorCommand(() => ({...editorRef.current, ...setSelectionLocked(editorRef.current, keys, locked)}));
    }
    function saveTemplate(name: string) {
        const keys = [...selectionRef.current];
        void editorCommand(() => ({
            ...editorRef.current, templates: [...editorRef.current.templates,
                createSelectionTemplate(sceneRef.current, keys, name, newUuid(), editorRef.current)],
        }));
    }
    async function insertTemplate(id: string) {
        if (readOnly || !editorReadyRef.current || busy) return;
        setBusy(true);
        try {
            await queue.flushNow();
            const template = editorRef.current.templates.find(item => item.id === id);
            if (!template) throw new Error("Шаблон уже удалён.");
            const rect = viewportRef.current!.getBoundingClientRect();
            const [x, y] = toWorld(rect.left + rect.width / 2, rect.top + rect.height / 2);
            const insertion = instantiateTemplate(template, {x, y}, newUuid, maxZ(sceneRef.current) + 1);
            const copied = insertionObjects(insertion, registry, projectId, board.id);
            const editor: LocalEditorValue = {
                ...editorRef.current,
                groups: [...editorRef.current.groups, ...copied.grouping.groups],
                locked: [...editorRef.current.locked, ...copied.grouping.locked],
            };
            if (!commit(new Map(copied.objects), {editor})) return;
            setSelectionBoth([...copied.objects.keys()]);
            await queue.flushNow();
        } catch (error) { toast.error(errorMessage(error, "Шаблон не вставлен.")); }
        finally { setBusy(false); }
    }
    async function exportBoard(format: "png" | "svg", scope: "board" | "selection") {
        if (busy || editing || gestureRef.current) return;
        setBusy(true);
        try {
            const options = {selectedKeys: scope === "selection" ? selectionRef.current : undefined, palette: resolveExportPalette(viewportRef.current!)};
            const output = format === "svg" ? exportSVG(sceneRef.current, options) : await exportPNG(sceneRef.current, options);
            const url = URL.createObjectURL(output.blob);
            const link = document.createElement("a");
            link.href = url; link.download = board.title.replace(/[\\/:*?"<>|]/g, "_") + "." + format;
            link.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (error) { toast.error(errorMessage(error, "Не удалось выгрузить доску.")); }
        finally { setBusy(false); }
    }
    async function promoteBoard(project: ProjectCreate) {
        await queue.flushNow();
        let result;
        try { result = await createProjectFromBoard(board.id, metadataRevisionRef.current, project); }
        catch (error) {
            if (error instanceof ApiError && error.status === 409) {
                const fresh = await loadEditorMetadata(projectId, board.id);
                metadataRevisionRef.current = fresh.revision;
                updateBoardRevision(fresh.revision);
                setEditorValue({type: "editor", ...decodeMetadata(fresh.metadata, registry)});
            }
            throw error;
        }
        await Promise.all([
            queryClient.invalidateQueries({queryKey: queryKeys.freeBoards}),
            queryClient.invalidateQueries({queryKey: ["projects"]}),
            queryClient.invalidateQueries({queryKey: ["dashboard"]}),
        ]);
        navigate(`/projects/${encodeURIComponent(result.project.key)}/whiteboard?board=${board.id}`);
    }

    function selectedObjects(): SceneObject[] {
        return selectionRef.current.map((key) => sceneRef.current.get(key)).filter((object): object is SceneObject => Boolean(object));
    }

    function applyStyle(patch: Partial<ShapeStyle>) {
        if (!ensureEditable(selectionRef.current)) return;
        const scene = sceneRef.current;
        const lookup = nodeLookup(scene);
        const updates = new Map<string, SceneObject | null>();
        for (const object of selectedObjects()) {
            if (object.type !== "shape") continue;
            const styled: ShapeObject = { ...object, style: { ...object.style, ...patch } };
            if (isConnectorKind(object.kind) && patch.route !== undefined && object.points) {
                styled.points = [object.points[0], object.points[object.points.length - 1]];
            }
            updates.set(object.key, isConnectorKind(object.kind) ? withConnectorGeometry(styled, connectorPoints(styled, lookup)) : styled);
        }
        if (updates.size > 0) commit(updates);
    }

    function applyLayer(direction: "front" | "back") {
        if (!ensureEditable(selectionRef.current)) return;
        const updates = reorderObjects(sceneRef.current, selectionRef.current, direction);
        if (updates.size > 0) commit(new Map(updates));
    }

    function nudge(dx: number, dy: number) {
        if (!ensureEditable(selectionRef.current)) return;
        const updates = new Map<string, SceneObject | null>();
        const moving = new Set(selectionRef.current);
        for (const object of selectedObjects()) {
            if (object.type === "shape" && isConnectorKind(object.kind)) {
                const points = (object.points ?? []).map(([x, y]) => [x + dx, y + dy] as Point);
                updates.set(object.key, withConnectorGeometry({
                    ...object,
                    points,
                    startRef: object.startRef && moving.has(object.startRef.key) ? object.startRef : null,
                    endRef: object.endRef && moving.has(object.endRef.key) ? object.endRef : null,
                }, points));
            } else {
                updates.set(object.key, { ...object, x: object.x + dx, y: object.y + dy });
            }
        }
        if (updates.size > 0) commit(updates);
    }

    function requestDelete() {
        if (!ensureEditable(selectionRef.current)) return;
        const objects = selectedObjects();
        if (objects.length === 0) return;
        const shapeKeys = objects.filter((object) => object.type === "shape").map((object) => object.key);
        const stickerKeys = objects.filter((object) => object.type === "sticker").map((object) => object.key);
        if (stickerKeys.length > 0) {
            setDeleteError(null);
            setDeleteRequest({ shapeKeys, stickerKeys });
            return;
        }
        commit(new Map(shapeKeys.map((key) => [key, null])));
    }

    async function confirmDelete() {
        if (!deleteRequest || !ensureEditable([...deleteRequest.shapeKeys, ...deleteRequest.stickerKeys])) return;
        setDeleting(true);
        try {
            await queue.flushNow();
            const keys = [...deleteRequest.shapeKeys, ...deleteRequest.stickerKeys];
            if (keys.length > 500) throw new Error("За один раз можно удалить до 500 объектов.");
            commit(new Map(keys.map(key => [key, null])));
            await queue.flushNow();
            setDeleteRequest(null);
        } catch (error) {
            setDeleteError(errorMessage(error, "Не удалось удалить выделение."));
        } finally { setDeleting(false); }
    }

    function copySelection() {
        const objects = selectedObjects();
        if (objects.length === 0) return;
        const keys = new Set(objects.map(object => object.key));
        const grouping: BoardGrouping = {
            groups: editorRef.current.groups.filter(group => group.members.every(key => keys.has(key))).map(group => structuredClone(group)),
            locked: editorRef.current.locked.filter(key => keys.has(key)),
        };
        boardClipboard = { boardId: board.id, objects: objects.map(object => structuredClone(object)), grouping };
        try { localStorage.setItem(`board-clipboard:${board.id}`, JSON.stringify({objects: boardClipboard.objects, grouping: boardClipboard.grouping})); } catch { /* Буфер в памяти остаётся доступен. */ }
        pasteCountRef.current = 0;
    }

    function readClipboard(): SceneObject[] | null {
        if (boardClipboard?.boardId === board.id) return boardClipboard.objects;
        try {
            const parsed: unknown = JSON.parse(localStorage.getItem(`board-clipboard:${board.id}`) ?? "null");
            const payload = parsed as {objects?: SceneObject[]; grouping?: BoardGrouping} | null;
            const objects = Array.isArray(parsed) ? parsed : payload?.objects;
            if (!Array.isArray(objects) || !objects.every(item => item && (item.type === "shape" || item.type === "sticker") && typeof item.key === "string")) return null;
            boardClipboard = {boardId: board.id, objects, grouping: !Array.isArray(parsed) ? payload?.grouping : undefined};
            return objects;
        } catch { return null; }
    }

    async function paste(objects: SceneObject[] | null = readClipboard()) {
        if (readOnly || !editorReadyRef.current || busy || !objects || objects.length === 0) return;
        if (objects.length > 500) {
            toast.error("За один раз можно дублировать до 500 объектов.");
            return;
        }
        setBusy(true);
        try {
        await queue.flushNow();
        pasteCountRef.current += 1;
        const offset = PASTE_OFFSET * pasteCountRef.current;
        const top = maxZ(sceneRef.current) + 1;
        const { shapes, stickers, keyMap } = duplicateShapes(objects, offset, () => registry.newKey(), top);
        const sourceGrouping = objects === boardClipboard?.objects ? boardClipboard.grouping ?? editorRef.current : editorRef.current;
        const updates = new Map<string, SceneObject | null>();
        for (const shape of shapes) updates.set(shape.key, shape);
        for (const sticker of stickers) updates.set(sticker.key, sticker);
        if (updates.size > 0) {
            // Стрелки внутри копии пересчитываются по новым узлам.
            const draftScene: Scene = new Map(sceneRef.current);
            for (const [key, object] of updates) if (object) draftScene.set(key, object);
            const draftLookup = nodeLookup(draftScene);
            for (const shape of shapes) {
                if (!isConnectorKind(shape.kind)) continue;
                updates.set(shape.key, withConnectorGeometry(shape, connectorPoints(shape, draftLookup)));
            }
            const editor: LocalEditorValue = {
                ...editorRef.current,
                groups: [
                    ...editorRef.current.groups,
                    ...sourceGrouping.groups.filter(group => group.members.every(key => keyMap.has(key)))
                        .map(group => ({...group, id: newUuid(), members: group.members.map(key => keyMap.get(key)!)})),
                ],
                locked: [...editorRef.current.locked, ...sourceGrouping.locked.filter(key => keyMap.has(key)).map(key => keyMap.get(key)!)],
            };
            if (!commit(updates, {editor})) return;
        }
        setSelectionBoth([...updates.keys()]);
        await queue.flushNow();
        } catch (error) {
            toast.error(`Не удалось вставить объекты: ${errorMessage(error, "ошибка сервера")}`);
        } finally { setBusy(false); }
    }

    function newSticker(input: BoardStickerInput, position: { canvas_x: number; canvas_y: number }): SceneObject {
        return stickerObject({
            ...input, ...position, id: 0, project_id: projectId ?? 0, board_id: board.id,
            width: input.width ?? 230, height: input.height ?? 230,
            z_index: maxZ(sceneRef.current) + 1, revision: 1,
            created_by_user_id: null, created_by_username_snapshot: "",
            created_by_display_name_snapshot: "", created_at: "", updated_at: "",
        }, registry.newStickerKey());
    }

    async function convertToSticker() {
        if (!ensureEditable(selectionRef.current)) return;
        const [object] = selectedObjects();
        if (busy || !object || object.type !== "shape" || isConnectorKind(object.kind)) return;
        setBusy(true);
        try {
            await queue.flushNow();
            const sticker = newSticker({
                body: object.label.trim() || "Новый стикер", color: "#fff19a", task_ids: [],
                width: Math.min(520, Math.max(160, object.w)), height: Math.min(520, Math.max(160, object.h)),
            }, { canvas_x: object.x, canvas_y: object.y });
            sticker.z = object.z;
            const updates = new Map<string, SceneObject | null>([[object.key, null], [sticker.key, sticker]]);
            for (const [key, item] of sceneRef.current) {
                if (item.type !== "shape" || !isConnectorKind(item.kind)) continue;
                const retarget = (ref: LocalRef | null) => ref?.key === object.key ? { ...ref, type: "sticker" as const, key: sticker.key } : ref;
                if (item.startRef?.key === object.key || item.endRef?.key === object.key) {
                    updates.set(key, { ...item, startRef: retarget(item.startRef), endRef: retarget(item.endRef) });
                }
            }
            if (updates.size > 500) throw new Error("Замена затрагивает больше 500 объектов.");
            const editor: LocalEditorValue = {
                ...editorRef.current,
                groups: editorRef.current.groups.map(group => ({...group, members: group.members.map(key => key === object.key ? sticker.key : key)})),
            };
            if (!commit(updates, {editor})) return;
            setSelectionBoth([sticker.key]);
            await queue.flushNow();
        } catch (error) {
            toast.error(`Не удалось заменить на стикер: ${errorMessage(error, "ошибка сервера")}`);
        } finally { setBusy(false); }
    }

    // ─── Стикеры через диалог ───────────────────────────────────────────────

    async function saveSticker(input: BoardStickerInput) {
        if (!stickerEditor || (stickerEditor.mode === "edit" && !ensureEditable([stickerEditor.key]))) return;
        setStickerSaving(true);
        setStickerError(null);
        try {
            await queue.flushNow();
            if (stickerEditor.mode === "edit") {
                const key = stickerEditor.key;
                const current = sceneRef.current.get(key);
                if (!current || current.type !== "sticker") throw new Error("Стикер уже удалён.");
                commit(new Map([[key, {
                    ...current, w: input.width ?? current.w, h: input.height ?? current.h,
                    sticker: { ...current.sticker, ...input },
                }]]));
            } else {
                const created = newSticker(input, stickerEditor.position);
                commit(new Map([[created.key, created]]));
                setSelectionBoth([created.key]);
            }
            await queue.flushNow();
            setStickerEditor(null);
        } catch (error) {
            if (error instanceof ApiError && error.status === 409) {
                setStickerError("Стикер уже изменён другим участником. Доска обновлена — повторите действие.");
                onReload();
            } else {
                setStickerError(errorMessage(error, "Не удалось сохранить стикер."));
            }
        } finally {
            setStickerSaving(false);
        }
    }

    const openStickerEditor = (sticker: BoardSticker, key: string) => {
        if (!ensureEditable([key])) return;
        setStickerError(null);
        setStickerEditor({ mode: "edit", sticker, key });
    };

    const requestStickerDelete = (_sticker: BoardSticker, key: string) => {
        if (!ensureEditable([key])) return;
        setDeleteError(null);
        setDeleteRequest({ shapeKeys: [], stickerKeys: [key] });
    };

    const register = useCallback((key: string, element: HTMLElement | null) => {
        if (element) elementsRef.current.set(key, element);
        else elementsRef.current.delete(key);
    }, []);

    // ─── Подпись фигуры ─────────────────────────────────────────────────────

    function finishEditing(save: boolean) {
        if (!editing) return;
        const object = sceneRef.current.get(editing.key);
        setEditing(null);
        if (!object || object.type !== "shape") return;
        const label = labelDraft.slice(0, 2000);
        const emptyNewText = editing.isNew && object.kind === "text" && !label.trim();
        if (emptyNewText) {
            // Пустая новая надпись исчезает, не оставляя следа в истории.
            history.forget([object.key]);
            commit(new Map([[object.key, null]]), { record: false });
            syncHistoryFlags();
            return;
        }
        if (!save || label === object.label) return;
        commit(new Map([[object.key, { ...object, label }]]));
    }

    // ─── Клавиатура ─────────────────────────────────────────────────────────

    const keyHandlerRef = useRef<(event: KeyboardEvent) => void>(() => undefined);
    useEffect(() => {
        keyHandlerRef.current = (event: KeyboardEvent) => {
            if ((event.ctrlKey || event.metaKey) && event.code === "KeyF" && !isTextInput(event.target) && !stickerEditor && !deleteRequest) {
                event.preventDefault();
                setActionsOpen(false);
                setSearchOpen(true);
                return;
            }
            if (isTextInput(event.target) || stickerEditor || deleteRequest) return;
            const mod = event.ctrlKey || event.metaKey;
            // Пробел на кнопке или ссылке нажимает её, а не включает руку.
            if (event.code === "Space" && event.target instanceof HTMLElement && event.target.closest("button, a")) return;
            if (event.code === "Space" && !event.repeat) {
                setSpaceHeld(true);
                event.preventDefault();
                return;
            }
            if (event.key === "Escape") {
                if (gestureRef.current) cancelGesture();
                else if (tool !== "select") selectTool("select");
                else if (selectionRef.current.length > 0) setSelectionBoth([]);
                return;
            }
            if (mod && event.code === "KeyA") {
                event.preventDefault();
                setSelectionBoth([...sceneRef.current.keys()]);
                return;
            }
            if (interactionReadOnly) return;
            if (mod && event.code === "KeyZ") {
                event.preventDefault();
                if (event.shiftKey) redo();
                else undo();
                return;
            }
            if (mod && event.code === "KeyY") {
                event.preventDefault();
                redo();
                return;
            }
            if (mod && event.code === "KeyC") {
                copySelection();
                return;
            }
            if (mod && event.code === "KeyV") {
                event.preventDefault();
                void paste();
                return;
            }
            if (mod && event.code === "KeyD") {
                event.preventDefault();
                const objects = selectedObjects();
                if (objects.length === 0) return;
                pasteCountRef.current = 0;
                void paste(objects.map((object) => structuredClone(object)));
                return;
            }
            if (mod && (event.code === "BracketRight" || event.code === "BracketLeft")) {
                event.preventDefault();
                applyLayer(event.code === "BracketRight" ? "front" : "back");
                return;
            }
            if (event.key === "Delete" || event.key === "Backspace") {
                if (selectionRef.current.length === 0) return;
                event.preventDefault();
                requestDelete();
                return;
            }
            if (event.key.startsWith("Arrow") && selectionRef.current.length > 0) {
                event.preventDefault();
                const step = event.shiftKey ? 8 : 1;
                const dx = event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0;
                const dy = event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0;
                nudge(dx, dy);
                return;
            }
            if (mod || event.altKey) return;
            const definition = TOOLS.find((item) => item.code === event.code);
            if (definition) {
                event.preventDefault();
                selectTool(definition.tool);
            }
        };
    });

    useEffect(() => {
        const onKeyDown = (event: KeyboardEvent) => keyHandlerRef.current(event);
        const onKeyUp = (event: KeyboardEvent) => {
            if (event.code === "Space") setSpaceHeld(false);
        };
        const onBlur = () => setSpaceHeld(false);
        window.addEventListener("keydown", onKeyDown);
        window.addEventListener("keyup", onKeyUp);
        window.addEventListener("blur", onBlur);
        return () => {
            window.removeEventListener("keydown", onKeyDown);
            window.removeEventListener("keyup", onKeyUp);
            window.removeEventListener("blur", onBlur);
        };
    }, []);

    // ─── Рендер ─────────────────────────────────────────────────────────────

    // Невидимые объекты не рендерим; выделенные и прицепленные к ним стрелки — всегда.
    const visibleObjects = useMemo(() => {
        const rect: Box = { x: -view.x / view.zoom, y: -view.y / view.zoom, w: size.width / view.zoom, h: size.height / view.zoom };
        // Запас в экран: при панораме объекты уже на месте, до фиксации вида.
        const margin = Math.max(rect.w, rect.h);
        const keep = new Set(selection);
        if (editing) keep.add(editing.key);
        for (const key of connectorsAttachedTo(scene, keep)) keep.add(key);
        return sortedObjects(scene).filter((object) => keep.has(object.key) || isBoxVisible(objectBox(object), rect, margin));
    }, [scene, view, size, selection, editing]);

    const selectedList = useMemo(
        () => selection.map((key) => scene.get(key)).filter((object): object is SceneObject => Boolean(object)),
        [scene, selection],
    );
    const selectionBox = useMemo(() => unionBoxes(selectedList.map(objectBox)), [selectedList]);
    const single = selectedList.length === 1 ? selectedList[0] : null;
    const editingObject = editing ? scene.get(editing.key) : undefined;

    const propsPosition = useMemo(() => {
        if (!selectionBox) return null;
        const center = (selectionBox.x + selectionBox.w / 2) * view.zoom + view.x;
        const topEdge = selectionBox.y * view.zoom + view.y;
        const bottomEdge = (selectionBox.y + selectionBox.h) * view.zoom + view.y;
        const left = Math.max(180, Math.min(size.width - 180, center));
        const top = topEdge - 52 >= 8 ? topEdge - 52 : Math.min(size.height - 52, bottomEdge + 12);
        return { left, top };
    }, [selectionBox, view, size]);

    const isEmpty = editorReady && scene.size === 0;
    // На сенсорном экране клавиш нет — подсказка пустой доски говорит про касание.
    const coarsePointer = typeof window !== "undefined" && window.matchMedia?.("(pointer: coarse)").matches;
    const connectorSelected = single && single.type === "shape" && isConnectorKind(single.kind) ? single : null;

    return (
        <div className="board-editor" data-props-open={mobilePropsOpen || undefined}>
            {tasksError && (
                <ErrorMessage
                    title="Не удалось загрузить задачи"
                    message="Доска доступна, но названия связей стикеров временно не показаны."
                    action={<Button size="sm" variant="ghost" onClick={onRetryTasks}>Повторить</Button>}
                    className="mx-3 mt-2"
                />
            )}
            <div
                ref={viewportRef}
                className="board-viewport"
                role="region"
                aria-label={`Холст доски «${board.title}»`}
                data-tool={spaceHeld ? "hand" : tool}
                data-readonly={interactionReadOnly || undefined}
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onPointerCancel={onPointerUp}
                onPointerLeave={() => {
                    if (!gestureRef.current) showAnchors(null, null);
                }}
                onContextMenu={(event) => {
                    if (!readOnly) event.preventDefault();
                }}
            >
                <div ref={worldRef} className="board-world" style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.zoom})` }}>
                    {editorReady && localEditor.groups.map(group => {
                        const members = group.members.map(key => scene.get(key)).filter((object): object is SceneObject => Boolean(object));
                        const bounds = unionBoxes(members);
                        if (!bounds) return null;
                        return <div key={group.id} className="board-group-frame" data-group-id={group.id}
                            data-locked={lockedSelection(group.members, localEditor) || undefined}
                            style={{left: bounds.x - 8, top: bounds.y - 8, width: bounds.w + 16, height: bounds.h + 16}}>
                            <span className="board-group-label">{lockedSelection(group.members, localEditor) ? "🔒 " : ""}{group.name}</span>
                        </div>;
                    })}

                    {editorReady && visibleObjects.map((object) => {
                        if (object.type === "sticker") {
                            return (
                                <StickerView
                                    key={object.key}
                                    object={object as StickerObject}
                                    selected={selectedSet.has(object.key)}
                                    readOnly={interactionReadOnly || lockedSelection([object.key], localEditor)}
                                    readOnlyHint={lockedSelection([object.key], localEditor) ? "Стикер заблокирован. Выделите его и откройте инструменты доски, чтобы разблокировать." : readOnlyHint}
                                    members={members}
                                    tasksById={tasksById}
                                    register={register}
                                    onOpenTask={setSelectedTaskId}
                                    locked={lockedSelection([object.key], localEditor)}
                                    onEdit={openStickerEditor}
                                    onDelete={requestStickerDelete}
                                />
                            );
                        }
                        if (isConnectorKind(object.kind)) {
                            return <ConnectorView key={object.key} object={object} locked={lockedSelection([object.key], localEditor)} selected={selectedSet.has(object.key)} editing={editing?.key === object.key} register={register} />;
                        }
                        return <ShapeView key={object.key} object={object} locked={lockedSelection([object.key], localEditor)} selected={selectedSet.has(object.key)} editing={editing?.key === object.key} register={register} />;
                    })}

                    {!interactionReadOnly && single && !lockedSelection([single.key], localEditor) && !editing && !isConnector(single) && (
                        <div className="board-handles" style={{ left: single.x, top: single.y, width: single.w, height: single.h, zIndex: OVERLAY_Z }}>
                            {RESIZE_HANDLES.map((handle) => (
                                <span key={handle} data-handle={handle} className={`board-handle board-handle--${handle}`} title="Потяните, чтобы изменить размер; Shift — сохранить пропорции" />
                            ))}
                        </div>
                    )}
                    {!interactionReadOnly && connectorSelected?.points && !lockedSelection([connectorSelected.key], localEditor) && !editing && (
                        <div className="board-handles board-handles--points" style={{ zIndex: OVERLAY_Z }}>
                            {(["start", "end"] as const).map((end) => {
                                const points = connectorSelected.points!;
                                const [x, y] = end === "start" ? points[0] : points[points.length - 1];
                                return (
                                    <span
                                        key={end}
                                        data-endpoint={end}
                                        className="board-endpoint"
                                        style={{ left: x, top: y }}
                                        title="Перетащите конец к фигуре или стикеру, чтобы прицепить"
                                    />
                                );
                            })}
                            {connectorSelected.points.slice(1, -1).map(([x, y], index) => (
                                <span key={`bend-${index}`} data-bend={index + 1} className="board-endpoint board-bend"
                                    style={{left: x, top: y}} title="Перетащите изгиб связи" />
                            ))}
                            {connectorSelected.points.length <= (connectorSelected.style.route === "orthogonal" ? 48 : 49) && connectorSelected.points.slice(0, -1).map(([x, y], index) => {
                                const next = connectorSelected.points![index + 1];
                                if (x === next[0] && y === next[1]) return null;
                                return <span key={`segment-${index}`} data-segment={index} className="board-endpoint board-segment"
                                    style={{left: (x + next[0]) / 2, top: (y + next[1]) / 2}} title="Перетащите середину сегмента, чтобы добавить изгиб" />;
                            })}
                        </div>
                    )}

                    <svg ref={guidesRef} className="board-guides" style={{ zIndex: OVERLAY_Z }} aria-hidden="true" />
                    <div ref={marqueeRef} className="board-marquee" style={{ zIndex: OVERLAY_Z }} hidden />
                    <div ref={ghostShapeRef} className="board-ghost" style={{ zIndex: OVERLAY_Z }} hidden>
                        <svg aria-hidden="true"><path data-part="outline" /></svg>
                    </div>
                    <div ref={ghostConnectorRef} className="board-ghost board-ghost--connector" style={{ zIndex: OVERLAY_Z }} hidden>
                        <svg aria-hidden="true">
                            <path data-part="line" className="board-connector__line" />
                            <path data-part="head-start" className="board-connector__head" />
                            <path data-part="head-end" className="board-connector__head" />
                        </svg>
                    </div>
                    <div ref={anchorsRef} className="board-anchors" style={{ zIndex: OVERLAY_Z }} hidden>
                        {ANCHORS.map((anchor) => <span key={anchor} data-anchor={anchor} className="board-anchor" />)}
                    </div>

                    {!compact && editing && editingObject && editingObject.type === "shape" && (
                        <textarea
                            className={`board-editing board-editing--${isConnectorKind(editingObject.kind) ? "connector" : "shape"}`}
                            aria-label="Подпись фигуры"
                            placeholder="Подпись · Ctrl+Enter — сохранить, Esc — отменить"
                            autoFocus
                            value={labelDraft}
                            maxLength={2000}
                            style={(() => {
                                const font = FONT_SIZE_PX[editingObject.style.font_size];
                                if (isConnectorKind(editingObject.kind) && editingObject.points) {
                                    const points = editingObject.points;
                                    const mid = points.length === 2
                                        ? [(points[0][0] + points[1][0]) / 2, (points[0][1] + points[1][1]) / 2]
                                        : points[Math.floor(points.length / 2)];
                                    return { left: mid[0] - 90, top: mid[1] - 20, width: 180, height: 60, fontSize: font, zIndex: OVERLAY_Z };
                                }
                                return {
                                    left: editingObject.x,
                                    top: editingObject.y,
                                    width: Math.max(80, editingObject.w),
                                    height: Math.max(36, editingObject.h),
                                    fontSize: font,
                                    textAlign: editingObject.style.text_align,
                                    zIndex: OVERLAY_Z,
                                };
                            })()}
                            onChange={(event) => setLabelDraft(event.target.value)}
                            onBlur={() => finishEditing(true)}
                            onKeyDown={(event) => {
                                event.stopPropagation();
                                if (event.key === "Escape") {
                                    event.preventDefault();
                                    finishEditing(false);
                                } else if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                                    event.preventDefault();
                                    finishEditing(true);
                                }
                            }}
                        />
                    )}
                </div>

                {!editorReady && (
                    <div className="board-load" role="status">
                        {editorError ? <><p>{editorError}</p><Button onClick={onReload}>Повторить</Button></> : "Загрузка доски…"}
                    </div>
                )}
                {projectDialog && <BoardProjectDialog key={board.id} boardTitle={board.title} onCreate={promoteBoard} onClose={() => setProjectDialog(false)} />}

                {isEmpty && (
                    <div className="board-empty" aria-live="polite">
                        <Shapes size={22} aria-hidden="true" />
                        <p className="board-empty__title">{readOnly ? "На доске пока ничего нет" : "Доска пустая"}</p>
                        <p className="board-empty__text">
                            {readOnly
                                ? readOnlyHint ?? "Участники проекта ещё ничего не добавили."
                                : coarsePointer
                                  ? "Выберите инструмент. Одним пальцем двигайте холст, двумя — меняйте масштаб. Выделите объект и откройте «Свойства», чтобы изменить его"
                                  : "Выберите инструмент слева или нажмите S, чтобы поставить стикер. Двигать холст — зажатой правой кнопкой мыши"}
                        </p>
                    </div>
                )}
            </div>

            {compact && editing && (
                <Modal
                    isOpen
                    title="Редактировать подпись"
                    onOpenChange={open => { if (!open) finishEditing(false); }}
                    footer={<><Button onClick={() => finishEditing(false)}>Отмена</Button><Button variant="primary" onClick={() => finishEditing(true)}>Сохранить</Button></>}
                >
                    <textarea className="board-input board-label-input" aria-label="Подпись фигуры" autoFocus maxLength={2000} value={labelDraft} onChange={event => setLabelDraft(event.target.value)} />
                </Modal>
            )}

                {editorReady && (
                    <div className="board-actions">
                        <button type="button" className="board-actions-toggle board-button" aria-expanded={actionsOpen} aria-label="Инструменты доски" onClick={() => { setSearchOpen(false); setMobilePropsOpen(false); setActionsOpen(open => !open); }}>
                            Инструменты
                        </button>
                        <button type="button" className="board-actions-toggle board-button" aria-expanded={searchOpen} aria-label="Поиск по доске" title="Найти объект (Ctrl+F)" onClick={() => { setActionsOpen(false); setMobilePropsOpen(false); setSearchOpen(open => !open); }}>
                            <Search size={15} aria-hidden="true" /> Найти
                        </button>
                        {!interactionReadOnly && tool === "select" && selection.length > 0 && (
                            <button type="button" className="board-mobile-props-toggle board-button" aria-label="Свойства выделенного" aria-expanded={mobilePropsOpen} onClick={() => { setActionsOpen(false); setSearchOpen(false); setMobilePropsOpen(open => !open); }}>
                                Свойства · {selection.length}
                            </button>
                        )}
                        {searchOpen && <BoardSearch scene={scene} onChoose={focusSearchObject} onClose={() => setSearchOpen(false)} />}
                        {actionsOpen && <div className="board-actions-panel">
                            <BoardActions
                                selectedCount={selection.length}
                                editable={!readOnly}
                                busy={busy || Boolean(editing) || Boolean(stickerEditor) || Boolean(deleteRequest)}
                                hasLockedSelection={lockedSelection(selection, localEditor)}
                                hasGroupSelection={localEditor.groups.some(group => group.members.some(key => selectedSet.has(key)))}
                                canCreateProject={projectId === null && !readOnly}
                                templates={localEditor.templates}
                                onAlign={alignSelection} onDistribute={distributeSelection}
                                onGroup={makeGroup} onUngroup={removeGroup} onSetLocked={toggleLock}
                                onSaveTemplate={saveTemplate} onInsertTemplate={id => void insertTemplate(id)}
                                onDeleteTemplate={id => void editorCommand(() => ({...editorRef.current, templates: editorRef.current.templates.filter(item => item.id !== id)}))}
                                onExport={(format, scope) => void exportBoard(format, scope)}
                                onCreateProject={() => setProjectDialog(true)}
                            />
                        </div>}
                    </div>
                )}
                {!interactionReadOnly && (
                    <BoardToolbar
                        horizontal={compact}
                        tool={tool}
                        locked={toolLocked}
                        canUndo={historyFlags.canUndo}
                        canRedo={historyFlags.canRedo}
                        onSelectTool={selectTool}
                        onUndo={undo}
                        onRedo={redo}
                    />
                )}

                {!interactionReadOnly && tool === "select" && selectedList.length > 0 && propsPosition && !editing && (
                    <BoardPropsPanel
                        selected={selectedList}
                        position={propsPosition}
                        busy={busy || lockedSelection(selection, localEditor)}
                        onStyle={applyStyle}
                        onEditLabel={() => handleDoubleClick(selectionRef.current[0] ?? null)}
                        onConnectorAction={connectorAction}
                        onLayer={applyLayer}
                        onToSticker={() => void convertToSticker()}
                        onDelete={requestDelete}
                    onDuplicate={() => { pasteCountRef.current = 0; void paste(selectedObjects().map(object => structuredClone(object))); }}
                    />
                )}

                <div className="board-zoom" role="group" aria-label="Масштаб холста">
                    <span className="board-zoom__status" role="status" aria-label="Сохранение доски" data-state={saveState} title={saveState === "error" ? saveErrors.get(board.id) : "Изменения сохраняются автоматически"} aria-live="polite">{saveState === "saving" ? "Сохраняется…" : saveState === "error" ? "Ошибка сохранения" : "Сохранено"}</span>
                    <button type="button" className="board-props__btn" aria-label="Уменьшить масштаб" title="Уменьшить масштаб (колёсико мыши)" onClick={() => zoomBy(1 / 1.2)}>
                        <Minus size={14} aria-hidden="true" />
                    </button>
                    <button
                        type="button"
                        className="board-props__btn board-props__btn--text tabular-nums"
                        title="Вернуть масштаб 100%"
                        aria-label={`Масштаб ${Math.round(view.zoom * 100)}%, нажмите, чтобы вернуть 100%`}
                        onClick={() => {
                            zoomAt(1 / viewRef.current.zoom, [size.width / 2, size.height / 2]);
                            commitView();
                        }}
                    >
                        {Math.round(view.zoom * 100)}%
                    </button>
                    <button type="button" className="board-props__btn" aria-label="Увеличить масштаб" title="Увеличить масштаб (колёсико мыши)" onClick={() => zoomBy(1.2)}>
                        <Plus size={14} aria-hidden="true" />
                    </button>
                    <button type="button" className="board-props__btn" aria-label="Показать всё" title="Подогнать масштаб, чтобы всё на доске поместилось на экран" onClick={fitToContent}>
                        <Maximize size={14} aria-hidden="true" />
                    </button>
                </div>


            {stickerEditor && (
                <BoardStickerDialog
                    key={stickerEditor.mode === "edit" ? `${stickerEditor.sticker.id}:${stickerEditor.sticker.revision}` : "new"}
                    projectId={projectId}
                    sticker={stickerEditor.mode === "edit" ? stickerEditor.sticker : null}
                    tasks={tasks}
                    tasksLoading={tasksLoading}
                    isSaving={stickerSaving}
                    error={stickerError}
                    onClose={() => {
                        if (!stickerSaving) setStickerEditor(null);
                    }}
                    onSave={(input) => void saveSticker(input)}
                />
            )}

            {deleteRequest && (
                <Modal
                    isOpen
                    onOpenChange={(open) => {
                        if (!open && !deleting) setDeleteRequest(null);
                    }}
                    isDismissable={!deleting}
                    title={deleteRequest.stickerKeys.length === 1 && deleteRequest.shapeKeys.length === 0 ? "Удалить стикер?" : "Удалить выделенное?"}
                    description={deleteRequest.shapeKeys.length > 0
                        ? `Стикеров: ${deleteRequest.stickerKeys.length}. Фигур: ${deleteRequest.shapeKeys.length}. Всё выделение можно вернуть через Ctrl+Z.`
                        : "Удаление можно отменить через Ctrl+Z."}
                    footer={
                        <>
                            <Button disabled={deleting} onClick={() => setDeleteRequest(null)}>Отмена</Button>
                            <Button variant="destructive" disabled={deleting} onClick={() => void confirmDelete()}>
                                {deleting ? "Удаление…" : "Удалить"}
                            </Button>
                        </>
                    }
                >
                    {deleteRequest.stickerKeys.slice(0, 3).map((key) => {
                        const object = scene.get(key);
                        return object?.type === "sticker"
                            ? <p key={key} className="mb-2 line-clamp-3 whitespace-pre-wrap text-[13px] text-secondary">{object.sticker.body}</p>
                            : null;
                    })}
                    {deleteError && <p role="alert" className="mt-3 text-[12px] text-danger">{deleteError}</p>}
                </Modal>
            )}
        </div>
    );
}
