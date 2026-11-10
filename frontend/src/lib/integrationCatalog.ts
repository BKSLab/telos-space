import raw from "@/content/integrations.generated.json";

export interface JsonSchema {
    [key: string]: unknown;
    $ref?: string;
    $defs?: Record<string, JsonSchema>;
    type?: string | string[];
    title?: string;
    description?: string;
    properties?: Record<string, JsonSchema>;
    required?: string[];
    items?: JsonSchema;
    anyOf?: JsonSchema[];
    oneOf?: JsonSchema[];
    allOf?: JsonSchema[];
    enum?: unknown[];
    default?: unknown;
    format?: string;
}
export interface CatalogParameter {
    name: string;
    in: string;
    required?: boolean;
    description?: string;
    schema?: JsonSchema;
}
interface Media { schema?: JsonSchema }
export interface CatalogEntry {
    id: string;
    kind: "http" | "websocket" | "mcp" | "action";
    name: string;
    summary: string;
    description: string;
    group: string;
    method?: string;
    path?: string;
    operationId?: string;
    access?: "public" | "subscription" | "session" | "authenticated";
    scopes: string[] | null;
    legacy: string[];
    write?: boolean;
    requiresConfirmation?: boolean;
    ownerOnly?: boolean;
    parameters?: CatalogParameter[];
    inputSchema?: JsonSchema;
    outputSchema?: JsonSchema | null;
    requestBody?: { required?: boolean; content: Record<string, Media> } | null;
    responses?: Record<string, { description?: string; content?: Record<string, Media> }>;
}
export interface IntegrationCatalog {
    version: number;
    scopes: Record<string, string>;
    api: CatalogEntry[];
    mcp: CatalogEntry[];
    actions: CatalogEntry[];
    schemas: Record<string, JsonSchema>;
}
export const integrationCatalog = raw as unknown as IntegrationCatalog;
export type CatalogKind = "api" | "mcp";

const GROUPS: Record<string, string> = {
    projects: "Проекты", "project-members": "Команда", "project-stages": "Стадии",
    tasks: "Задачи", "task-comments": "Комментарии", comments: "Комментарии",
    "task-reports": "Итоговые отчёты", reports: "Итоговые отчёты",
    "task-activity": "История задач", "task-attention": "Персональное внимание",
    "task-attachments": "Файлы задач", "task-documents": "Файлы задач",
    "task-dependencies": "Зависимости", milestones: "Вехи",
    calendar: "Календарь и вехи", "calendar-scenarios": "Перепланирование",
    "project-risks": "Риски", risks: "Риски", wbs: "Структура работ", structure: "Структура работ",
    "free-board": "Доски", "project-board": "Доски", "board-editor": "Доски", boards: "Доски",
    documents: "Документы", "document-links": "Связи документов",
    "project knowledge": "Знания проекта", knowledge: "Знания проекта",
    "project chat": "Общий чат проекта", "project agent": "Проектные диалоги Ариса",
    agent: "Внешний вызов Ариса", tim: "Личный Арис",
    dashboard: "Аналитика", analytics: "Аналитика",
    auth: "Вход и сессия", users: "Профиль и пользователи",
    "personal api keys": "API-ключи", "api tokens": "Прежние токены",
    websocket: "WebSocket",
    "project-actions": "Каталог действий проекта",
};

export function catalogGroup(entry: CatalogEntry): string {
    const group = GROUPS[entry.group] ?? entry.group;
    return entry.kind === "action" ? `Действия · ${group}` : group;
}
export function catalogEntries(kind: CatalogKind): CatalogEntry[] {
    return kind === "api" ? integrationCatalog.api : [...integrationCatalog.mcp, ...integrationCatalog.actions];
}
export function resolveSchema(schema: JsonSchema, root: JsonSchema = schema, seen = new Set<string>()): JsonSchema {
    if (!schema.$ref || seen.has(schema.$ref)) return schema;
    seen.add(schema.$ref);
    const name = schema.$ref.split("/").at(-1)!;
    const target = schema.$ref.startsWith("#/components/schemas/") ? integrationCatalog.schemas[name] : root.$defs?.[name];
    return target ? { ...resolveSchema(target, root, seen), ...Object.fromEntries(Object.entries(schema).filter(([key]) => key !== "$ref")) } : schema;
}
export function schemaType(schema: JsonSchema, root: JsonSchema = schema, depth = 0): string {
    if (depth > 6) return schema.$ref?.split("/").at(-1) ?? "object";
    const value = resolveSchema(schema, root);
    if (Object.hasOwn(value, "const")) return JSON.stringify(value.const);
    if (value.enum) return value.enum.map(item => JSON.stringify(item)).join(" | ");
    const variants = value.anyOf ?? value.oneOf ?? value.allOf;
    if (variants) return [...new Set(variants.map(item => schemaType(item, root, depth + 1)))].join(" | ");
    if (value.type === "array") return `array<${schemaType(value.items ?? {}, root, depth + 1)}>`;
    if (Array.isArray(value.type)) return value.type.join(" | ");
    return value.type ? `${value.type}${value.format ? ` (${value.format})` : ""}` : value.$ref?.split("/").at(-1) ?? "object";
}

const searchCache = new WeakMap<CatalogEntry, string>();
function normalize(text: string): string { return text.toLocaleLowerCase("ru-RU").replaceAll("ё", "е"); }
function schemaText(value: unknown, root: JsonSchema | undefined, seen: Set<string>, depth = 0): string {
    if (value == null || depth > 20) return "";
    if (typeof value !== "object") return String(value);
    const object = value as JsonSchema;
    const pieces: string[] = [];
    if (object.$ref && !seen.has(object.$ref)) {
        seen.add(object.$ref);
        const name = object.$ref.split("/").at(-1)!;
        const target = object.$ref.startsWith("#/components/schemas/") ? integrationCatalog.schemas[name] : root?.$defs?.[name];
        if (target) pieces.push(schemaText(target, root, seen, depth + 1));
    }
    for (const [key, child] of Object.entries(value)) {
        pieces.push(key, schemaText(child, root, seen, depth + 1));
    }
    return pieces.join(" ");
}

/** Only attach definitions actually referenced by this operation. */
export function schemaDocument(schema: JsonSchema): { schema: JsonSchema; definitions: Record<string, JsonSchema> } {
    const definitions: Record<string, JsonSchema> = {};
    const visit = (value: unknown) => {
        if (!value || typeof value !== "object") return;
        const reference = (value as JsonSchema).$ref;
        if (reference?.startsWith("#/components/schemas/")) {
            const name = reference.split("/").at(-1)!;
            if (!(name in definitions) && integrationCatalog.schemas[name]) {
                definitions[name] = integrationCatalog.schemas[name];
                visit(definitions[name]);
            }
        }
        for (const child of Object.values(value)) visit(child);
    };
    visit(schema);
    return { schema, definitions };
}
export function catalogSearchText(entry: CatalogEntry): string {
    let text = searchCache.get(entry);
    if (text === undefined) {
        text = normalize(`${entry.name} ${entry.summary} ${entry.description} ${catalogGroup(entry)} ${entry.scopes?.join(" ") ?? ""} ${entry.legacy.join(" ")} ${schemaText(entry, entry.inputSchema, new Set())}`);
        searchCache.set(entry, text);
    }
    return text;
}
export function findCatalogEntries(kind: CatalogKind, terms: string[]): CatalogEntry[] {
    return catalogEntries(kind).filter(entry => terms.every(term => catalogSearchText(entry).includes(term)));
}
