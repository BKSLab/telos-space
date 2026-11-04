import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { BookOpen, ChartNoAxesCombined, Check, ChevronRight, CodeXml, Copy, FolderKanban, LayoutTemplate, ListTodo, Plug, Search, X } from "lucide-react";
import { HELP_SECTIONS, type HelpArticle } from "@/content/help";
import { helpHighlights, searchHelp } from "@/lib/helpSearch";
import { cn } from "@/lib/cn";
import { buildMcpConfig, mcpServerUrl } from "@/lib/mcpConfig";
import { Button, IconButton, LinkButton } from "@/components/ui/Button";
import { Input, Select } from "@/components/ui/Field";
import { EmptyState } from "@/components/ui/States";
import { IntegrationCatalog } from "@/components/help/IntegrationCatalog";

const ICONS = [BookOpen, FolderKanban, ListTodo, LayoutTemplate, ChartNoAxesCombined, CodeXml, Plug];

function Highlight({ text, query }: { text: string; query: string }) {
    return <>{helpHighlights(text, query).map((part, index) => part.match
        ? <mark key={index} className="rounded-sm bg-accent/20 text-primary">{part.text}</mark>
        : <span key={index}>{part.text}</span>)}</>;
}

function ConnectionExample({ type }: { type: "api" | "mcp" }) {
    const [copied, setCopied] = useState(false);
    const [error, setError] = useState(false);
    const origin = window.location.origin;
    const code = type === "mcp" ? buildMcpConfig({ origin, secret: "YOUR_API_KEY" })
        : `curl -H "Authorization: Bearer YOUR_API_KEY" "${origin}/api/v1/projects"`;
    const copy = async () => {
        try { await navigator.clipboard.writeText(code); setCopied(true); setError(false); }
        catch { setError(true); }
    };
    return <div className="min-w-0 space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2 text-[12px] text-muted">
            <span>{type === "mcp" ? `Сервер: ${mcpServerUrl(origin)}` : "Пример запроса · curl"}</span>
            <Button size="sm" onClick={() => void copy()} icon={copied ? <Check size={14} /> : <Copy size={14} />} aria-label={type === "mcp" ? "Копировать конфигурацию MCP" : "Копировать пример API"}>{copied ? "Скопировано" : "Копировать"}</Button>
        </div>
        <pre className="scrollbar-thin max-w-full overflow-x-auto rounded-[var(--radius-card)] border border-line-subtle bg-sidebar p-4 text-[12px] leading-relaxed text-secondary"><code>{code}</code></pre>
        {error && <p role="alert" className="text-[12px] text-danger">Буфер обмена недоступен. Выделите пример и скопируйте вручную.</p>}
    </div>;
}

function Article({ article, sectionId }: { article: HelpArticle; sectionId: string }) {
    return <section aria-labelledby={article.id} className="min-w-0 scroll-mt-6 space-y-3 border-t border-line-subtle pt-6 first:border-t-0 first:pt-0">
        <h3 id={article.id} tabIndex={-1} className="text-[17px] font-semibold tracking-[-0.02em] text-primary outline-none">
            <Link to={`/help/${sectionId}#${article.id}`} className="rounded hover:text-accent focus-visible:outline-2 focus-visible:outline-accent">{article.title}</Link>
        </h3>
        {article.paragraphs.map((paragraph, index) => <p key={index} className="text-[14px] leading-7 text-secondary">{paragraph}</p>)}
        {article.steps && <ol className="list-decimal space-y-2 pl-5 text-[14px] leading-7 text-secondary">{article.steps.map(step => <li key={step} className="pl-1">{step}</li>)}</ol>}
        {article.bullets && <ul className="list-disc space-y-2 pl-5 text-[14px] leading-7 text-secondary">{article.bullets.map(item => <li key={item}>{item}</li>)}</ul>}
        {article.example && <ConnectionExample type={article.example} />}
        {article.catalog && <IntegrationCatalog kind={article.catalog} />}
        {article.links && <div className="flex flex-wrap gap-2 pt-1">{article.links.map(link => <LinkButton key={link.to} to={link.to} size="sm" variant="secondary">{link.label}<ChevronRight size={14} aria-hidden="true" /></LinkButton>)}</div>}
    </section>;
}

