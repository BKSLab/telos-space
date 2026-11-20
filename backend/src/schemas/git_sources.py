"""Неизменяемые снимки GitHub; проектные права и credentials сюда не входят."""

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    model_validator,
)


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


class GitAccountSnapshot(GitSnapshot):
    id: int = Field(gt=0)
    login: str = Field(min_length=1, max_length=255)


class GitRepositoryPage(GitSnapshot):
    repositories: tuple[GitRepositorySnapshot, ...]
    next_page: int | None


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

    index_state: Literal["pending", "ready", "metadata", "error"] = "pending"
    index_reason: str | None = None

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
        description="GitHub сообщил о неполноте manifest.", examples=[False]
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


class GitCommitDetails(GitCommit):
    message: str = Field(repr=False)
    message_truncated: bool = False


class GitPullRequestSnapshot(GitSnapshot):
    number: int = Field(gt=0)
    title: str = Field(repr=False, max_length=512)
    state: Literal["open", "closed"]
    merged: bool
    head_sha: GitSha
    base_sha: GitSha
    merge_sha: GitSha | None
    base_repository_id: int = Field(gt=0)
    head_ref: str
    base_ref: str

    @model_validator(mode="after")
    def verify_merged_identity(self):
        """Merged PR requires its actual merge/rebase/squash commit identity."""
        if self.merged and (self.merge_sha is None or self.state != "closed"):
            raise ValueError("Unverified merged pull request.")
        return self


class GitCheckSnapshot(GitSnapshot):
    id: int = Field(gt=0)
    name: str = Field(max_length=512, repr=False)
    sha: GitSha
    status: str = Field(max_length=80)
    conclusion: str | None = Field(max_length=80)
    updated_at: datetime | None
    provider_type: Literal["check_run", "commit_status"]


class GitCheckPage(GitSnapshot):
    items: tuple[GitCheckSnapshot, ...]
    complete: bool
    total: int = Field(ge=0)


class GitDeploymentSnapshot(GitSnapshot):
    id: int = Field(gt=0)
    sha: GitSha
    environment: str = Field(min_length=1, max_length=255)
    created_at: datetime


class GitDeploymentPage(GitSnapshot):
    items: tuple[GitDeploymentSnapshot, ...]
    complete: bool


class GitDeploymentStatusSnapshot(GitSnapshot):
    id: int = Field(gt=0)
    state: str = Field(min_length=1, max_length=80)
    environment: str | None = Field(max_length=255)
    created_at: datetime


class GitDeploymentStatusPage(GitSnapshot):
    items: tuple[GitDeploymentStatusSnapshot, ...]
    complete: bool


class GitCandidateSnapshot(GitSnapshot):
    kind: Literal["commit", "pull_request"]
    sha: GitSha
    title: str = Field(max_length=2000, repr=False)
    pull_number: int | None = None
    branch: str | None = None


class GitCandidatePage(GitSnapshot):
    items: tuple[GitCandidateSnapshot, ...]
    complete: bool


class GitConnectionCreate(BaseModel):
    """Разрешённая область подключения; секрет доступен только UI-сценарию владельца."""

    model_config = ConfigDict(extra="forbid")
    owner: GitOwner = Field(description="Login владельца GitHub.")
    repo: GitRepositoryName = Field(description="Имя репозитория GitHub.")
    name: str = Field(min_length=1, max_length=160, description="Имя подключения в проекте.")
    purpose: str = Field(default="", max_length=1000, description="Назначение этой части проекта.")
    ref: GitRef | None = Field(
        default=None, description="heads/ветка, tags/тег или SHA; null выбирает default branch."
    )
    share_private_with_project: bool = Field(
        default=False,
        description="Явное согласие владельца на чтение закрытого корпуса командой проекта.",
    )

StageIds = Annotated[list[Annotated[int, Field(gt=0, strict=True)]], Field(max_length=100)]


