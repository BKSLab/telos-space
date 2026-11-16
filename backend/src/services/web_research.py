"""Публичное исследование через общие права, разрешения и каталог документов."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from src.clients.web_context import WebContextClient
from src.exceptions.api_tokens import InsufficientTokenScopeError
from src.exceptions.web_context import WebContextClientError
from src.exceptions.web_research import (
    WebResearchConsentError,
    WebResearchDisabledError,
    WebResearchServiceError,
    WebResearchVersionError,
)
from src.schemas.document_catalog import DocumentCatalogItemSchema, DocumentCreateRequestSchema
from src.schemas.web_context import WebReadRequest, WebSearchRequest
from src.schemas.web_research import (
    WebPageGrantSchema,
    WebResearchReadSchema,
    WebResearchSearchSchema,
    WebResearchStatusSchema,
    WebSearchPageSchema,
    WebTopicGrantSchema,
)
from src.services.auth import Principal
from src.services.document_catalog import DocumentCatalogScopeFactory, DocumentCatalogService
from src.utils.api_scopes import SCOPE_KNOWLEDGE_READ, SCOPE_PROJECTS_READ, SCOPE_PROJECTS_WRITE
from src.utils.web_grants import WebGrantSigner


@dataclass(frozen=True, slots=True)
class PreparedWebResearch:
    """Server-only прочитанная версия; не аргумент модели или публичного API."""

    project_id: int
    user_id: int
    page_token: str
    title: str
    content_md: str
    request_binding: tuple[tuple[str, str], ...]


class WebResearchService:
    """Модель использует только подписанные темы/страницы, не raw private query."""

    def __init__(
        self,
        *,
        scope: DocumentCatalogScopeFactory,
        client: WebContextClient,
        signer: WebGrantSigner,
        documents: DocumentCatalogService,
        enabled: bool,
        search_enabled: bool,
    ) -> None:
        self.scope = scope
        self.client = client
        self.signer = signer
        self.documents = documents
        self.enabled = enabled
        self.search_enabled = enabled and search_enabled

    async def status(self, *, project_id: int, principal: Principal) -> WebResearchStatusSchema:
        await self._access(project_id, principal)
        return WebResearchStatusSchema(
            reading_enabled=self.enabled, search_enabled=self.search_enabled
        )

    async def approve_topic(
        self, *, project_id: int, principal: Principal, public_topic: str, approval_id: str
    ) -> WebTopicGrantSchema:
        """Вызывается только human endpoint либо с immutable approved pending payload."""
        await self._access(project_id, principal)
        self._enabled()
        topic = WebSearchRequest(query=public_topic).query
        self._public_literal(topic)
        token, deadline = self.signer.issue(
            project_id=project_id,
            user_id=principal.user_id,
            kind="topic",
            value=topic,
            approval_id=approval_id,
        )
        return WebTopicGrantSchema(public_topic=topic, topic_token=token, expires_at=deadline)

    async def approve_url(
        self, *, project_id: int, principal: Principal, url: str, approval_id: str
    ) -> WebPageGrantSchema:
        """Разрешение точного адреса, введённого человеком; сеть не вызывается."""
        await self._access(project_id, principal)
        self._enabled()
        self._public_literal(url)
        try:
            parts = urlsplit(url)
            if (
                parts.scheme not in {"http", "https"}
                or not parts.hostname
                or parts.username
                or parts.password
            ):
                raise ValueError
            if len(url) > 4096 or any(ord(char) <= 32 for char in url):
                raise ValueError
        except ValueError:
            raise WebResearchConsentError("Недопустимый публичный адрес.") from None
        token, deadline = self.signer.issue(
            project_id=project_id,
            user_id=principal.user_id,
            kind="page",
            value=url,
            approval_id=approval_id,
        )
        return WebPageGrantSchema(url=url, page_token=token, expires_at=deadline)

    async def search(
        self, *, project_id: int, principal: Principal, topic_token: str, limit: int = 5
    ) -> WebResearchSearchSchema:
        await self._access(project_id, principal)
        self._enabled()
        if not self.search_enabled:
            raise WebResearchDisabledError("Ключ Brave Search не настроен.")
        grant = self._grant(topic_token, project_id, principal, "topic")
        request = WebSearchRequest(query=grant["q"], limit=min(max(limit, 1), 10))
        try:
            result = await self.client.search(request, allow_external_query=True)
        except WebContextClientError as error:
            raise WebResearchServiceError(error.detail) from error
        await self._access(project_id, principal)
        self._grant(topic_token, project_id, principal, "topic")
        items = []
        for item in result.items:
            token, _ = self.signer.issue(
                project_id=project_id,
                user_id=principal.user_id,
                kind="page",
                value=item.url,
                approval_id=grant["a"],
                expires_at=datetime.fromtimestamp(grant["exp"], UTC),
            )
            items.append(WebSearchPageSchema(**item.model_dump(), page_token=token))
        return WebResearchSearchSchema(fetched_at=result.fetched_at, items=items)

    async def read(
        self, *, project_id: int, principal: Principal, page_token: str, max_chars: int = 8000
    ) -> WebResearchReadSchema:
        await self._access(project_id, principal)
        self._enabled()
        grant = self._grant(page_token, project_id, principal, "page")
        try:
            result = await self.client.read(WebReadRequest(url=grant["q"], max_chars=max_chars))
        except WebContextClientError as error:
            raise WebResearchServiceError(error.detail) from error
        await self._access(project_id, principal)
        self._grant(page_token, project_id, principal, "page")
        return WebResearchReadSchema(**result.model_dump(), page_token=page_token)

    async def validate_page_grants(
        self, *, project_id: int, principal: Principal, page_tokens: list[str]
    ) -> None:
        """Проверяет snippets без подмены их версий хэшем непрочитанной страницы."""
        await self._access(project_id, principal)
        self._enabled()
        if len(page_tokens) > 200:
            raise WebResearchConsentError("Слишком много разрешений в одном ответе.")
        for token in page_tokens:
            self._grant(token, project_id, principal, "page")

    async def save(
        self,
        *,
        project_id: int,
        principal: Principal,
        page_token: str,
        expected_hash: str,
        title: str,
        request_id: UUID,
    ) -> DocumentCatalogItemSchema:
        """Сохраняет перечитанную версию, не присланный моделью текст или якобы full page."""
        current = await self._access(project_id, principal, write=True)
        self._enabled()
        grant = self._grant(page_token, project_id, current, "page")
        binding = self._save_binding(grant["q"], expected_hash)
        previous = await self.documents.replay_creation(
            user_id=current.user_id,
            request_id=request_id,
            project_id=project_id,
            title=title,
            request_binding=binding,
            principal=current,
        )
        if previous is not None:
            return previous
        plan = await self.prepare_save(
            project_id=project_id,
            principal=principal,
            page_token=page_token,
            expected_hash=expected_hash,
            title=title,
        )
        return await self.persist_prepared(
            project_id=project_id,
            principal=principal,
            plan=plan,
            request_id=request_id,
            documents=self.documents,
        )

    async def prepare_save(
        self,
        *,
        project_id: int,
        principal: Principal,
        page_token: str,
        expected_hash: str,
        title: str,
    ) -> PreparedWebResearch:
        """Сеть вне транзакции business effect/receipt, потом неизменяемый план."""
        current = await self._access(project_id, principal, write=True)
        source = await self.read(
            project_id=project_id, principal=current, page_token=page_token, max_chars=20000
        )
        if source.content_hash != expected_hash:
            raise WebResearchVersionError("Хэш внешней страницы изменился.")
        current = await self._access(project_id, current, write=True)
        # Fence longer than every backtick run keeps source Markdown inert.
        fence = "`" * max(
            3, 1 + max((len(m.group()) for m in re.finditer(r"`+", source.text)), default=0)
        )
        metadata = (
            "Внешнее исследование; не утверждённое требование проекта.\n\n"
            f"URL: <{source.url}>\n\nПолучено: {source.fetched_at.isoformat()}\n\n"
            f"Версия текста SHA-256: {source.content_hash}\n\n"
            f"Покрытие: {source.coverage}; извлечено {source.total_chars} символов, "
            f"сохранено {len(source.text)}.\n\n"
        )
        if source.reported_date:
            metadata += f"Заявленная источником дата: {source.reported_date}\n\n"
        if source.version_hint:
            metadata += f"Заявленная версия источника: {source.version_hint}\n\n"
        return PreparedWebResearch(
            project_id=project_id,
            user_id=current.user_id,
            page_token=page_token,
            title=title,
            content_md=f"{metadata}{fence}text\n{source.text}\n{fence}\n",
            request_binding=tuple(
                self._save_binding(
                    self._grant(page_token, project_id, current, "page")["q"], expected_hash
                ).items()
            ),
        )

    async def persist_prepared(
        self,
        *,
        project_id: int,
        principal: Principal,
        plan: PreparedWebResearch,
        request_id: UUID,
        documents: DocumentCatalogService,
    ) -> DocumentCatalogItemSchema:
        """Не вызывает сеть; bound documents участвует в общей транзакции WriteGate."""
        if plan.project_id != project_id or plan.user_id != principal.user_id:
            raise WebResearchConsentError("План исследования принадлежит другому контексту.")
        self._grant(plan.page_token, project_id, principal, "page")
        current = await self._access(project_id, principal, write=True)
        return await documents.create(
            user_id=current.user_id,
            principal=current,
            request_binding=dict(plan.request_binding),
            data=DocumentCreateRequestSchema(
                request_id=request_id,
                project_id=project_id,
                title=plan.title,
                caption="Внешнее исследование: версия и полнота указаны в тексте.",
                content_md=plan.content_md,
            ),
        )

    @staticmethod
    def _save_binding(url: str, expected_hash: str) -> dict[str, str]:
        return {"kind": "web_research_v1", "url": url, "content_hash": expected_hash}

    async def _access(
        self, project_id: int, principal: Principal, *, write: bool = False
    ) -> Principal:
        async with self.scope() as db:
            current = await db.auth.refresh_principal(principal)
            current.require(SCOPE_PROJECTS_READ)
            current.require(SCOPE_KNOWLEDGE_READ)
            grant = await db.access.ensure_project_access(
                project_id=project_id, user_id=current.user_id
            )
            if write:
                current.require(SCOPE_PROJECTS_WRITE)
                if current.api_key is None and not current.can_write:
                    raise InsufficientTokenScopeError()
                db.access.require_write(grant)
            return current

    def _grant(self, token, project_id, principal, kind):
        try:
            return self.signer.verify(
                token, project_id=project_id, user_id=principal.user_id, kind=kind
            )
        except ValueError:
            raise WebResearchConsentError("Нужен действующий подписанный публичный ввод.") from None

    def _enabled(self):
        if not self.enabled:
            raise WebResearchDisabledError("Внешнее исследование выключено.")

    @staticmethod
    def _public_literal(value: str) -> None:
        # Approval is the primary privacy boundary; obvious secrets/PII are also refused.
        if re.search(
            r"(?i)(?:bearer\s|(?:password|passwd|token|api[_-]?key|secret)\s*[:=]|"
            r"telos_(?:key|token)_|gh[pousr]_[a-z0-9]{15}|-----BEGIN |"
            r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,})",
            value,
        ):
            raise WebResearchConsentError(
                "В публичной теме обнаружены credentials или личный адрес."
            )