export function HelpPage() {
    const navigate = useNavigate();
    const { sectionId = "about" } = useParams();
    const [params, setParams] = useSearchParams();
    const location = useLocation();
    const query = params.get("q") ?? "";
    const searching = query.trim().length > 0;
    const section = HELP_SECTIONS.find(item => item.id === sectionId);
    const results = searching ? searchHelp(query) : [];
    const content = useRef<HTMLDivElement>(null);
    const updateSearch = (value: string) => setParams(value ? { q: value } : {}, { replace: true });

    useEffect(() => {
        const frame = requestAnimationFrame(() => {
            const container = content.current;
            if (!container) return;
            let id = location.hash.slice(1);
            try { id = decodeURIComponent(id); } catch { /* An invalid fragment is treated as plain text. */ }
            const target = id && !searching ? document.getElementById(id) : null;
            if (target && container.contains(target)) {
                container.scrollTop += target.getBoundingClientRect().top - container.getBoundingClientRect().top - 24;
                target.focus({ preventScroll: true });
            } else container.scrollTop = 0;
        });
        return () => cancelAnimationFrame(frame);
    }, [location.hash, sectionId, searching, query]);

    return <div className="flex h-full min-h-0 min-w-0 flex-col">
        <header className="material-metal flex shrink-0 flex-col gap-4 border-b border-line px-3 py-4 sm:px-6 sm:py-5">
            <div className="flex flex-wrap items-center justify-between gap-4">
                <div className="space-y-1"><h1 className="text-[24px] font-semibold tracking-[-0.03em] text-primary">Справка</h1><p className="text-[13px] text-muted">Руководство по TELOS SPACE</p></div>
                <div className="relative w-full sm:max-w-md">
                    <Search size={17} aria-hidden="true" className="pointer-events-none absolute top-3 left-3 text-muted" />
                    <Input type="search" aria-label="Поиск по справке" placeholder="Найти во всех разделах справки" maxLength={160} value={query} onChange={event => updateSearch(event.target.value)} className="h-10 pr-11 pl-9" />
                    {query && <IconButton label="Очистить поиск по справке" size="sm" onClick={() => updateSearch("")} className="absolute top-1 right-1"><X size={15} /></IconButton>}
                </div>
            </div>
            <div className="lg:hidden"><Select aria-label="Раздел справки" value={section?.id ?? "about"} onChange={event => navigate(`/help/${event.target.value}`)}>
                {HELP_SECTIONS.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
            </Select></div>
        </header>
        <div className="flex min-h-0 min-w-0 flex-1">
            <nav aria-label="Разделы справки" className="scrollbar-thin hidden w-52 shrink-0 flex-col gap-1 overflow-y-auto border-r border-line-subtle p-3 lg:flex">
                {HELP_SECTIONS.map((item, index) => {
                    const Icon = ICONS[index];
                    return <Link id={`help-nav-${item.id}`} key={item.id} to={`/help/${item.id}`} aria-current={!searching && item.id === sectionId ? "page" : undefined}
                        className={cn("brand-side-nav relative flex min-h-10 items-center gap-2.5 rounded-md px-3 py-2 text-[13px] before:absolute before:top-2 before:bottom-2 before:left-0 before:w-0.5 before:rounded-full focus-visible:outline-2 focus-visible:outline-accent", !searching && item.id === sectionId ? "bg-accent-soft text-primary before:bg-accent" : "text-muted hover:bg-hover hover:text-primary")}>
                        <Icon size={16} aria-hidden="true" />{item.title}
                    </Link>;
                })}
            </nav>
            <div ref={content} className="scrollbar-thin min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-y-contain px-3 py-5 pb-24 sm:px-6 sm:py-7">
                <div className="mx-auto flex w-full max-w-4xl min-w-0 flex-col gap-7">
                    {searching ? <>
                        <div className="space-y-2"><h2 className="text-[20px] font-semibold text-primary">Поиск по справке</h2><p role="status" aria-label="Результаты поиска по справке" className="text-[13px] text-muted">Найдено материалов: {results.length} · по всем разделам</p></div>
                        {results.length ? <ul className="divide-y divide-line-subtle">{results.map(result => <li key={`${result.section.id}/${result.article.id}`}>
                            <Link to={`/help/${result.section.id}#${result.article.id}`} aria-label={`${result.section.title} · ${result.article.title}`} className="flex min-w-0 flex-col gap-2 rounded-md px-2 py-5 hover:bg-hover focus-visible:outline-2 focus-visible:outline-accent">
                                <span className="text-[12px] text-muted">{result.section.title}</span>
                                <h3 aria-label={result.article.title} className="text-[16px] font-semibold break-words text-primary"><Highlight text={result.article.title} query={query} /></h3>
                                <p className="text-[13px] leading-6 break-words text-secondary"><Highlight text={result.excerpt} query={query} /></p>
                            </Link>
                        </li>)}</ul> : <EmptyState title="Ничего не найдено" description="Попробуйте название сущности или действия: например, «чек-лист», «риски» или «MCP»." action={<Button onClick={() => updateSearch("")}>Сбросить поиск</Button>} />}
                    </> : section ? <>
                        <div className="space-y-3"><p className="text-[11px] font-medium tracking-[0.08em] text-muted uppercase">Руководство</p><h2 className="text-[24px] font-semibold tracking-[-0.03em] text-primary">{section.title}</h2><p className="text-[14px] leading-7 text-muted">{section.description}</p></div>
                        <nav aria-label="В этом разделе" className="flex flex-wrap gap-x-5 gap-y-2 border-b border-line-subtle pb-5">{section.articles.map(article => <Link key={article.id} to={`/help/${section.id}#${article.id}`} className="text-[12px] text-accent hover:text-accent-hover">{article.title}</Link>)}</nav>
                        <div className="min-w-0 space-y-7">{section.articles.map(article => <Article key={article.id} article={article} sectionId={section.id} />)}</div>
                    </> : <EmptyState title="Раздел справки не найден" description="Выберите раздел руководства или воспользуйтесь поиском." action={<LinkButton to="/help/about">Общие сведения</LinkButton>} />}
                </div>
            </div>
        </div>
    </div>;
}
