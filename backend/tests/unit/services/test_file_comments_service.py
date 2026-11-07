"""Инварианты отправки до получения соединения PostgreSQL."""

from uuid import uuid4

import pytest

from src.exceptions.file_comments import FileCommentValidationError
from src.exceptions.task_attachments import (
    TaskAttachmentTooLargeError,
    TaskAttachmentUnsupportedTypeError,
)
from src.services.file_comments import CommentFileUpload, FileCommentsService
from src.storage.task_attachments import TaskAttachmentStorage


async def test_invalid_submission_never_opens_database_or_creates_original(tmp_path):
    def forbidden_scope():
        raise AssertionError("Некорректная отправка не должна обращаться к БД")

    service = FileCommentsService(scope=forbidden_scope, storage=TaskAttachmentStorage(tmp_path))
    for body, uploads, ids in (
        (" ", [], []),
        ("text\x00", [], []),
        ("x" * 50_001, [], []),
        ("text", [], [1, 1]),
        ("text", [], [True]),
        ("text", [], [{}]),
        ("text", [], list(range(1, 12))),
        ("", [CommentFileUpload("a.txt", b"a", caption="x" * 4001)], []),
    ):
        with pytest.raises(FileCommentValidationError):
            await service.submit(
                task_id=1,
                request_id=uuid4(),
                body_md=body,
                uploads=uploads,
                attachment_ids=ids,
                session_token=None,
                bearer_secret=None,
            )
    for file, expected in (
        (CommentFileUpload("file.exe", b"x"), TaskAttachmentUnsupportedTypeError),
        (
            CommentFileUpload("file.txt", b"x" * (service.max_file_size + 1)),
            TaskAttachmentTooLargeError,
        ),
    ):
        with pytest.raises(expected):
            await service.submit(
                task_id=1,
                request_id=uuid4(),
                body_md="",
                uploads=[file],
                attachment_ids=[],
                session_token=None,
                bearer_secret=None,
            )
    assert not list(tmp_path.rglob("*"))