class GitConnectionUpdate(BaseModel):
    """PATCH конфигурации с обязательной optimistic concurrency версией."""

    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, description="Прочитанная версия настройки.")
    sync_stage_ids: StageIds | None = Field(
        default=None, description="ID стадий этого проекта, запускающих сверку; [] отключает.",
    )
    name: str | None = Field(default=None, min_length=1, max_length=160, description="Новое имя.")
    purpose: str | None = Field(default=None, max_length=1000, description="Новое назначение.")
    ref: GitRef | None = Field(
        default=None, description="Новый ref; явный null выбирает default branch."
    )
    enabled: bool | None = Field(
        default=None, description="Включение/отключение без удаления истории."
    )
    share_private_with_project: bool | None = Field(
        default=None, description="Согласие на общий закрытый корпус."
    )

    @model_validator(mode="after")
    def valid_update(self):
        """Проверяет только присутствующие поля, различая null выбранного ref."""
        if any(getattr(self, name) is None for name in self.model_fields_set - {"ref"}):
            raise ValueError("Параметр настройки не может быть null.")
        if self.sync_stage_ids is not None:
            self.sync_stage_ids = list(dict.fromkeys(self.sync_stage_ids))
        return self


class GitWebhookSecretUpdate(BaseModel):
    """Секретные поля задаёт только сессия текущего владельца проекта."""

    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, description="Прочитанная версия подключения.")
    webhook_secret: SecretStr | None = Field(
        repr=False,
        exclude=True,
        description="Секрет проверки входящих событий; null отключает webhook.",
    )


class GitHubProfile(BaseModel):
    account: GitAccountSnapshot | None


class GitHubProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr = Field(
        min_length=1,
        max_length=1024,
        repr=False,
        exclude=True,
        description="Личный токен GitHub для проверки аккаунта или сохранения в профиле.",
        examples=["github_pat_example"],
    )


class GitCoverage(BaseModel):
    """Фактический охват версии, а не обещание анализа всего репозитория."""

    total_files: int = 0
    indexed_files: int = 0
    cataloged_files: int = 0
    metadata_files: int = 0
    metadata_counts: dict[str, int] = Field(default_factory=dict)
    failed_files: int = 0
    truncated: bool = False
    reasons: list[str] = Field(default_factory=list)
    commit_sha: str | None = None
    generation: int | None = None


class GitConnection(BaseModel):
    """Безопасное публичное состояние project binding без credentials/ciphertext."""

    id: int
    project_id: int
    provider: Literal["github"] = "github"
    repository_id: int
    owner: str
    repo: str
    name: str
    purpose: str
    ref: str
    enabled: bool
    is_private: bool
    share_private_with_project: bool
    has_credential: bool
    has_webhook_secret: bool = False
    sync_stage_ids: list[int] = Field(default_factory=list)
    sync_pending: bool = False
    poll_interval_seconds: float = 3600.0
    last_sync_reason: Literal["poll", "manual", "webhook", "task_stage", "configuration"] | None = None
    last_sync_requested_at: datetime | None = None
    last_webhook_received_at: datetime | None = None
    last_webhook_event: str | None = None
    last_webhook_processed_at: datetime | None = None
    revision: int
    generation: int
    credential_revision: int
    state: Literal["pending", "syncing", "ready", "partial", "error", "disabled", "access_lost"]
    observed_sha: str | None
    indexed_sha: str | None
    observed_at: datetime | None
    indexed_at: datetime | None
    checked_at: datetime | None
    next_poll_at: datetime | None = None
    last_error_code: str | None
    coverage: GitCoverage = Field(default_factory=GitCoverage)
    created_at: datetime
    updated_at: datetime


class GitConnectionPage(BaseModel):
    items: list[GitConnection]
    total: int
    offset: int
    limit: int
    next_offset: int | None


