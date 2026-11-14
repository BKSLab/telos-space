import type { DocumentPage } from "@/lib/types";
import { Button } from "@/components/ui/Button";

export function DocumentPagination({ page, busy, onOffsetChange }: { page: DocumentPage; busy: boolean; onOffsetChange: (offset: number) => void }) {
    if (page.total <= page.limit && page.offset === 0) return null;
    return <nav aria-label="Страницы документов" className="flex flex-wrap items-center gap-2">
        <Button disabled={page.offset === 0 || busy} onClick={() => onOffsetChange(Math.max(0, page.offset - page.limit))}>Назад</Button>
        <span className="text-[12px] text-muted">{page.total === 0 ? 0 : page.offset + 1}–{Math.min(page.offset + page.items.length, page.total)} из {page.total}</span>
        <Button disabled={page.next_offset === null || busy} onClick={() => { if (page.next_offset !== null) onOffsetChange(page.next_offset); }}>Далее</Button>
    </nav>;
}
