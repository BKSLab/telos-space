import type { BoardEditorState } from "@/lib/board/metadata";
import type { BoardStickerCreateInput, BoardSticker } from "@/lib/board/stickers";

/** Доски проекта и фигуры на них — по контракту docs/boards-contract.md. */

export interface Board {
    id: number;
    project_id: number | null;
    title: string;
    position: number;
    revision: number;
    created_by_user_id: number | null;
    created_at: string;
    updated_at: string;
}

export type ShapeKind = "rect" | "ellipse" | "diamond" | "text" | "arrow" | "line";
export type PaletteColor = "neutral" | "yellow" | "blue" | "green" | "red" | "violet" | "none";
export type Anchor = "top" | "right" | "bottom" | "left" | "center";
export type StrokeWidth = 1 | 2 | 4;
export type FontSize = "s" | "m" | "l";

export interface ShapeStyle {
    stroke: PaletteColor;
    fill: PaletteColor;
    stroke_width: StrokeWidth;
    dash: "solid" | "dashed";
    route: "straight" | "orthogonal";
    start_head: "none" | "arrow";
    end_head: "none" | "arrow";
    font_size: FontSize;
    text_align: "left" | "center";
}

export type Point = [number, number];

/** Ссылка конца стрелки на объект доски в формате сервера. */
export interface ServerRef {
    type: "sticker" | "shape";
    id?: number;
    client_id?: string;
    anchor: Anchor;
}

export interface BoardShape {
    id: number;
    board_id: number;
    kind: ShapeKind;
    x: number;
    y: number;
    w: number;
    h: number;
    points: Point[] | null;
    start_ref: ServerRef | null;
    end_ref: ServerRef | null;
    style: ShapeStyle;
    label: string;
    z_index: number;
    created_by_user_id: number | null;
    revision: number;
    created_at: string;
    updated_at: string;
}

export interface BoardContent {
    board: Board;
    stickers: BoardSticker[];
    shapes: BoardShape[];
}

/**
 * Данные фигуры для записи. Фигуре и надписи — рамка (x, y, w, h), стрелке
 * и линии — точки и привязки: их рамку сервер считает сам. В обновлении
 * вида (kind) нет — он не меняется.
 */
export interface ShapeData {
    kind?: ShapeKind;
    x?: number;
    y?: number;
    w?: number;
    h?: number;
    points?: Point[];
    start_ref?: ServerRef | null;
    end_ref?: ServerRef | null;
    style: ShapeStyle;
    label: string;
    z_index: number;
}

export type BatchOp =
    | { op: "create_shape"; client_id: string; data: ShapeData }
    | { op: "update_shape"; id: number; revision?: number; data: ShapeData }
    | { op: "delete_shape"; id: number; revision?: number }
    | { op: "create_sticker"; client_id: string; data: BoardStickerCreateInput }
    | { op: "update_sticker"; id: number; revision: number; data: BoardStickerCreateInput }
    | { op: "delete_sticker"; id: number; revision: number }
    | { op: "move_sticker"; id: number; data: { canvas_x: number; canvas_y: number; width?: number; height?: number; z_index?: number } };

export interface BatchEditorState { expected_revision: number; metadata: Record<string, unknown> }

export interface BatchResponse {
    shapes: BoardShape[];
    deleted_shape_ids: number[];
    deleted_sticker_ids?: number[];
    editor_state?: BoardEditorState | null;
    stickers: BoardSticker[];
    id_map: Record<string, number>;
}

export interface ToStickerResponse {
    sticker: BoardSticker;
    updated_shapes: BoardShape[];
}

export const PALETTE: { value: PaletteColor; label: string }[] = [
    { value: "neutral", label: "Нейтральный" },
    { value: "yellow", label: "Жёлтый" },
    { value: "blue", label: "Синий" },
    { value: "green", label: "Зелёный" },
    { value: "red", label: "Красный" },
    { value: "violet", label: "Фиолетовый" },
    { value: "none", label: "Нет" },
];

export const FONT_SIZE_PX: Record<FontSize, number> = { s: 12, m: 14, l: 18 };

/** Умолчания стиля так же, как их дополняет сервер. */
export function defaultStyle(kind: ShapeKind, patch: Partial<ShapeStyle> = {}): ShapeStyle {
    return {
        stroke: kind === "text" ? "none" : "neutral",
        fill: "none",
        stroke_width: 2,
        dash: "solid",
        route: "straight",
        start_head: "none",
        end_head: kind === "arrow" ? "arrow" : "none",
        font_size: "m",
        text_align: "center",
        ...patch,
    };
}

export function isConnectorKind(kind: ShapeKind): boolean {
    return kind === "arrow" || kind === "line";
}

/** CSS-переменные палитры (определены в app.css для обеих тем). */
export function strokeVar(color: PaletteColor): string {
    return color === "none" ? "transparent" : `var(--board-stroke-${color})`;
}

export function fillVar(color: PaletteColor): string {
    return color === "none" ? "transparent" : `var(--board-fill-${color})`;
}
