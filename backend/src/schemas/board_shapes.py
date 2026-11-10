"""Контракт фигур доски: запись, ответ, пакет операций.

Проверка формы (обязательные поля по виду фигуры, диапазоны, стиль) живёт
здесь; всё, что требует базы — существование объекта ссылки, лимит фигур,
ревизии, — проверяет сервис.
"""

from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.db.models.board_shapes import LINEAR_KINDS, BoardShapeKind
from src.schemas.board_editor_batch import BoardBatchEditorInsert, BoardBatchEditorState
from src.schemas.board_stickers import (
    BoardStickerCreateSchema,
    BoardStickerPositionUpdateSchema,
    BoardStickerSchema,
    StickerColor,
)

MAX_SHAPE_COORDINATE = 1_000_000.0
MIN_SHAPE_SIZE = 1.0
MAX_SHAPE_SIZE = 20_000.0
MIN_LINE_POINTS = 2
MAX_LINE_POINTS = 50
MAX_SHAPE_LABEL_LENGTH = 2000
MAX_Z_INDEX = 1_000_000_000
MAX_SHAPES_PER_BOARD = 2000
MAX_BATCH_OPS = 500
MAX_CLIENT_ID_LENGTH = 64

ShapeColor = Literal["neutral", "yellow", "blue", "green", "red", "violet", "none"]
ShapeAnchor = Literal["top", "right", "bottom", "left", "center"]
Coordinate = Annotated[
    float, Field(ge=-MAX_SHAPE_COORDINATE, le=MAX_SHAPE_COORDINATE, allow_inf_nan=False)
]
ShapeSize = Annotated[float, Field(ge=MIN_SHAPE_SIZE, le=MAX_SHAPE_SIZE, allow_inf_nan=False)]
ZIndex = Annotated[int, Field(ge=-MAX_Z_INDEX, le=MAX_Z_INDEX)]
Point = tuple[Coordinate, Coordinate]
Points = Annotated[list[Point], Field(min_length=MIN_LINE_POINTS, max_length=MAX_LINE_POINTS)]
ClientId = Annotated[str, Field(min_length=1, max_length=MAX_CLIENT_ID_LENGTH)]


class BoardShapeStyleInput(BaseModel):
    """Стиль при записи: любое поле можно опустить, сервер дополнит умолчанием."""

    model_config = ConfigDict(extra="forbid")

    stroke: ShapeColor | None = None
    fill: ShapeColor | None = None
    stroke_width: Literal[1, 2, 4] | None = None
    dash: Literal["solid", "dashed"] | None = None
    route: Literal["straight", "orthogonal"] | None = None
    start_head: Literal["none", "arrow"] | None = None
    end_head: Literal["none", "arrow"] | None = None
    font_size: Literal["s", "m", "l"] | None = None
    text_align: Literal["left", "center"] | None = None


class BoardShapeStyleSchema(BaseModel):
    """Полный стиль фигуры в ответе."""

    stroke: ShapeColor
    fill: ShapeColor
    stroke_width: Literal[1, 2, 4]
    dash: Literal["solid", "dashed"]
    route: Literal["straight", "orthogonal"]
    start_head: Literal["none", "arrow"]
    end_head: Literal["none", "arrow"]
    font_size: Literal["s", "m", "l"]
    text_align: Literal["left", "center"]


def default_style(kind: BoardShapeKind) -> dict[str, Any]:
    """Умолчания стиля: у стрелки наконечник на конце, у остальных — нет."""
    return {
        "stroke": "neutral",
        "fill": "none",
        "stroke_width": 2,
        "dash": "solid",
        "route": "straight",
        "start_head": "none",
        "end_head": "arrow" if kind is BoardShapeKind.ARROW else "none",
        "font_size": "m",
        "text_align": "center",
    }


