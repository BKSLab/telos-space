import type { ChatMessageReference } from "@/lib/types";

/** Ссылки ведут в собственные маршруты приложения, а не в текст, выбранный моделью. */
export function chatMessageReferenceHref(reference: ChatMessageReference): string | null {
    if (reference.state === "unavailable") return null;
    if (!Number.isSafeInteger(reference.message_id) || reference.message_id <= 0) return null;
    const params = new URLSearchParams();
    const projectPath = reference.project_key ? `/projects/${encodeURIComponent(reference.project_key)}` : null;
    if (reference.kind === "project_chat") {
        if (!projectPath) return null;
        params.set("message", String(reference.message_id));
        return `${projectPath}/chat?${params}`;
    }
    if (!Number.isSafeInteger(reference.chat_id) || reference.chat_id <= 0) return null;
    // Проектный приватный диалог не становится личным при скрытом ключе проекта.
    if (reference.project_id != null && !projectPath) return null;
    params.set("conversation", String(reference.chat_id));
    params.set("message", String(reference.message_id));
    return `${projectPath ? `${projectPath}/conversations` : "/chat"}?${params}`;
}

export function chatMessageId(value: string | null): number | null {
    const id = Number(value);
    return Number.isSafeInteger(id) && id > 0 ? id : null;
}
