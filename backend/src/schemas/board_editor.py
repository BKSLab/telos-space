"""Явные группы/locks доски и автономные шаблоны выделения."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.board_shapes import BoardShapeCreateSchema, ShapeAnchor
from src.schemas.board_stickers import BoardStickerCreateSchema
from src.schemas.boards import BoardSchema
from src.schemas.projects import ProjectCreateSchema, ProjectSchema

MAX_METADATA_BYTES = 512_000
MAX_GROUPS = 200
MAX_MEMBERS = 2000
MAX_TEMPLATES = 50
MAX_TEMPLATE_OBJECTS = 200

Identifier = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
Name = Annotated[str, Field(min_length=1, max_length=120)]


class EditorModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="after")
    def reject_null_bytes(self) -> Self:
        for value in self.__dict__.values():
            if isinstance(value, str) and "\x00" in value:
                raise ValueError("Нулевые байты недопустимы.")
        return self


class BoardObjectRef(EditorModel):
    type: Literal["shape", "sticker"]
    id: int = Field(gt=0, le=2_147_483_647, strict=True)

    def key(self) -> tuple[str, int]:
        return self.type, self.id


class BoardEditorGroup(EditorModel):
    id: Identifier
    name: Name
    members: list[BoardObjectRef] = Field(min_length=1, max_length=MAX_MEMBERS)

    @model_validator(mode="after")
    def unique_members(self) -> Self:
        keys = [member.key() for member in self.members]
        if len(keys) != len(set(keys)):
            raise ValueError("Объект не может повторяться в группе.")
        return self


class TemplateObjectRef(EditorModel):
    object_id: Identifier
    anchor: ShapeAnchor = "center"


class TemplateShapeData(BoardShapeCreateSchema):
    """Привязки шаблона используют собственные стабильные object_id, не ID доски."""

    start_ref: TemplateObjectRef | None = None
    end_ref: TemplateObjectRef | None = None


class TemplateStickerData(BoardStickerCreateSchema):
    task_ids: list[int] = Field(default_factory=list, max_length=0)

    @model_validator(mode="after")
    def finite_geometry(self) -> Self:
        import math

        if any(
            not math.isfinite(value)
            for value in (
                self.canvas_x,
                self.canvas_y,
                self.width,
                self.height,
            )
        ):
            raise ValueError("Координаты шаблона должны быть конечными.")
        if "\x00" in self.body:
            raise ValueError("Нулевые байты недопустимы.")
        return self


class TemplateShape(EditorModel):
    type: Literal["shape"]
    object_id: Identifier
    data: TemplateShapeData


class TemplateSticker(EditorModel):
    type: Literal["sticker"]
    object_id: Identifier
    data: TemplateStickerData


TemplateObject = Annotated[TemplateShape | TemplateSticker, Field(discriminator="type")]


class BoardTemplateGroup(EditorModel):
    id: Identifier
    name: Name
    members: list[Identifier] = Field(min_length=2, max_length=MAX_TEMPLATE_OBJECTS)


class BoardSelectionTemplate(EditorModel):
    id: Identifier
    name: Name
    objects: list[TemplateObject] = Field(min_length=1, max_length=MAX_TEMPLATE_OBJECTS)
    groups: list[BoardTemplateGroup] = Field(default_factory=list, max_length=MAX_GROUPS)
    locked: list[Identifier] = Field(default_factory=list, max_length=MAX_TEMPLATE_OBJECTS)

    @model_validator(mode="after")
    def validate_local_refs(self) -> Self:
        identifiers = [item.object_id for item in self.objects]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("object_id шаблона должны различаться.")
        by_id = {item.object_id: item for item in self.objects}
        for item in self.objects:
            if isinstance(item, TemplateShape):
                for ref in (item.data.start_ref, item.data.end_ref):
                    if ref is not None:
                        target = by_id.get(ref.object_id)
                        if target is None:
                            raise ValueError("Привязка должна вести на объект внутри шаблона.")
                        if isinstance(target, TemplateShape) and target.data.kind in (
                            "line",
                            "arrow",
                        ):
                            raise ValueError("Привязка не может вести на другую стрелку или линию.")
        group_ids = [group.id for group in self.groups]
        members = [member for group in self.groups for member in group.members]
        if len(group_ids) != len(set(group_ids)) or len(members) != len(set(members)):
            raise ValueError("Группы шаблона должны быть уникальными и непересекающимися.")
        if any(member not in by_id for member in members):
            raise ValueError("Группа должна содержать только объекты шаблона.")
        if len(self.locked) != len(set(self.locked)) or any(
            key not in by_id for key in self.locked
        ):
            raise ValueError("Блокировки должны вести на уникальные объекты шаблона.")
        return self


class BoardEditorMetadata(EditorModel):
    version: Literal[1] = 1
    groups: list[BoardEditorGroup] = Field(default_factory=list, max_length=MAX_GROUPS)
    locked: list[BoardObjectRef] = Field(default_factory=list, max_length=MAX_MEMBERS)
    templates: list[BoardSelectionTemplate] = Field(default_factory=list, max_length=MAX_TEMPLATES)

    @model_validator(mode="after")
    def unique_ids_and_size(self) -> Self:
        for items in (self.groups, self.templates):
            identifiers = [item.id for item in items]
            if len(identifiers) != len(set(identifiers)):
                raise ValueError("Идентификаторы групп и шаблонов должны различаться.")
        members = [member.key() for group in self.groups for member in group.members]
        if len(members) > MAX_MEMBERS or len(members) != len(set(members)):
            raise ValueError("Объект входит только в одну группу; всего не более 2000 объектов.")
        locks = [item.key() for item in self.locked]
        if len(locks) != len(set(locks)):
            raise ValueError("Повторяющиеся locks недопустимы.")
        if len(self.model_dump_json().encode("utf-8")) > MAX_METADATA_BYTES:
            raise ValueError("Metadata доски превышает 512000 байт.")
        return self


class BoardEditorWrite(EditorModel):
    expected_revision: int = Field(ge=1, strict=True)
    metadata: BoardEditorMetadata


class BoardEditorState(EditorModel):
    board_id: int
    revision: int
    metadata: BoardEditorMetadata


class BoardPromotionWrite(EditorModel):
    expected_revision: int = Field(ge=1, strict=True)
    project: ProjectCreateSchema


class BoardPromotionResult(EditorModel):
    project: ProjectSchema
    board: BoardSchema
