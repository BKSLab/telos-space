from fastapi import status

from src.exceptions.base import RepositoryError, ServiceError


class ProjectStagesRepositoryError(RepositoryError):
    """Ошибка доступа к стадиям проекта."""

    detail = "Ошибка базы данных при обработке стадий проекта."


class ProjectStageNameAlreadyExistsRepositoryError(ProjectStagesRepositoryError):
    """Стадия с таким названием уже существует в проекте."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(error_details=f"Стадия {name!r} уже существует в проекте.")


class ProjectStagesServiceError(ServiceError):
    """Ошибка бизнес-операции со стадиями проекта."""

    detail = "Не удалось выполнить операцию со стадиями проекта."


class ProjectStageNotFoundError(ProjectStagesServiceError):
    """Стадия проекта не найдена."""

    status_code = status.HTTP_404_NOT_FOUND

    def __init__(self, stage_id: int):
        self.stage_id = stage_id
        super().__init__(error_details=f"Стадия id={stage_id} не найдена.")

    @property
    def detail(self) -> str:
        return f"Стадия с id={self.stage_id} не найдена."


class ProjectStageNameConflictError(ProjectStagesServiceError):
    """Название стадии уже занято внутри проекта."""

    status_code = status.HTTP_409_CONFLICT

    def __init__(self, name: str):
        self.name = name
        super().__init__(error_details=f"Стадия {name!r} уже существует в проекте.")

    @property
    def detail(self) -> str:
        return f"Стадия с названием {self.name!r} уже существует в проекте."


class ProjectStageNameNotFoundError(ProjectStagesServiceError):
    """Название стадии не найдено; содержит варианты для исправления запроса."""

    status_code = status.HTTP_404_NOT_FOUND

    def __init__(self, *, stage_name: str, known: list[str]) -> None:
        self.stage_name = stage_name
        self.known = known
        super().__init__(error_details=f"Стадия {stage_name!r} не найдена.")

    @property
    def detail(self) -> str:
        return f"Стадия не найдена. Доступные стадии: {', '.join(self.known)}."


class ProjectStageHasTasksError(ProjectStagesServiceError):
    """Стадия содержит задачи и не может быть удалена."""

    status_code = status.HTTP_409_CONFLICT

    def __init__(self, stage_id: int):
        self.stage_id = stage_id
        super().__init__(error_details=f"Стадия id={stage_id} содержит задачи.")

    @property
    def detail(self) -> str:
        return f"Стадия с id={self.stage_id} содержит задачи и не может быть удалена."


class ProjectLastStageDeleteError(ProjectStagesServiceError):
    """Последнюю стадию проекта удалять нельзя."""

    status_code = status.HTTP_409_CONFLICT

    def __init__(self, stage_id: int):
        self.stage_id = stage_id
        super().__init__(error_details=f"Стадия id={stage_id} — последняя в проекте.")

    @property
    def detail(self) -> str:
        return "Нельзя удалить последнюю стадию проекта."


class ProjectStageForeignProjectError(ProjectStagesServiceError):
    """Стадия принадлежит другому проекту."""

    status_code = status.HTTP_409_CONFLICT

    def __init__(self, stage_id: int, project_id: int):
        self.stage_id = stage_id
        self.project_id = project_id
        super().__init__(
            error_details=f"Стадия id={stage_id} не принадлежит проекту id={project_id}.",
        )

    @property
    def detail(self) -> str:
        return f"Стадия с id={self.stage_id} принадлежит другому проекту."
