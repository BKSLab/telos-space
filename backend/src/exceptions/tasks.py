from fastapi import status

from src.exceptions.base import RepositoryError, ServiceError


class TasksRepositoryError(RepositoryError):
    """Ошибка доступа к задачам."""

    detail = "Ошибка базы данных при обработке задач."


class TaskNumberAlreadyExistsRepositoryError(TasksRepositoryError):
    """Номер задачи уже занят внутри проекта."""

    def __init__(self, project_id: int, number: int):
        self.project_id = project_id
        self.number = number
        super().__init__(
            error_details=f"Номер {number} уже занят в проекте id={project_id}.",
        )


class TasksServiceError(ServiceError):
    """Ошибка бизнес-операции с задачами."""

    detail = "Не удалось выполнить операцию с задачами."


class TaskChecklistConflictError(TasksServiceError):
    """Кто-то уже изменил чек-лист после получения карточки."""

    status_code = status.HTTP_409_CONFLICT
    detail = "Чек-лист уже изменён. Обновите его и повторите правки."


class TaskVersionConflictError(TasksServiceError):
    """Прочитанная версия задачи устарела перед удалением."""

    status_code = status.HTTP_409_CONFLICT
    detail = "Задача изменилась. Прочитайте свежую версию перед удалением."


class TaskRevisionConflictError(TasksServiceError):
    """Общие поля задачи изменились после чтения её версии."""

    status_code = status.HTTP_409_CONFLICT
    detail = "Задача изменилась. Перечитайте её и повторите изменения."


class TaskRevisionValidationError(TasksServiceError):
    """Ожидаемая версия задачи не является положительным целым числом."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    detail = "Ожидаемая версия задачи должна быть положительным целым числом."


class TaskChecklistValidationError(TasksServiceError):
    """Пункты или версия чек-листа не соответствуют контракту."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    detail = "Проверьте пункты и текущую версию чек-листа."


class TaskChecklistGenerationError(TasksServiceError):
    """Модель не вернула допустимое предложение."""

    status_code = status.HTTP_502_BAD_GATEWAY
    detail = "Не удалось сформировать чек-лист. Попробуйте ещё раз."


class TaskNotFoundError(TasksServiceError):
    """Задача не найдена."""

    status_code = status.HTTP_404_NOT_FOUND

    def __init__(self, task_id: int):
        self.task_id = task_id
        super().__init__(error_details=f"Задача id={task_id} не найдена.")

    @property
    def detail(self) -> str:
        return f"Задача с id={self.task_id} не найдена."


class TaskForeignProjectError(TasksServiceError):
    """Задача принадлежит другому проекту."""

    status_code = status.HTTP_409_CONFLICT

    def __init__(self, task_id: int, project_id: int):
        self.task_id = task_id
        self.project_id = project_id
        super().__init__(
            error_details=f"Задача id={task_id} не принадлежит проекту id={project_id}.",
        )

    @property
    def detail(self) -> str:
        return f"Задача с id={self.task_id} принадлежит другому проекту."


class TaskNumberAllocationError(TasksServiceError):
    """Не удалось выделить свободный номер задачи."""

    status_code = status.HTTP_409_CONFLICT

    def __init__(self, project_id: int):
        self.project_id = project_id
        super().__init__(
            error_details=f"Не удалось выделить номер задачи в проекте id={project_id}.",
        )

    @property
    def detail(self) -> str:
        return "Не удалось выделить номер задачи. Повторите попытку."


class TaskDateRangeError(TasksServiceError):
    """Начало задачи находится после её дедлайна."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    detail = "Дата начала задачи не может быть позже даты завершения."

    def __init__(self):
        super().__init__(error_details=self.detail)


class TaskParticipantNotProjectMemberError(TasksServiceError):
    """На задачу пытаются назначить пользователя не из команды проекта."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    detail = "На задачу можно назначать только участников команды проекта."

    def __init__(self, user_id: int):
        self.user_id = user_id
        super().__init__(error_details=f"Пользователь id={user_id} не состоит в команде проекта.")


class TaskReporterPermissionError(TasksServiceError):
    """Участник проекта пытается назначить другого постановщика."""

    status_code = status.HTTP_403_FORBIDDEN
    detail = "Изменить постановщика может только владелец проекта."

    def __init__(self) -> None:
        super().__init__(error_details=self.detail)


class TaskDescriptionRewriteError(TasksServiceError):
    """LLM вернула непригодный результат переформулирования."""

    status_code = status.HTTP_502_BAD_GATEWAY
    detail = "Не удалось безопасно переформулировать описание. Исходный текст сохранён."

    def __init__(self, error_details: str | None = None) -> None:
        super().__init__(error_details=error_details or self.detail)


class TaskContextDocumentError(TasksServiceError):
    """В AI-контекст передан документ другого проекта или неизвестный документ."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    detail = "Для контекста можно использовать только документы текущего проекта."

    def __init__(self, document_ref: str | None = None) -> None:
        self.document_ref = document_ref
        super().__init__(error_details="Выбранный документ недоступен в текущем проекте.")


class TaskContextFileError(TasksServiceError):
    """Файл не удалось прочитать для AI-контекста."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT

    def __init__(self, file_name: str) -> None:
        self.file_name = file_name
        super().__init__(error_details=f"Не удалось извлечь текст из файла {file_name!r}.")

    @property
    def detail(self) -> str:
        return f"Не удалось прочитать «{self.file_name}» для переформулирования."


class TaskOutsideProjectError(TasksServiceError):
    """Операция имеет смысл только для проектной задачи."""

    status_code = status.HTTP_409_CONFLICT
    detail = "Это действие доступно только задачам проекта."

    def __init__(self, task_id: int) -> None:
        self.task_id = task_id
        super().__init__(error_details=f"Задача id={task_id} не принадлежит проекту.")


class TaskStatusOfProjectTaskError(TasksServiceError):
    """Статус проектной задачи задаёт стадия канбана, а не поле status."""

    status_code = status.HTTP_409_CONFLICT
    detail = "Состояние задачи проекта меняется переносом по стадиям доски."

    def __init__(self, task_id: int) -> None:
        self.task_id = task_id
        super().__init__(error_details=f"Задача id={task_id} проектная, статус задаёт стадия.")


class TaskPersonNotFoundError(TasksServiceError):
    """Назначаемый на задачу пользователь не найден или отключён."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    detail = "Назначить на задачу можно только активного локального пользователя."

    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        super().__init__(error_details=f"Пользователь id={user_id} не найден или отключён.")


class TaskManagerRequiredError(TasksServiceError):
    """Менять постановщика задачи вне проекта может только её OWNER."""

    status_code = status.HTTP_403_FORBIDDEN
    detail = (
        "Изменить постановщика может постановщик разовой задачи."
    )

    def __init__(self) -> None:
        super().__init__(error_details=self.detail)
