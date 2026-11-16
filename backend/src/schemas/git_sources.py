"""Неизменяемые снимки GitHub; проектные права и credentials сюда не входят."""

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


def validate_git_owner(value: str) -> str:
    """Проверяет login владельца без возможности изменить адрес API."""
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", value):
        raise ValueError("Некорректный владелец GitHub.")
    return value


def validate_git_repository(value: str) -> str:
    """Принимает имя репозитория, а не URL или путь."""
    if value in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value):
        raise ValueError("Некорректное имя репозитория GitHub.")
    return value


def validate_git_sha(value: str) -> str:
    """Требует полный SHA-1 и сохраняет его каноническую запись."""
    if not re.fullmatch(r"[0-9a-fA-F]{40}", value):
        raise ValueError("Требуется полный SHA-1 Git.")
    return value.lower()


def validate_git_ref(value: str) -> str:
    """Проверяет literal ref, включая heads/ветка и tags/тег."""
    components = value.split("/")
    if (
        not value
        or len(value) > 1024
        or value == "@"
        or value.startswith("-")
        or value.endswith(".")
        or ".." in value
        or "@{" in value
        or any(ord(char) <= 32 or ord(char) == 127 or char in "~^:?*[\\" for char in value)
        or any(not part or part.startswith(".") or part.endswith(".lock") for part in components)
    ):
        raise ValueError("Некорректная ссылка Git.")
    return value


