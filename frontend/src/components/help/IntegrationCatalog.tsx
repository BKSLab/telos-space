import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ChevronDown, Search } from "lucide-react";
import { Input, Select } from "@/components/ui/Field";
import { EmptyState } from "@/components/ui/States";
import { helpSearchTerms } from "@/lib/helpSearch";
import { catalogEntries, catalogGroup, catalogSearchText, integrationCatalog, resolveSchema, schemaDocument, schemaType, type CatalogEntry, type CatalogKind, type JsonSchema } from "@/lib/integrationCatalog";

function SchemaFields({ schema, root = schema, depth = 0 }: { schema: JsonSchema; root?: JsonSchema; depth?: number }) {
    const value = resolveSchema(schema, root);
    const fields = value.properties;
    const variants = value.anyOf ?? value.oneOf ?? value.allOf;
    if (!fields) return <div className="min-w-0 space-y-2"><p className="text-[12px] leading-6 break-words text-muted">{schemaType(schema, root)}{value.description ? ` · ${value.description}` : ""}</p>
        {depth < 4 && value.items && <NestedSchema schema={value.items} root={root} depth={depth + 1} />}
        {depth < 4 && variants?.map((variant, index) => <div key={index} className="min-w-0 border-l border-line-subtle pl-3"><p className="text-[11px] text-muted">Вариант {index + 1}{resolveSchema(variant, root).title ? ` · ${resolveSchema(variant, root).title}` : ""}</p><SchemaFields schema={variant} root={root} depth={depth + 1} /></div>)}
    </div>;
    return <dl className="divide-y divide-line-subtle rounded-md border border-line-subtle px-3">
        {Object.entries(fields).map(([name, field]) => {
            const resolved = resolveSchema(field, root);
            const required = value.required?.includes(name);
            const constraints = ["minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "pattern"].filter(key => key in resolved).map(key => `${key}: ${JSON.stringify(resolved[key])}`);
            return <div key={name} className="min-w-0 space-y-1.5 py-3">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1"><code className="text-[12px] font-semibold break-all text-primary">{name}</code><span className="text-[11px] text-muted">{required ? "Обязательный" : "Необязательный"}</span></div>
                <p className="font-mono text-[11px] break-words text-accent">{schemaType(field, root)}</p>
                {resolved.description && <p className="text-[12px] leading-6 whitespace-pre-line text-secondary">{resolved.description}</p>}
                {Object.hasOwn(resolved, "default") && <p className="text-[11px] break-words text-muted">По умолчанию: <code>{JSON.stringify(resolved.default)}</code></p>}
                {constraints.length > 0 && <p className="font-mono text-[11px] break-all text-muted">{constraints.join(" · ")}</p>}
                {depth < 4 && (resolved.properties || resolved.items || (resolved.anyOf ?? resolved.oneOf ?? resolved.allOf)?.some(item => resolveSchema(item, root).properties)) && <NestedSchema schema={resolved.items ?? resolved} root={root} depth={depth + 1} />}
            </div>;
        })}
    </dl>;
}
function NestedSchema({ schema, root, depth }: { schema: JsonSchema; root: JsonSchema; depth: number }) {
    const [open, setOpen] = useState(false);
    return <details onToggle={event => setOpen(event.currentTarget.open)} className="pt-1">
        <summary className="cursor-pointer text-[12px] text-accent">Вложенные поля</summary>
        {open && <div className="mt-2"><SchemaFields schema={schema} root={root} depth={depth} /></div>}
    </details>;
}
function FullSchema({ schema }: { schema: JsonSchema }) {
    const [open, setOpen] = useState(false);
    return <details onToggle={event => setOpen(event.currentTarget.open)} className="min-w-0">
        <summary className="cursor-pointer py-2 text-[12px] text-accent">JSON Schema</summary>
        {open && <pre className="scrollbar-thin max-h-96 max-w-full overflow-auto rounded-md border border-line-subtle bg-sidebar p-3 text-[11px] leading-6 text-muted">{JSON.stringify(schemaDocument(schema), null, 2)}</pre>}
    </details>;
}
function SchemaBlock({ schema, title }: { schema: JsonSchema; title: string }) {
    return <section className="min-w-0 space-y-2"><h5 className="text-[12px] font-semibold text-primary">{title}</h5><SchemaFields schema={schema} /><FullSchema schema={schema} /></section>;
}
function Operation({ entry, initialOpen }: { entry: CatalogEntry; initialOpen: boolean }) {
    const [open, setOpen] = useState(initialOpen);
    return <details open={open} onToggle={event => setOpen(event.currentTarget.open)} className="min-w-0 border-t border-line-subtle">
        <summary id={entry.id} tabIndex={-1} className="flex min-h-14 cursor-pointer list-none items-start justify-between gap-3 px-3 py-4 outline-none focus-visible:rounded-md focus-visible:outline-2 focus-visible:outline-accent sm:px-4">
            <span className="flex min-w-0 flex-col gap-2">
                <span className="flex min-w-0 flex-wrap items-start gap-2">{entry.method && <span className="rounded border border-accent/25 bg-accent-soft px-1.5 py-0.5 font-mono text-[10px] font-semibold text-accent">{entry.method}</span>}<code className="min-w-0 text-[12px] font-semibold break-all text-primary">{entry.path ?? entry.name}</code></span>
                <span className="text-[13px] leading-6 text-secondary">{entry.summary}</span>
                {entry.kind === "action" && <span className="text-[11px] text-muted">Через telos_execute_project_tool</span>}
            </span><ChevronDown size={15} aria-hidden="true" className={`mt-1 shrink-0 text-muted ${open ? "rotate-180" : ""}`} />
        </summary>
        {open && <div className="min-w-0 space-y-5 border-t border-line-subtle bg-surface/25 px-3 py-5 sm:px-4">
            {entry.description && <p className="text-[13px] leading-7 whitespace-pre-line text-secondary">{entry.description}</p>}
            <section aria-label={`Доступ ${entry.name}`} className="space-y-2 rounded-md border border-line-subtle bg-control p-3 text-[12px] leading-6 text-secondary">
                <p className="font-semibold text-primary">Доступ</p>
                {entry.access === "public" ? <p>Без авторизации.</p> : entry.access === "subscription" ? <p>По секретной ссылке ICS-подписки.</p> : entry.access === "session" ? <p>Только сессия интерфейса. Внешние ключи и токены не принимаются.</p> : <>
                    <p>{entry.scopes?.length ? <>API-ключ ttk_: {entry.scopes.map(scope => <code key={scope} title={integrationCatalog.scopes[scope]} className="mr-1 inline-block rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] break-all">{scope}</code>)}{entry.scopes.length > 1 && <span>Нужны все перечисленные права.</span>}</> : "API-ключам ttk_ недоступно."}</p>
                    {entry.legacy.length > 0 && <p>Прежний токен tt_: {entry.legacy.includes("READ") ? "READ или WRITE" : "WRITE"}.</p>}
                    <p>Действует текущий доступ владельца к объекту.</p>
                </>}
                {entry.ownerOnly && <p>Требуется роль владельца проекта.</p>}
                {entry.requiresConfirmation && <p>Нужно отдельное подтверждение пользователя через MCP-клиент.</p>}
            </section>
            {entry.parameters && entry.parameters.length > 0 && <section className="space-y-2"><h5 className="text-[12px] font-semibold text-primary">Параметры запроса</h5><dl className="divide-y divide-line-subtle">{entry.parameters.map(param => <div key={`${param.in}:${param.name}`} className="space-y-1 py-2"><p className="text-[12px] break-words text-primary"><code>{param.name}</code> <span className="text-[11px] text-muted">· {param.in} · {param.required ? "обязательный" : "необязательный"} · {schemaType(param.schema ?? {})}</span></p>{param.description && <p className="text-[12px] leading-6 text-secondary">{param.description}</p>}{param.schema && <SchemaFields schema={{ type: "object", properties: { [param.name]: param.schema }, required: param.required ? [param.name] : [] }} />}</div>)}</dl></section>}
            {entry.requestBody && Object.entries(entry.requestBody.content).map(([media, value]) => <div key={media} className="space-y-2"><p className="text-[12px] text-muted">Тело запроса: {media} · {entry.requestBody?.required ? "обязательное" : "необязательное"}</p>{value.schema && <SchemaBlock schema={value.schema} title="Поля тела запроса" />}</div>)}
            {entry.inputSchema && <SchemaBlock schema={entry.inputSchema} title={entry.kind === "websocket" ? "Сообщение клиента" : "Аргументы"} />}
            {entry.outputSchema && <SchemaBlock schema={entry.outputSchema} title="Результат" />}
            {entry.responses && <section className="space-y-3"><h5 className="text-[12px] font-semibold text-primary">Объявленные ответы</h5>{Object.entries(entry.responses).map(([status, response]) => <details key={status} className="rounded-md border border-line-subtle p-3"><summary className="cursor-pointer text-[12px] leading-6 text-secondary"><code className="mr-2 font-semibold text-primary">{status}</code>{response.description === "Successful Response" ? "Успешный ответ" : response.description}</summary><div className="mt-3 space-y-4">{Object.entries(response.content ?? {}).map(([media, body]) => <div key={media} className="min-w-0 space-y-2"><p className="text-[11px] text-muted">{media}</p>{body.schema && <SchemaBlock schema={body.schema} title="Тело ответа" />}</div>)}</div></details>)}<p className="text-[11px] leading-6 text-muted">Защищённые методы также могут отказать в авторизации или доступе (401/403). Общие ошибки описаны выше.</p></section>}
        </div>}
    </details>;
}
function Group({ title, entries, activeId, initialOpen }: { title: string; entries: CatalogEntry[]; activeId: string; initialOpen: boolean }) {
    const [open, setOpen] = useState(initialOpen);
    return <details open={open} onToggle={event => setOpen(event.currentTarget.open)} className="min-w-0 overflow-hidden rounded-[var(--radius-card)] border border-line-subtle bg-surface/30">
        <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-3 px-3 py-3 text-[13px] font-semibold text-primary sm:px-4"><span>{title} <span className="ml-2 font-normal text-muted">{entries.length}</span></span><ChevronDown size={15} aria-hidden="true" className={`shrink-0 text-muted ${open ? "rotate-180" : ""}`} /></summary>
        {open && entries.map(entry => <Operation key={`${entry.id}:${activeId === entry.id}`} entry={entry} initialOpen={activeId === entry.id} />)}
    </details>;
}

export function IntegrationCatalog({ kind }: { kind: CatalogKind }) {
    const location = useLocation();
    const navigate = useNavigate();
    const [query, setQuery] = useState("");
    const [filter, setFilter] = useState("all");
    const entries = catalogEntries(kind);
    const terms = helpSearchTerms(query);
    const filtered = entries.filter(entry => terms.every(term => catalogSearchText(entry).includes(term))
        && (filter === "all" || filter === "keys" && !!entry.scopes || filter === "session" && entry.access === "session"
            || filter === "primary" && entry.kind === "mcp" || filter === "actions" && entry.kind === "action"));
    const groups = new Map<string, CatalogEntry[]>();
    for (const entry of filtered) {
        const title = catalogGroup(entry);
        const group = groups.get(title) ?? [];
        group.push(entry);
        groups.set(title, group);
    }
    const activeId = location.hash.slice(1);
    const clearAnchor = () => { if (location.hash) navigate({ pathname: location.pathname, search: location.search, hash: "" }, { replace: true }); };
    return <div className="min-w-0 space-y-4">
        <div className="flex flex-wrap items-center gap-3">
            <div className="relative min-w-0 basis-full sm:flex-1 sm:basis-auto"><Search size={15} aria-hidden="true" className="pointer-events-none absolute top-3 left-3 text-muted" /><Input type="search" aria-label={kind === "api" ? "Поиск методов API" : "Поиск инструментов MCP"} placeholder="Имя, путь, возможность или параметр" value={query} onChange={event => { clearAnchor(); setQuery(event.target.value); }} className="h-10 pl-9" /></div>
            <Select aria-label="Фильтр каталога" value={filter} onChange={event => { clearAnchor(); setFilter(event.target.value); }} className="h-10 w-full sm:w-auto">
                <option value="all">{kind === "api" ? "Все методы" : "Весь каталог"}</option><option value="keys">Для ключей ttk_</option>
                {kind === "api" ? <option value="session">Только сессия интерфейса</option> : <><option value="primary">Инструменты MCP</option><option value="actions">Действия каталога</option></>}
            </Select>
        </div>
        <p role="status" aria-label="Результат фильтра каталога" className="text-[12px] text-muted">Найдено: {filtered.length} из {entries.length}</p>
        {filtered.length === 0 && <EmptyState title="Методы не найдены" description="Измените запрос или фильтр доступа." />}
        <div className="min-w-0 space-y-3">{[...groups].map(([title, values]) => {
            const expanded = terms.length > 0 || values.some(entry => entry.id === activeId);
            return <Group key={`${title}:${expanded}`} title={title} entries={values} activeId={activeId} initialOpen={expanded} />;
        })}</div>
        <Link to="/profile?tab=keys" className="inline-flex min-h-11 items-center text-[12px] text-accent hover:text-accent-hover">Настроить ключи доступа в профиле</Link>
    </div>;
}
