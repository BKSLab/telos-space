import { useState } from "react";
import {
    BringToFront,
    Copy,
    CornerDownRight,
    MoveRight,
    Pencil,
    GitBranch,
    SendToBack,
    StickyNote,
    Trash2,
} from "lucide-react";
import { cn } from "@/lib/cn";
import { isConnectorKind, PALETTE, fillVar, strokeVar, type PaletteColor, type ShapeStyle } from "@/lib/board/types";
import type { SceneObject, ShapeObject } from "@/lib/board/scene";
import type { ConnectorAction } from "@/lib/board/connectorEditing";

interface BoardPropsPanelProps {
    selected: SceneObject[];
    position: { left: number; top: number };
    busy: boolean;
    onStyle: (patch: Partial<ShapeStyle>) => void;
    onLayer: (direction: "front" | "back") => void;
    onToSticker: () => void;
    onDelete: () => void;
    onDuplicate: () => void;
    onEditLabel: () => void;
    onConnectorAction: (action: ConnectorAction) => void;
}

function ColorPicker({ label, value, kind, allowNone, onChange }: {
    label: string;
    value: PaletteColor;
    kind: "stroke" | "fill";
    allowNone: boolean;
    onChange: (value: PaletteColor) => void;
}) {
    const [open, setOpen] = useState(false);
    const swatch = (color: PaletteColor) => (kind === "stroke" ? strokeVar(color) : fillVar(color));
    const current = PALETTE.find((item) => item.value === value)?.label ?? value;
    return (
        <span className="board-props__picker">
            <button
                type="button"
                className="board-props__btn"
                aria-label={`${label}: ${current}`}
                title={`${label}: ${current}`}
                aria-expanded={open}
                onClick={() => setOpen((state) => !state)}
            >
                <span className={cn("board-swatch", kind === "stroke" && "board-swatch--ring", value === "none" && "board-swatch--none")} style={{ ["--swatch" as string]: swatch(value) }} />
            </button>
            {open && (
                <span className="board-props__palette" role="group" aria-label={label}>
                    {PALETTE.filter((item) => allowNone || item.value !== "none").map((item) => (
                        <button
                            key={item.value}
                            type="button"
                            className={cn("board-props__btn", item.value === value && "board-props__btn--active")}
                            aria-label={`${label}: ${item.label}`}
                            title={`${label}: ${item.label}`}
                            aria-pressed={item.value === value}
                            onClick={() => {
                                onChange(item.value);
                                setOpen(false);
                            }}
                        >
                            <span className={cn("board-swatch", kind === "stroke" && "board-swatch--ring", item.value === "none" && "board-swatch--none")} style={{ ["--swatch" as string]: swatch(item.value) }} />
                        </button>
                    ))}
                </span>
            )}
        </span>
    );
}

/**
 * Плавающая панель свойств над выделением. Показывает только то, что применимо
 * к выделенному: у стрелок — наконечники и маршрут, у фигур — заливка и «Заменить на стикер».
 */
