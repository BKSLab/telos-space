from fastapi import status

from src.exceptions.base import RepositoryError, ServiceError


class ProjectsRepositoryError(RepositoryError):
    """Ошибка доступа к проектам."""

    detail = "Ошибка базы данных при обработке проектов."


class ProjectKeyAlreadyExistsRepositoryError(ProjectsRepositoryError):
    """Код проекта уже занят в базе данных."""

    def __init__(self, key: str):
        self.key = key
        super().__init__(error_details=f"Код проекта {key} уже занят.")


class ProjectMemberAlreadyExistsRepositoryError(ProjectsRepositoryError):
    """Пользователь уже входит в команду проекта."""

    def __init__(self, project_id: int, user_id: int):
        self.project_id = project_id
        self.user_id = user_id
        super().__init__(
            error_details=(f"Пользователь id={user_id} уже входит в проект id={project_id}.")
        )


class ProjectsServiceError(ServiceError):
    """Ошибка бизнес-операции с проектами."""

    detail = "Не удалось выполнить операцию с проектами."


class ProjectNotFoundError(ProjectsServiceError):
    """Проект не найден."""

    status_code = status.HTTP_404_NOT_FOUND

    def __init__(self, project_id: int):
        self.project_id = project_id
        super().__init__(error_details=f"Проект id={project_id} не найден.")

    @property
    def detail(self) -> str:
        return f"Проект с id={self.project_id} не найден."


class ProjectValidationError(ProjectsServiceError):
    """Паспорт или изменение срока нарушает правила проекта."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(error_details=detail)


class ProjectDeadlineConflictError(ProjectsServiceError):
    """Срок успел измениться после открытия формы."""

    status_code = status.HTTP_409_CONFLICT
    detail = "Срок проекта уже изменён. Обновите данные и повторите изменение."

    def __init__(self):
        super().__init__(error_details=self.detail)


class ProjectContextConflictError(ProjectsServiceError):
    """Паспорт изменился после чтения его версии."""

    status_code = status.HTTP_409_CONFLICT
    detail = "Паспорт проекта уже изменён. Обновите данные и повторите изменение."

    def __init__(self):
        super().__init__(error_details=self.detail)


class ProjectOwnerRequiredError(ProjectsServiceError):
    """Редактирование проекта доступно только его актуальному владельцу."""

    status_code = status.HTTP_403_FORBIDDEN
    detail = "Изменять проект может только его владелец."

    def __init__(self):
        super().__init__(error_details=self.detail)


class ProjectKeyConflictError(ProjectsServiceError):
    """Код проекта уже используется другим проектом."""

    status_code = status.HTTP_409_CONFLICT

    def __init__(self, key: str):
        self.key = key
        super().__init__(error_details=f"Код проекта {key} уже используется.")

    @property
    def detail(self) -> str:
        return f"Проект с кодом {self.key} уже существует."


class ProjectMemberUserNotFoundError(ProjectsServiceError):
    """Пользователь с указанным точным логином не найден."""

    status_code = status.HTTP_404_NOT_FOUND
    detail = "Пользователь с таким логином не найден."

    def __init__(self, username: str):
        self.username = username
        super().__init__(error_details=f"Пользователь {username!r} не найден или неактивен.")


class ProjectMemberAlreadyExistsError(ProjectsServiceError):
    """Пользователь уже состоит в проектной команде."""

    status_code = status.HTTP_409_CONFLICT
    detail = "Пользователь уже состоит в команде проекта."

    def __init__(self, user_id: int):
        self.user_id = user_id
        super().__init__(error_details=f"Пользователь id={user_id} уже в команде.")


class ProjectMemberNotFoundError(ProjectsServiceError):
    """Участие пользователя в проекте не найдено."""

    status_code = status.HTTP_404_NOT_FOUND
    detail = "Участник команды не найден."

    def __init__(self, user_id: int):
        self.user_id = user_id
        super().__init__(error_details=f"Участник user_id={user_id} не найден.")


class ProjectOwnerRemovalError(ProjectsServiceError):
    """Владельца нельзя удалить из собственной команды."""

    status_code = status.HTTP_409_CONFLICT
    detail = "Владельца проекта нельзя удалить из команды."

    def __init__(self):
        super().__init__(error_details=self.detail)


class ProjectIconImageInvalidError(ProjectsServiceError):
    """Загруженная картинка проекта не PNG, JPEG или WebP."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    detail = "Картинка проекта — только PNG, JPG или WebP до 2 МБ."

    def __init__(self, reason: str):
        super().__init__(error_details=reason)


class ProjectIconImageNotFoundError(ProjectsServiceError):
    """У проекта нет своей картинки."""

    status_code = status.HTTP_404_NOT_FOUND
    detail = "У проекта нет своей картинки."

    def __init__(self, project_id: int):
        super().__init__(error_details=f"У проекта id={project_id} нет картинки.")


class ProjectIconImageTooLargeError(ProjectsServiceError):
    """Картинка проекта больше допустимого размера."""

    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    detail = "Картинка проекта должна быть не больше 2 МБ."

    def __init__(self):
        super().__init__(error_details=self.detail)


class ProjectIconStorageError(ProjectsServiceError):
    """Хранилище файлов не приняло или не отдало картинку проекта."""

    status_code = status.HTTP_502_BAD_GATEWAY
    detail = "Файловое хранилище недоступно, повторите позже."
