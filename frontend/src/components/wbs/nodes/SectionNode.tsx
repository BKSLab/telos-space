import { useEffect, useRef, useState } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { AlertTriangle, ChevronDown, ChevronRight, MoreHorizontal } from "lucide-react";
import { cn } from "@/lib/cn";
import type { WbsTreeNode } from "@/lib/wbsTree";
import { ProgressBar } from "@/components/ui/Progress";

export interface SectionNodeData {
    section: WbsTreeNode;
    isCollapsed: boolean;
    hiddenSections: number;
    hiddenTasks: number;
    isDropTarget: boolean;
    /** Показывает результат переноса до отпускания мыши (§30 ТЗ). */
    dropZone: "before" | "inside" | "after" | null;
    isEditing: boolean;
    /** Раздел предложен ИИ и ещё не сохранён в проекте. */
    isDraft: boolean;
    /** Уровень детализации из semantic zoom: при отдалении прячем вторичное. */
    detail: "full" | "compact" | "minimal";
    onToggleCollapse: (nodeId: number) => void;
    onRename: (nodeId: number, title: string) => void;
    onCancelRename: () => void;
    onOpenMenu: (nodeId: number, anchor: { x: number; y: number }) => void;
}

/**
 * Структурный узел ИСР (§20 ТЗ): вычисленный номер, название, число задач
 * и агрегированный прогресс. Уровни различаются размером и типографикой,
 * но не превращаются в отдельные дизайны (§21).
 */
export function SectionNode({ data, selected }: NodeProps) {
    const {
        section,
        isCollapsed,
        hiddenSections,
        hiddenTasks,
        isDropTarget,
        dropZone,
        isEditing,
        isDraft,
        detail,
        onToggleCollapse,
        onRename,
        onCancelRename,
        onOpenMenu,
    } = data as unknown as SectionNodeData;

    const [draft, setDraft] = useState(section.node.title);
    const inputRef = useRef<HTMLInputElement>(null);
    const isRoot = section.depth === 0;
    const { total, done, overdue } = section.progress;
    const hasChildren = section.children.length > 0 || section.tasks.length > 0;

    useEffect(() => {
        if (isEditing) {
            inputRef.current?.focus();
            inputRef.current?.select();
        }
    }, [isEditing]);

    return (
        <div
            onContextMenu={(event) => {
                event.preventDefault();
                onOpenMenu(section.node.id, { x: event.clientX, y: event.clientY });
            }}
            className={cn(
                "group/section flex h-full w-full flex-col justify-between rounded-[var(--radius-card)] border bg-surface-2 px-3 py-2.5",
                "transition-[background-color,border-color,box-shadow] duration-[var(--duration-normal)]",
                "ease-[var(--ease-standard)] shadow-card",
                isDropTarget
                    ? "border-accent/70 bg-accent/[0.08] shadow-selected"
                    : selected
                      ? "material-metal border-accent/60 shadow-selected"
                      : isDraft
                        ? "border-dashed border-accent/50 bg-accent/[0.05]"
                        : "border-line-subtle hover:border-line hover:bg-elevated",
            )}
        >
            <Handle
                type="target"
                id="top"
                position={Position.Top}
                className="!opacity-0"
                isConnectable={false}
            />
            <Handle
                type="target"
                id="left"
                position={Position.Left}
                className="!opacity-0"
                isConnectable={false}
            />
            <Handle
                type="source"
                id="right"
                position={Position.Right}
                className="!opacity-0"
                isConnectable={false}
            />
            {/*
             * Из этой точки тянут стрелку к задаче: связь и означает привязку.
             * Точка видна всегда — иначе про неё невозможно догадаться.
             */}
            <Handle
                type="source"
                id="bottom"
                position={Position.Bottom}
                isConnectableEnd={false}
                title="Потяните к задаче, чтобы привязать её к разделу"
                className={cn(
                    "!size-3 !cursor-crosshair !border-2 !border-surface-2 !bg-accent",
                    "transition-[opacity,transform] duration-[var(--duration-fast)]",
                    isDraft
                        ? "!opacity-0"
                        : "!opacity-70 group-hover/section:!scale-125 group-hover/section:!opacity-100",
                )}
            />

            {(dropZone === "before" || dropZone === "after") && (
                <span
                    aria-hidden="true"
                    className={cn(
                        "absolute right-0 left-0 h-0.5 rounded-full bg-accent",
                        dropZone === "before" ? "-top-1" : "-bottom-1",
                    )}
                />
            )}

            <div className="flex min-w-0 items-start gap-1.5">
                {hasChildren && (
                    <button
                        type="button"
                        aria-label={isCollapsed ? "Раскрыть ветку" : "Свернуть ветку"}
                        aria-expanded={!isCollapsed}
                        onClick={(event) => {
                            event.stopPropagation();
                            onToggleCollapse(section.node.id);
                        }}
                        className="wbs-section-collapse nodrag nopan mt-0.5 shrink-0 rounded-sm p-0.5 text-muted hover:bg-hover hover:text-primary"
                    >
                        {isCollapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
                    </button>
                )}

                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="font-mono text-[10px] tracking-[0.04em] text-muted">
                        {section.number}
                    </span>
                    {isEditing ? (
                        <input
                            ref={inputRef}
                            value={draft}
                            aria-label="Название раздела"
                            onChange={(event) => setDraft(event.target.value)}
                            onBlur={() => onRename(section.node.id, draft)}
                            onKeyDown={(event) => {
                                if (event.key === "Enter") {
                                    onRename(section.node.id, draft);
                                }
                                if (event.key === "Escape") {
                                    setDraft(section.node.title);
                                    onCancelRename();
                                }
                            }}
                            className="nodrag nopan w-full rounded-sm border border-accent/70 bg-elevated px-1 py-0.5 text-[13px] text-primary outline-none shadow-focus"
                        />
                    ) : (
                        <h3
                            className={cn(
                                "line-clamp-2 leading-snug break-words text-primary",
                                isRoot ? "text-[14px] font-semibold" : "text-[13px] font-medium",
                            )}
                        >
                            {section.node.title}
                        </h3>
                    )}
                </div>

                {!isEditing && (
                    <button
                        type="button"
                        aria-label={`Действия раздела ${section.node.title}`}
                        onClick={(event) => {
                            event.stopPropagation();
                            onOpenMenu(section.node.id, {
                                x: event.clientX,
                                y: event.clientY,
                            });
                        }}
                        className="wbs-section-menu nodrag nopan shrink-0 rounded-sm p-0.5 text-muted hover:bg-hover hover:text-primary"
                    >
                        <MoreHorizontal size={13} />
                    </button>
                )}
            </div>

            {detail !== "minimal" && (
                <div className="flex flex-col gap-1">
                    <div className="flex items-center justify-between gap-2 text-[10px]">
                        <span className="text-muted">
                            {isCollapsed && hiddenSections > 0
                                ? `${hiddenSections} разделов · ${hiddenTasks} задач`
                                : `${total} задач`}
                            {overdue > 0 && (
                                <span className="ml-1 inline-flex items-center gap-0.5 text-danger">
                                    <AlertTriangle size={9} aria-hidden="true" />
                                    {overdue}
                                </span>
                            )}
                        </span>
                        <span className="font-mono text-secondary">
                            {total === 0 ? "—" : `${Math.round((done / total) * 100)}%`}
                        </span>
                    </div>
                    <ProgressBar
                        value={total === 0 ? 0 : done / total}
                        label={`Прогресс раздела ${section.node.title}`}
                        className="h-1"
                    />
                </div>
            )}
        </div>
    );
}
