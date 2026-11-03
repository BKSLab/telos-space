import { useEffect, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { Search, X } from "lucide-react";
import { api } from "@/lib/api";
import { chatAuthorName, chatKeys, chatPath, type ChatMessagePage } from "@/lib/projectChat";
import { useProjectChat } from "@/lib/useProjectChat";
import { Button, IconButton } from "@/components/ui/Button";
import { cn } from "@/lib/cn";

export function ChatSearch({ onJump, onClose, compact = false }: { onJump: (id: number) => void; onClose: () => void; compact?: boolean }) {
    const { projectId, userId } = useProjectChat();
    const [text, setText] = useState("");
    const [query, setQuery] = useState("");
    useEffect(() => { const timer = setTimeout(() => setQuery(text.trim()), 250); return () => clearTimeout(timer); }, [text]);
    const search = useInfiniteQuery({
        queryKey: [...chatKeys.search(projectId, userId), query],
        queryFn: ({ pageParam }) => api.get<ChatMessagePage>(`${chatPath(projectId)}/search?q=${encodeURIComponent(query)}${pageParam ? `&before=${pageParam}` : ""}`),
        initialPageParam: 0,
        getNextPageParam: (page) => page.has_more ? page.messages[0]?.seq : undefined,
        enabled: query.length >= 2,
    });
    const results = search.data?.pages.flatMap((page) => [...page.messages].reverse()) ?? [];
    return <aside aria-label="Поиск по сообщениям" className={cn("chat-search material-mineral absolute inset-y-0 right-0 z-20 flex max-w-full flex-col border-l border-line-subtle shadow-panel", compact ? "w-full" : "w-80 lg:relative lg:shrink-0 lg:shadow-none")}>
        <div className="flex items-center gap-2 border-b border-line-subtle p-3"><Search size={14} className="shrink-0 text-muted" /><input type="search" autoFocus value={text} onChange={(event) => setText(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); onClose(); } }} maxLength={200} aria-label="Поиск по сообщениям" placeholder="Найти в переписке…" className="min-h-11 min-w-0 flex-1 bg-transparent text-base text-primary outline-none sm:min-h-8 sm:text-sm" /><IconButton label="Закрыть поиск" size="sm" onClick={onClose}><X size={13} /></IconButton></div>
        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto overscroll-contain p-2">
            {query.length < 2 && <p className="px-2 py-3 text-xs text-muted">Введите хотя бы два символа.</p>}
            {query.length >= 2 && search.isFetching && !results.length && <p role="status" className="px-2 py-3 text-xs text-muted">Поиск…</p>}
            {query.length >= 2 && !search.isFetching && !results.length && !search.error && <p className="px-2 py-3 text-xs text-muted">Сообщения не найдены.</p>}
            {search.error && <p role="alert" className="px-2 py-3 text-xs text-danger">{search.error.message}</p>}
            {results.map((message) => <button key={message.id} type="button" className="block w-full rounded px-2 py-3 text-left hover:bg-hover" onClick={() => onJump(message.id)}><span className="flex justify-between gap-2 text-[10px] text-muted"><span className="truncate font-medium text-secondary">{chatAuthorName(message)}</span><time className="shrink-0">{new Date(message.created_at).toLocaleDateString("ru-RU")}</time></span><span className="mt-1 block line-clamp-4 text-xs leading-relaxed break-words text-primary">{message.content}</span></button>)}
            {search.hasNextPage && <Button variant="ghost" size="sm" disabled={search.isFetchingNextPage} onClick={() => void search.fetchNextPage()}>Ещё результаты</Button>}
        </div>
    </aside>;
}