class GitSourceProof(BaseModel):
    """Server-only identity прочитанного фрагмента для повторной проверки публикации."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    source_id: str = Field(pattern=r"^git_file:[1-9][0-9]*$")
    entity_type: Literal["git_file"] = "git_file"
    entity_id: int = Field(gt=0)
    file_id: int = Field(gt=0)
    project_id: int = Field(gt=0)
    connection_id: int = Field(gt=0)
    repository_id: int = Field(gt=0)
    ref: str
    commit_sha: GitSha
    path: GitPath
    blob_sha: GitSha
    text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    generation: int = Field(ge=1)
    credential_revision: int = Field(ge=0)


class GitFileRead(GitSourceProof):
    title: str
    source_url: str
    text: str
    offset: int
    next_offset: int | None
    total_chars: int
    line_start: int
    line_end: int
    stale: bool
    partial: bool


class GitSearchHit(GitSourceProof):
    title: str
    source_url: str
    excerpt: str
    offset: int
    line_start: int
    line_end: int
    stale: bool
    partial: bool


class GitSearchResult(BaseModel):
    items: list[GitSearchHit]
    partial: bool
    inaccessible_connection_ids: list[int] = Field(default_factory=list)
    incomplete_reasons: list[str] = Field(default_factory=list)


class GitTreePage(BaseModel):
    connection_id: int
    repository_id: int
    commit_sha: str
    items: list[GitTreeEntry]
    offset: int
    limit: int
    next_offset: int | None
    truncated: bool
    stale: bool
    total_files: int = 0


class GitComponent(BaseModel):
    connection_id: int
    repository_id: int
    commit_sha: str
    file_id: int
    path: str
    kind: str
    symbols: list[dict] = Field(default_factory=list)
    imports: list[dict] = Field(default_factory=list)


class GitProjectMap(BaseModel):
    project_id: int
    manifest: list[GitConnection]
    components: list[GitComponent]
    relations: list[dict] = Field(default_factory=list)
    sources: list[GitSourceProof] = Field(default_factory=list)
    total_components: int = 0
    components_truncated: bool = False
    partial: bool
    incomplete_reasons: list[str] = Field(default_factory=list)


class GitTaskLinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: int = Field(gt=0)
    kind: Literal["commit", "pull_request"]
    commit_sha: GitSha | None = None
    pull_number: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def exact_identity(self):
        """Ветка не заменяет постоянную identity изменения."""
        if (
            self.kind == "commit" and (self.commit_sha is None or self.pull_number is not None)
        ) or (
            self.kind == "pull_request"
            and (self.pull_number is None or self.commit_sha is not None)
        ):
            raise ValueError("Укажите единственную identity изменения.")
        return self


class GitEvidence(BaseModel):
    """Фактически прочитанное доказательство точного repository/SHA/environment."""

    kind: Literal["check", "deployment"]
    provider_type: Literal["check_run", "commit_status", "deployment"]
    provider_id: str
    connection_id: int
    repository_id: int
    commit_sha: GitSha
    status: str
    conclusion: str | None = None
    environment: str | None = None
    source_url: str
    observed_at: datetime
    upstream_at: datetime | None = None
    is_current: bool | None = None


class GitChangeProof(BaseModel):
    """Server-only identity прочитанных PR/CI/deploy фактов для публикации."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    project_id: int
    connection_id: int
    change_id: int
    task_id: int
    link_id: int
    repository_id: int
    generation: int
    credential_revision: int
    revision: int
    commit_sha: GitSha
    evidence_hash: str


class GitTaskLink(BaseModel):
    id: int
    task_id: int
    change_id: int
    connection_id: int
    repository_id: int
    kind: Literal["commit", "pull_request"]
    title: str
    source_url: str
    commit_sha: str
    head_sha: str | None = None
    merge_sha: str | None = None
    pull_number: int | None = None
    status: str
    revision: int
    checked_at: datetime | None
    evidence: list[GitEvidence] = Field(default_factory=list)
    evidence_partial: bool = True
    last_error_code: str | None = None


class GitTaskLinks(BaseModel):
    project_id: int
    task_id: int
    items: list[GitTaskLink]
    sources: list[GitChangeProof] = Field(default_factory=list)
    partial: bool = False
    incomplete_reasons: list[str] = Field(default_factory=list)


class GitTaskCandidate(BaseModel):
    connection_id: int
    repository_id: int
    kind: Literal["commit", "pull_request"]
    commit_sha: GitSha
    pull_number: int | None = None
    title: str
    source_url: str
    match: Literal["task_key"] = "task_key"
    ambiguous: bool = False


class GitTaskCandidates(BaseModel):
    items: list[GitTaskCandidate]
    partial: bool
    incomplete_reasons: list[str] = Field(default_factory=list)


class GitQueued(BaseModel):
    queued: bool = True


class GitEventAccepted(BaseModel):
    accepted: bool = True
    duplicate: bool = False
