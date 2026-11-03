import { Redo2, Undo2 } from "lucide-react";
import { cn } from "@/lib/cn";
import { SHORTCUTS, TOOLS, type BoardTool } from "@/components/board/boardTools";
import { HelpHint } from "@/components/ui/HelpHint";

interface BoardToolbarProps {
    tool: BoardTool;
    locked: boolean;
    canUndo: boolean;
    canRedo: boolean;
    onSelectTool: (tool: BoardTool, lock: boolean) => void;
    onUndo: () => void;
    onRedo: () => void;
    horizontal?: boolean;
}

/** Панель инструментов слева на холсте. Подсказка каждой кнопки — с клавишей. */
export function BoardToolbar({ tool, locked, canUndo, canRedo, onSelectTool, onUndo, onRedo, horizontal = false }: BoardToolbarProps) {
    return (
        <div className="board-toolbar" role="toolbar" aria-label="Инструменты доски" aria-orientation={horizontal ? "horizontal" : "vertical"}>
            {TOOLS.map((item) => {
                const active = tool === item.tool;
                return (
                    <button
                        key={item.tool}
                        type="button"
                        className={cn("board-tool", active && "board-tool--active", active && locked && "board-tool--locked")}
                        aria-pressed={active}
                        aria-label={`${item.label} (${item.key})`}
                        title={active && locked
                            ? `${item.label} закреплён — можно рисовать подряд. Esc или V — выйти`
                            : `${item.label} — ${item.key}. ${item.hint}. Двойной клик — оставить инструмент включённым`}
                        data-tool={item.tool}
                        onClick={() => onSelectTool(item.tool, false)}
                        onDoubleClick={() => onSelectTool(item.tool, true)}
                    >
                        {item.icon}
                        <span className="board-tool__label" aria-hidden="true">{item.tool === "hand" ? "Холст" : item.tool === "select" ? "Выбор" : item.tool === "rect" ? "Фигура" : item.label}</span>
                        <span className="board-tool__key" aria-hidden="true">{item.key}</span>
                    </button>
                );
            })}
            <span className="board-toolbar__sep" aria-hidden="true" />
            <button type="button" className="board-tool" disabled={!canUndo} aria-label="Отменить" title="Отменить — Ctrl+Z" onClick={onUndo}>
                <Undo2 size={16} aria-hidden="true" />
            </button>
            <button type="button" className="board-tool" disabled={!canRedo} aria-label="Повторить" title="Повторить — Ctrl+Shift+Z или Ctrl+Y" onClick={onRedo}>
                <Redo2 size={16} aria-hidden="true" />
            </button>
            <span className="board-toolbar__help">
                <HelpHint label="Горячие клавиши доски" title="Горячие клавиши и жесты доски">
                    <span className="board-shortcuts">
                        {SHORTCUTS.map((item) => (
                            <span key={item.keys} className="board-shortcuts__row">
                                <kbd>{item.keys}</kbd>
                                <span>{item.action}</span>
                            </span>
                        ))}
                    </span>
                </HelpHint>
            </span>
        </div>
    );
}
