import { memo } from "react";
import { shapePath } from "@/lib/board/geometry";
import type { ShapeObject, StickerObject } from "@/lib/board/scene";
import type { BoardSticker } from "@/lib/board/stickers";
import { FONT_SIZE_PX, fillVar, strokeVar } from "@/lib/board/types";
import type { ProjectMember, Task } from "@/lib/types";
import { BoardStickerCard } from "@/components/board/BoardStickerCard";
import { connectorLayout, shapeInset } from "@/components/board/boardDom";

/** Регистрация DOM-элемента объекта: жесты двигают его напрямую. */
export type RegisterElement = (key: string, element: HTMLElement | null) => void;

interface ShapeViewProps {
    object: ShapeObject;
    selected: boolean;
    locked?: boolean;
    editing: boolean;
    register: RegisterElement;
}

function dashArray(style: ShapeObject["style"]): string | undefined {
    return style.dash === "dashed" ? `${style.stroke_width * 4} ${style.stroke_width * 3}` : undefined;
}

/**
 * Фигура: свой маленький svg по рамке, подпись — HTML поверх (перенос строк внутри).
 * Перерисовывается только когда меняется сам объект (memo по ссылке — новая ссылка
 * появляется лишь при фиксации изменения).
 */
export const ShapeView = memo(function ShapeView({ object, selected, locked, editing, register }: ShapeViewProps) {
    const { style } = object;
    const hasOutline = style.stroke !== "none" || style.fill !== "none";
    return (
        <div
            ref={(element) => register(object.key, element)}
            data-key={object.key}
            data-kind={object.kind}
            data-selected={selected || undefined}
            data-locked={locked || undefined}
            className={`board-obj board-shape board-shape--${object.kind}`}
            style={{ left: object.x, top: object.y, width: object.w, height: object.h, zIndex: object.z }}
        >
            <svg className="board-shape__svg" width={object.w} height={object.h} aria-hidden="true">
                <path
                    data-part="outline"
                    d={shapePath(object.kind, object.w, object.h, shapeInset(style.stroke_width))}
                    style={{
                        fill: fillVar(style.fill),
                        stroke: hasOutline ? strokeVar(style.stroke) : "transparent",
                        strokeWidth: style.stroke_width,
                        strokeDasharray: dashArray(style),
                    }}
                />
            </svg>
            {!editing && object.label && (
                <div
                    className={`board-shape__label board-shape__label--${style.text_align}`}
                    style={{ fontSize: FONT_SIZE_PX[style.font_size] }}
                >
                    {object.label}
                </div>
            )}
            {!editing && !object.label && object.kind === "text" && (
                <div className="board-shape__label board-shape__label--placeholder" style={{ fontSize: FONT_SIZE_PX[style.font_size] }}>
                    Текст
                </div>
            )}
        </div>
    );
});

interface ConnectorViewProps {
    object: ShapeObject;
    selected: boolean;
    locked?: boolean;
    editing: boolean;
    register: RegisterElement;
}

/** Стрелка или линия: svg по рамке точек с запасом, подпись посередине на подложке. */
export const ConnectorView = memo(function ConnectorView({ object, selected, locked, editing, register }: ConnectorViewProps) {
    const points = object.points ?? [[object.x, object.y], [object.x + object.w, object.y + object.h]];
    const layout = connectorLayout(points, object.style);
    const { style } = object;
    const stroke = style.stroke === "none" ? "var(--board-stroke-neutral)" : strokeVar(style.stroke);
    return (
        <div
            ref={(element) => register(object.key, element)}
            data-key={object.key}
            data-kind={object.kind}
            data-selected={selected || undefined}
            data-locked={locked || undefined}
            className="board-obj board-connector"
            style={{ left: layout.left, top: layout.top, width: layout.width, height: layout.height, zIndex: object.z }}
        >
            <svg width={layout.width} height={layout.height} aria-hidden="true">
                <path data-part="halo" d={layout.line} className="board-connector__halo" />
                <path data-part="hit" d={layout.line} className="board-connector__hit" />
                <path
                    data-part="line"
                    d={layout.line}
                    className="board-connector__line"
                    style={{ stroke, strokeWidth: style.stroke_width, strokeDasharray: dashArray(style) }}
                />
                <path data-part="head-start" d={layout.startHead} className="board-connector__head" style={{ fill: stroke, stroke }} />
                <path data-part="head-end" d={layout.endHead} className="board-connector__head" style={{ fill: stroke, stroke }} />
            </svg>
            <div
                data-part="label"
                className="board-connector__label"
                hidden={editing || !object.label}
                style={{ left: layout.label[0], top: layout.label[1], fontSize: FONT_SIZE_PX[style.font_size] }}
            >
                {object.label}
            </div>
        </div>
    );
});

interface StickerViewProps {
    object: StickerObject;
    selected: boolean;
    locked?: boolean;
    readOnly: boolean;
    readOnlyHint?: string;
    members: ProjectMember[];
    tasksById: Map<number, Task>;
    register: RegisterElement;
    onOpenTask: (taskId: number) => void;
    onEdit: (sticker: BoardSticker, key: string) => void;
    onDelete: (sticker: BoardSticker, key: string) => void;
}

/** Стикер встраивается в доску как есть — та же карточка, что и раньше. */
export const StickerView = memo(function StickerView({
    object,
    selected,
    locked,
    readOnly,
    readOnlyHint,
    members,
    tasksById,
    register,
    onOpenTask,
    onEdit,
    onDelete,
}: StickerViewProps) {
    return (
        <div
            ref={(element) => register(object.key, element)}
            data-key={object.key}
            data-kind="sticker"
            data-selected={selected || undefined}
            data-locked={locked || undefined}
            className="board-obj board-sticker project-sticker-slot"
            style={{ left: object.x, top: object.y, width: object.w, height: object.h, zIndex: object.z }}
        >
            <BoardStickerCard
                sticker={object.sticker}
                members={members}
                tasksById={tasksById}
                readOnly={readOnly}
                readOnlyHint={readOnlyHint}
                onOpenTask={onOpenTask}
                onEdit={sticker => onEdit(sticker, object.key)}
                onDelete={sticker => onDelete(sticker, object.key)}
            />
        </div>
    );
});
