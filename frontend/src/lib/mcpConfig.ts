import type { ApiToken, ApiTokenScope } from "@/lib/types";

/** Плейсхолдер вместо секрета: показать токен повторно невозможно. */
export const SECRET_PLACEHOLDER = "ВСТАВЬТЕ_СЮДА_ТОКЕН";

/** Инструменты, доступные токену с правом только на чтение. */
export const READ_TOOLS = [
    "list_projects",
    "get_project",
    "list_tasks",
    "get_task",
    "list_comments",
    "search_tasks",
    "search_project_knowledge",
    "read_project_knowledge",
] as const;

/** Инструменты, доступные дополнительно токену с правом записи. */
export const WRITE_TOOLS = [
    "create_task",
    "update_task",
    "move_task",
    "delete_task",
    "add_comment",
] as const;

/**
 * Возвращает адрес MCP-сервера для текущего размещения трекера.
 *
 * Берётся origin открытой страницы: пользователь копирует конфигурацию там же,
 * где работает с трекером, поэтому подставлять чужой адрес было бы ошибкой.
 */
export function mcpServerUrl(origin: string, mcpPath = "/mcp"): string {
    const normalized = origin.replace(/\/+$/, "");
    return `${normalized}${mcpPath}`;
}

/**
 * Собирает готовый к вставке фрагмент конфигурации MCP-клиента.
 *
 * Секрет подставляется только сразу после выпуска: в остальное время в
 * конфигурации стоит плейсхолдер, потому что показать токен второй раз нельзя.
 */
export function buildMcpConfig(options: {
    origin: string;
    secret?: string | null;
    mcpPath?: string;
}): string {
    const url = mcpServerUrl(options.origin, options.mcpPath);
    const secret = options.secret?.trim() || SECRET_PLACEHOLDER;
    return JSON.stringify(
        {
            mcpServers: {
                "telos-space": {
                    type: "http",
                    url,
                    headers: { Authorization: `Bearer ${secret}` },
                },
            },
        },
        null,
        2,
    );
}

/** Возвращает список инструментов, разрешённых токену с такими правами. */
export function toolsForScope(scope: ApiTokenScope): string[] {
    return scope === "WRITE" ? [...READ_TOOLS, ...WRITE_TOOLS] : [...READ_TOOLS];
}

/** Человекочитаемое описание прав токена. */
export function scopeLabel(scope: ApiTokenScope): string {
    return scope === "WRITE" ? "Чтение и запись" : "Только чтение";
}

/** Проверяет, действует ли токен: не отозван и не истёк. */
export function isTokenActive(token: ApiToken, now: Date = new Date()): boolean {
    if (token.revoked_at !== null) {
        return false;
    }
    if (token.expires_at === null) {
        return true;
    }
    return new Date(token.expires_at).getTime() > now.getTime();
}

/** Возвращает состояние токена для отображения в списке. */
export function tokenState(token: ApiToken, now: Date = new Date()): string {
    if (token.revoked_at !== null) {
        return "Отозван";
    }
    if (token.expires_at !== null && new Date(token.expires_at).getTime() <= now.getTime()) {
        return "Истёк";
    }
    return "Действует";
}
