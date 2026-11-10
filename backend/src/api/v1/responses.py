"""Переиспользуемые описания ошибок OpenAPI для API v1."""

from src.schemas.http_errors import HttpErrorResponseSchema, HttpValidationErrorResponseSchema

NOT_FOUND_RESPONSE = {
    "model": HttpErrorResponseSchema,
    "description": "Запрашиваемый объект не найден.",
    "content": {"application/json": {"example": {"detail": "Объект не найден."}}},
}
CONFLICT_RESPONSE = {
    "model": HttpErrorResponseSchema,
    "description": "Операция конфликтует с текущим состоянием данных.",
    "content": {"application/json": {"example": {"detail": "Операция недоступна."}}},
}
VALIDATION_RESPONSE = {
    "model": HttpValidationErrorResponseSchema,
    "description": "Параметры или тело запроса не прошли валидацию.",
    "content": {
        "application/json": {
            "examples": {
                "domain": {
                    "summary": "Доменная валидация",
                    "value": {"detail": "Ошибка валидации."},
                },
                "request": {
                    "summary": "Валидация параметров FastAPI",
                    "value": {
                        "detail": [
                            {"loc": ["body", "title"], "msg": "Field required", "type": "missing"}
                        ]
                    },
                },
            }
        }
    },
}
SERVER_ERROR_RESPONSE = {
    "model": HttpErrorResponseSchema,
    "description": "Внутренняя ошибка обработки запроса.",
    "content": {"application/json": {"example": {"detail": "Внутренняя ошибка."}}},
}


FORBIDDEN_RESPONSE = {
    "model": HttpErrorResponseSchema,
    "description": "Недостаточно прав для этой операции.",
    "content": {"application/json": {"example": {"detail": "Недостаточно прав."}}},
}