def validate_git_path(value: str) -> str:
    """Сохраняет точный относительный POSIX path без нормализации Unicode."""
    if (
        not value
        or len(value) > 4096
        or "\\" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("Требуется относительный путь Git без обхода каталогов.")
    return value


GitOwner = Annotated[str, Field(strict=True), AfterValidator(validate_git_owner)]
GitRepositoryName = Annotated[str, Field(strict=True), AfterValidator(validate_git_repository)]
GitSha = Annotated[str, Field(strict=True), AfterValidator(validate_git_sha)]
GitRef = Annotated[str, Field(strict=True), AfterValidator(validate_git_ref)]
GitPath = Annotated[str, Field(strict=True), AfterValidator(validate_git_path)]


class GitSnapshot(BaseModel):
    """Явные поля без upstream payload, URL, пользовательских данных и секретов."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class GitRepositorySnapshot(GitSnapshot):
    """Стабильная upstream identity и текущие координаты репозитория."""

    id: int = Field(gt=0, description="Стабильный числовой repository ID GitHub.", examples=[12345])
    owner: GitOwner = Field(description="Канонический login владельца.", examples=["BKSLab"])
    name: GitRepositoryName = Field(
        description="Каноническое имя репозитория.", examples=["telos-space"]
    )
    default_branch: GitRef | None = Field(
        description="Имя default branch или null для отсутствующей ветки.", examples=["main"]
    )
    private: bool = Field(description="Текущая приватность upstream репозитория.", examples=[True])


class GitCommit(GitSnapshot):
    """Результат однократного разрешения mutable ref в immutable commit."""

    sha: GitSha = Field(description="Полный SHA коммита.", examples=["a" * 40])
    tree_sha: GitSha = Field(
        description="Полный SHA корневого дерева коммита.", examples=["b" * 40]
    )


class GitTreeEntry(GitSnapshot):
    """Элемент дерева; symlink и submodule описаны, но не разыменованы."""

    path: GitPath = Field(
        description="Точный путь относительно запрошенного дерева.", examples=["docs/README.md"]
    )
    sha: GitSha = Field(description="SHA Git-объекта.", examples=["c" * 40])
    type: Literal["blob", "tree", "commit"] = Field(
        description="Тип Git-объекта.", examples=["blob"]
    )
    mode: Literal["100644", "100755", "040000", "120000", "160000"] = Field(
        description="Git mode, отличающий обычный файл от ссылки и submodule.", examples=["100644"]
    )
    size: int | None = Field(
        default=None,
        ge=0,
        description="Размер blob в байтах; не заявлен для дерева/submodule.",
        examples=[512],
    )

    @model_validator(mode="after")
    def consistent_object(self) -> "GitTreeEntry":
        """Не превращает неизвестный mode в обычный файл."""
        expected = {
            "100644": "blob",
            "100755": "blob",
            "120000": "blob",
            "040000": "tree",
            "160000": "commit",
        }
        if expected[self.mode] != self.type or (self.type == "blob" and self.size is None):
            raise ValueError("Несогласованный тип Git-объекта.")
        return self

    @property
    def is_regular_file(self) -> bool:
        """Только обычный/исполняемый blob разрешает чтение файла по дереву."""
        return self.type == "blob" and self.mode in {"100644", "100755"}


class GitTree(GitSnapshot):
    """Ограниченная выборка дерева, явно отделяющая частичный manifest."""

    sha: GitSha = Field(description="SHA запрошенного дерева.", examples=["b" * 40])
    entries: tuple[GitTreeEntry, ...] = Field(
        description="Проверенные элементы в upstream порядке."
    )
    truncated: bool = Field(
        description="Upstream или локальный лимит сделал manifest неполным.", examples=[False]
    )


class GitBlob(GitSnapshot):
    """Точные байты blob; распознавание текста и LFS выполняет сервис."""

    model_config = ConfigDict(ser_json_bytes="base64")

    sha: GitSha = Field(description="Проверенный SHA blob.", examples=["c" * 40])
    size: int = Field(ge=0, description="Подтверждённое число байтов.", examples=[512])
    content: bytes = Field(
        repr=False, description="Байты без декодирования и разыменования ссылок."
    )


class GitFileChange(GitSnapshot):
    """Изменение одного пути; отсутствие patch не означает отсутствие изменения."""

    filename: GitPath = Field(description="Путь после изменения.", examples=["src/app.py"])
    previous_filename: GitPath | None = Field(
        default=None, description="Прежний путь при rename.", examples=["src/old.py"]
    )
    status: Literal["added", "removed", "modified", "renamed", "copied", "changed", "unchanged"] = (
        Field(description="Статус изменения GitHub.", examples=["modified"])
    )
    sha: GitSha = Field(
        description="SHA объекта изменения, в том числе прежнего при удалении.", examples=["c" * 40]
    )
    patch: str | None = Field(
        default=None, repr=False, description="Ограниченный текст patch; null допустим для binary."
    )
    patch_truncated: bool = Field(
        default=False, description="Patch обрезан по локальному лимиту.", examples=[False]
    )

    @model_validator(mode="after")
    def rename_has_origin(self) -> "GitFileChange":
        """Переименование сохраняет прежний путь для последующего обновления manifest."""
        if self.status == "renamed" and self.previous_filename is None:
            raise ValueError("Не задан прежний путь переименованного файла.")
        return self


class GitComparison(GitSnapshot):
    """Сравнение двух SHA, не обещающее полного diff при лимитах API."""

    base_sha: GitSha = Field(description="Полный запрошенный base SHA.", examples=["a" * 40])
    head_sha: GitSha = Field(description="Полный запрошенный head SHA.", examples=["d" * 40])
    merge_base_sha: GitSha = Field(
        description="SHA общего предка из ответа GitHub.", examples=["a" * 40]
    )
    status: Literal["ahead", "behind", "diverged", "identical"] = Field(
        description="Отношение head к base.", examples=["ahead"]
    )
    ahead_by: int = Field(
        ge=0, description="Число коммитов head после общего предка.", examples=[1]
    )
    behind_by: int = Field(
        ge=0, description="Число коммитов base после общего предка.", examples=[0]
    )
    total_commits: int = Field(
        ge=0, description="Полное заявленное upstream число коммитов сравнения.", examples=[1]
    )
    files: tuple[GitFileChange, ...] = Field(description="Ограниченные изменения первой страницы.")
    files_complete: bool = Field(
        description="Полнота списка файлов подтверждена без API/local cap и pagination.",
        examples=[True],
    )
    commits_complete: bool = Field(
        description="Все коммиты поместились в единственную прочитанную страницу.", examples=[True]
    )
    incomplete_reasons: tuple[
        Literal["api_file_limit", "file_limit", "pagination", "commit_limit"], ...
    ] = Field(default=(), description="Явные причины неполноты сравнения.")