def complete_style(
    kind: BoardShapeKind,
    changes: BoardShapeStyleInput | None,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Накладывает переданные поля стиля на текущий стиль или на умолчания."""
    style = {**default_style(kind), **(current or {})}
    if changes is not None:
        style.update(changes.model_dump(exclude_none=True))
    return BoardShapeStyleSchema.model_validate(style).model_dump()


class BoardShapeRefSchema(BaseModel):
    """Привязка конца стрелки к объекту той же доски."""

    type: Literal["sticker", "shape"]
    id: int
    anchor: ShapeAnchor = "center"


class BoardShapeRefInput(BaseModel):
    """Привязка при записи; в пакете фигура может быть задана client_id."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["sticker", "shape"]
    id: int | None = Field(None, gt=0)
    client_id: ClientId | None = None
    anchor: ShapeAnchor = "center"

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        """Ровно один из id и client_id; client_id бывает только у фигуры."""
        if (self.id is None) == (self.client_id is None):
            raise ValueError("В привязке нужен ровно один из id или client_id.")
        return self


class BoardShapeCreateSchema(BaseModel):
    """Новая фигура: рамка для фигур и надписей, точки — для стрелок и линий."""

    model_config = ConfigDict(extra="forbid")

    kind: BoardShapeKind
    x: Coordinate | None = None
    y: Coordinate | None = None
    w: float | None = Field(None, allow_inf_nan=False)
    h: float | None = Field(None, allow_inf_nan=False)
    points: Points | None = None
    start_ref: BoardShapeRefInput | None = None
    end_ref: BoardShapeRefInput | None = None
    style: BoardShapeStyleInput | None = None
    label: str = Field("", max_length=MAX_SHAPE_LABEL_LENGTH)
    z_index: ZIndex = 0

    @field_validator("label")
    @classmethod
    def reject_null_bytes(cls, value: str) -> str:
        """Нулевой байт PostgreSQL в тексте не хранит."""
        if "\x00" in value:
            raise ValueError("Подпись не должна содержать нулевые байты.")
        return value

    @model_validator(mode="after")
    def validate_kind(self) -> Self:
        """Стрелке и линии нужны точки, остальным — рамка и никаких привязок."""
        if self.kind in LINEAR_KINDS:
            if self.points is None:
                raise ValueError("Стрелке и линии нужны points.")
            return self
        if self.points is not None or self.start_ref is not None or self.end_ref is not None:
            raise ValueError("points и привязки бывают только у стрелки и линии.")
        if None in (self.x, self.y, self.w, self.h):
            raise ValueError("Фигуре нужны x, y, w и h.")
        for value in (self.w, self.h):
            if not MIN_SHAPE_SIZE <= value <= MAX_SHAPE_SIZE:
                raise ValueError(
                    f"Ширина и высота фигуры — от {MIN_SHAPE_SIZE:g} до {MAX_SHAPE_SIZE:g}."
                )
        return self


class BoardShapeChangesSchema(BaseModel):
    """Частичное изменение фигуры; вид фигуры не меняется.

    У стрелки и линии рамка считается по точкам: сдвиг x/y без points
    переносит все точки, а w/h без points не принимаются.
    """

    model_config = ConfigDict(extra="forbid")

    x: Coordinate | None = None
    y: Coordinate | None = None
    w: ShapeSize | None = None
    h: ShapeSize | None = None
    points: Points | None = None
    start_ref: BoardShapeRefInput | None = None
    end_ref: BoardShapeRefInput | None = None
    style: BoardShapeStyleInput | None = None
    label: str | None = Field(None, max_length=MAX_SHAPE_LABEL_LENGTH)
    z_index: ZIndex | None = None

    @model_validator(mode="after")
    def validate_changes(self) -> Self:
        """Требует изменение; null допустим только у привязок (отцепить)."""
        changed = self.model_fields_set - {"revision"}
        if not changed:
            raise ValueError("Нужно передать изменение фигуры.")
        nullable = {"start_ref", "end_ref"}
        for name in changed - nullable:
            if getattr(self, name) is None:
                raise ValueError(f"Поле {name} не может быть NULL.")
        if self.label is not None and "\x00" in self.label:
            raise ValueError("Подпись не должна содержать нулевые байты.")
        return self


class BoardShapeUpdateSchema(BoardShapeChangesSchema):
    """PATCH фигуры: revision необязателен — без него last-write-wins."""

    revision: int | None = Field(None, ge=1)


class BoardShapeSchema(BaseModel):
    """Фигура доски со всеми полями и полным стилем."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    board_id: int
    kind: BoardShapeKind
    x: float
    y: float
    w: float
    h: float
    points: list[list[float]] | None
    start_ref: BoardShapeRefSchema | None
    end_ref: BoardShapeRefSchema | None
    style: BoardShapeStyleSchema
    label: str
    z_index: int
    created_by_user_id: int | None
    revision: int
    created_at: datetime
    updated_at: datetime


class CreateShapeOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["create_shape"]
    client_id: ClientId | None = None
    data: BoardShapeCreateSchema


class UpdateShapeOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["update_shape"]
    id: int = Field(..., gt=0)
    revision: int | None = Field(None, ge=1)
    data: BoardShapeChangesSchema


class DeleteShapeOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["delete_shape"]
    id: int = Field(..., gt=0)
    revision: int | None = Field(None, ge=1)


class MoveStickerOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["move_sticker"]
    id: int = Field(..., gt=0)
    revision: int | None = Field(None, ge=1)
    data: BoardStickerPositionUpdateSchema


class CreateStickerOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["create_sticker"]
    client_id: ClientId
    data: BoardStickerCreateSchema


class UpdateStickerOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["update_sticker"]
    id: int = Field(..., gt=0)
    revision: int = Field(..., ge=1)
    data: BoardStickerCreateSchema


class DeleteStickerOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["delete_sticker"]
    id: int = Field(..., gt=0)
    revision: int = Field(..., ge=1)


BoardBatchOperation = Annotated[
    CreateShapeOperation
    | UpdateShapeOperation
    | DeleteShapeOperation
    | MoveStickerOperation
    | CreateStickerOperation
    | UpdateStickerOperation
    | DeleteStickerOperation,
    Field(discriminator="op"),
]


class BoardBatchSchema(BaseModel):
    """Пакет операций доски: выполняется целиком или не выполняется вовсе."""

    model_config = ConfigDict(extra="forbid")

    ops: list[BoardBatchOperation] = Field(..., min_length=0, max_length=MAX_BATCH_OPS)
    expected_revision: int | None = Field(None, ge=0)
    editor_insert: BoardBatchEditorInsert | None = None
    editor_state: BoardBatchEditorState | None = None

    @model_validator(mode="after")
    def unique_client_ids(self) -> Self:
        """client_id внутри пакета не повторяются."""
        if not self.ops and self.editor_state is None and self.editor_insert is None:
            raise ValueError("Нужны операции или изменение настроек редактора.")
        if self.editor_state is not None and self.editor_insert is not None:
            raise ValueError("Передайте только один вид изменения настроек редактора.")
        client_ids = [
            item.client_id
            for item in self.ops
            if isinstance(item, CreateShapeOperation | CreateStickerOperation)
            and item.client_id is not None
        ]
        if len(client_ids) != len(set(client_ids)):
            raise ValueError("client_id в пакете должны быть уникальны.")
        return self


class BoardBatchResultSchema(BaseModel):
    """Итог пакета: созданные и изменённые объекты, удалённые фигуры, id новых."""

    shapes: list[BoardShapeSchema]
    deleted_shape_ids: list[int]
    stickers: list[BoardStickerSchema]
    id_map: dict[str, int]
    deleted_sticker_ids: list[int] = Field(default_factory=list)
    editor_state: dict[str, Any] | None = None


class BoardShapeToStickerSchema(BaseModel):
    """Замена фигуры стикером."""

    model_config = ConfigDict(extra="forbid")

    revision: int | None = Field(None, ge=1)
    color: StickerColor | None = None


class BoardShapeToStickerResultSchema(BaseModel):
    """Новый стикер и стрелки, перецепленные на него."""

    sticker: BoardStickerSchema
    updated_shapes: list[BoardShapeSchema]