export function BoardPropsPanel({ selected, position, busy, onStyle, onLayer, onToSticker, onDelete, onDuplicate, onEditLabel, onConnectorAction }: BoardPropsPanelProps) {
    const [connectorMenu, setConnectorMenu] = useState(false);
    const shapes = selected.filter((object): object is ShapeObject => object.type === "shape");
    const connectors = shapes.filter((shape) => isConnectorKind(shape.kind));
    const nodes = shapes.filter((shape) => !isConnectorKind(shape.kind));
    const style = shapes[0]?.style;
    const single = selected.length === 1 ? selected[0] : null;
    const canToSticker = single?.type === "shape" && !isConnectorKind(single.kind);

    return (
        <div
            className="board-props"
            role="toolbar"
            aria-label="Свойства выделенного"
            style={{ left: position.left, top: position.top }}
            onPointerDown={(event) => event.stopPropagation()}
        >
            {style && (
                <>
                    <ColorPicker label="Обводка" kind="stroke" value={style.stroke} allowNone={connectors.length === 0} onChange={(stroke) => onStyle({ stroke })} />
                    {nodes.length > 0 && (
                        <ColorPicker label="Заливка" kind="fill" value={style.fill} allowNone onChange={(fill) => onStyle({ fill })} />
                    )}
                    <span className="board-props__group" role="group" aria-label="Толщина линии">
                        {([1, 2, 4] as const).map((width) => (
                            <button
                                key={width}
                                type="button"
                                className={cn("board-props__btn", style.stroke_width === width && "board-props__btn--active")}
                                aria-pressed={style.stroke_width === width}
                                title={`Толщина ${width}px`}
                                aria-label={`Толщина ${width}px`}
                                onClick={() => onStyle({ stroke_width: width })}
                            >
                                <span className="board-props__stroke" style={{ height: width }} />
                            </button>
                        ))}
                    </span>
                    <button
                        type="button"
                        className={cn("board-props__btn", style.dash === "dashed" && "board-props__btn--active")}
                        aria-pressed={style.dash === "dashed"}
                        title={style.dash === "dashed" ? "Пунктир — нажмите, чтобы сделать сплошной" : "Сделать пунктиром"}
                        aria-label="Пунктир"
                        onClick={() => onStyle({ dash: style.dash === "dashed" ? "solid" : "dashed" })}
                    >
                        <span className="board-props__dash" />
                    </button>
                    {connectors.length > 0 && (
                        <>
                            <span className="board-props__sep" aria-hidden="true" />
                            <button
                                type="button"
                                className={cn("board-props__btn", style.start_head === "arrow" && "board-props__btn--active")}
                                aria-pressed={style.start_head === "arrow"}
                                title="Наконечник в начале"
                                aria-label="Наконечник в начале"
                                onClick={() => onStyle({ start_head: style.start_head === "arrow" ? "none" : "arrow" })}
                            >
                                <MoveRight size={14} className="rotate-180" aria-hidden="true" />
                            </button>
                            <button
                                type="button"
                                className={cn("board-props__btn", style.end_head === "arrow" && "board-props__btn--active")}
                                aria-pressed={style.end_head === "arrow"}
                                title="Наконечник в конце"
                                aria-label="Наконечник в конце"
                                onClick={() => onStyle({ end_head: style.end_head === "arrow" ? "none" : "arrow" })}
                            >
                                <MoveRight size={14} aria-hidden="true" />
                            </button>
                            <button
                                type="button"
                                className={cn("board-props__btn", style.route === "orthogonal" && "board-props__btn--active")}
                                aria-pressed={style.route === "orthogonal"}
                                title={style.route === "orthogonal" ? "Ломаная под прямым углом — нажмите, чтобы сделать прямой" : "Сделать ломаной под прямым углом"}
                                aria-label="Ломаная под прямым углом"
                                onClick={() => onStyle({ route: style.route === "orthogonal" ? "straight" : "orthogonal" })}
                            >
                                <CornerDownRight size={14} aria-hidden="true" />
                            </button>
                        </>
                    )}
                    <span className="board-props__sep" aria-hidden="true" />
                    <span className="board-props__group" role="group" aria-label="Размер шрифта подписи">
                        {(["s", "m", "l"] as const).map((size) => (
                            <button
                                key={size}
                                type="button"
                                className={cn("board-props__btn board-props__btn--text", style.font_size === size && "board-props__btn--active")}
                                aria-pressed={style.font_size === size}
                                title={`Шрифт подписи: ${size === "s" ? "мелкий" : size === "m" ? "средний" : "крупный"}`}
                                aria-label={`Шрифт ${size.toUpperCase()}`}
                                onClick={() => onStyle({ font_size: size })}
                            >
                                {size.toUpperCase()}
                            </button>
                        ))}
                    </span>
                </>
            )}
            {single && (
                <button type="button" className="board-props__btn" disabled={busy} aria-label={single.type === "sticker" ? "Редактировать стикер" : "Редактировать подпись"} title="Добавить или изменить текст"
                    onClick={() => { setConnectorMenu(false); onEditLabel(); }}><Pencil size={14} aria-hidden="true" /></button>
            )}
            {single?.type === "shape" && isConnectorKind(single.kind) && (
                <span className="board-props__picker">
                    <button type="button" className="board-props__btn" disabled={busy} aria-label="Редактировать связь" aria-expanded={connectorMenu}
                        title="Направление, маршрут и привязки связи" onClick={() => setConnectorMenu(open => !open)}><GitBranch size={14} aria-hidden="true" /></button>
                    {connectorMenu && <span className="board-props__connector-menu" role="group" aria-label="Редактирование связи">
                        <span className="board-search__hint">Перетаскивайте точки и середины сегментов, чтобы менять изгибы.</span>
                        {([
                            ["reverse", "Обратить направление"],
                            ["reset-route", "Перестроить маршрут"],
                            ["detach-start", "Отцепить начало"],
                            ["detach-end", "Отцепить конец"],
                        ] as const).map(([action, label]) => (
                            <button key={action} type="button" className="board-button" disabled={busy || (action === "detach-start" && !single.startRef) || (action === "detach-end" && !single.endRef)}
                                onClick={() => { onConnectorAction(action); setConnectorMenu(false); }}>{label}</button>
                        ))}
                    </span>}
                </span>
            )}
            {style && <span className="board-props__sep" aria-hidden="true" />}
            <button type="button" className="board-props__btn" disabled={busy} title="Дублировать — Ctrl+D" aria-label="Дублировать выделенное" onClick={onDuplicate}>
                <Copy size={14} aria-hidden="true" />
            </button>
            <button type="button" className="board-props__btn" title="На передний план — Ctrl+]" aria-label="На передний план" onClick={() => onLayer("front")}>
                <BringToFront size={14} aria-hidden="true" />
            </button>
            <button type="button" className="board-props__btn" title="На задний план — Ctrl+[" aria-label="На задний план" onClick={() => onLayer("back")}>
                <SendToBack size={14} aria-hidden="true" />
            </button>
            {canToSticker && (
                <button
                    type="button"
                    className="board-props__btn board-props__btn--wide"
                    disabled={busy}
                    title="Поставить на место фигуры стикер: подпись станет его текстом, стрелки останутся прицеплены. Цвет и форма фигуры пропадут. Ctrl+Z отменяет замену"
                    onClick={onToSticker}
                >
                    <StickyNote size={14} aria-hidden="true" />
                    <span>Заменить на стикер</span>
                </button>
            )}
            <button type="button" className="board-props__btn board-props__btn--danger" title="Удалить — Delete" aria-label="Удалить выделенное" onClick={onDelete}>
                <Trash2 size={14} aria-hidden="true" />
            </button>
        </div>
    );
}
