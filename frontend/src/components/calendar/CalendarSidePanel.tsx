import { useEffect, useId, useRef, type ReactNode } from "react";
import { X } from "lucide-react";
import { IconButton } from "@/components/ui/Button";
import { Drawer } from "@/components/ui/Drawer";
import { cn } from "@/lib/cn";
import { useMediaQuery } from "@/lib/useCalendarView";

export interface SidePanelTab<T extends string> {
    id: T;
    label: string;
    title: string;
    count?: number;
    content: ReactNode;
}

/**
 * Выдвижная панель справа от календаря со вкладками. На широком экране — колонка со своей
 * прокруткой, на телефоне — поверх календаря. Сверху можно положить блок (сценарий «Что, если»).
 */
export function CalendarSidePanel<T extends string>({
    tabs,
    active,
    onActiveChange,
    onClose,
    top,
}: {
    tabs: SidePanelTab<T>[];
    active: T;
    onActiveChange: (tab: T) => void;
    onClose: () => void;
    top?: ReactNode;
}) {
    const compact = useMediaQuery("(max-width: 1023px)");
    const id = useId();
    const tabsRef = useRef<HTMLDivElement>(null);
    const current = tabs.find((tab) => tab.id === active) ?? tabs[0];
    useEffect(() => {
        tabsRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
    }, [active]);

    const tabList = (
        <div ref={tabsRef} className="scrollbar-thin flex min-w-0 flex-1 gap-0.5 overflow-x-auto" role="tablist" aria-label="Разделы панели">
            {tabs.map((tab, index) => (
                <button
                    key={tab.id}
                    id={`${id}-${tab.id}`}
                    type="button"
                    role="tab"
                    aria-selected={tab.id === current.id}
                    aria-controls={`${id}-panel`}
                    tabIndex={tab.id === current.id ? 0 : -1}
                    title={tab.title}
                    onClick={() => onActiveChange(tab.id)}
                    onKeyDown={(event) => {
                        const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length : null;
                        if (next === null) return;
                        event.preventDefault();
                        onActiveChange(tabs[next].id);
                        document.getElementById(`${id}-${tabs[next].id}`)?.focus();
                    }}
                    className={cn(
                        "flex h-11 shrink-0 items-center gap-1 rounded-[6px] px-2 text-[12px] transition-colors lg:h-7",
                        tab.id === current.id ? "bg-active font-semibold text-primary" : "text-muted hover:bg-hover hover:text-primary",
                    )}
                >
                    {tab.label}
                    {tab.count !== undefined && tab.count > 0 && <span className="font-mono text-[10px] text-muted">{tab.count}</span>}
                </button>
            ))}
        </div>
    );
    const content = (
        <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto p-3" role="tabpanel" id={`${id}-panel`} aria-labelledby={`${id}-${current.id}`}>
            {top && <div className="mb-3">{top}</div>}
            {current.content}
        </div>
    );

    if (compact) {
        return <Drawer label="Панель календаря" header={tabList} isOpen onClose={onClose} closeLabel="Свернуть панель">{content}</Drawer>;
    }

    return (
        <aside
            aria-label="Панель календаря"
            className={cn(
                "flex min-h-0 flex-col bg-surface",
                "w-[340px] shrink-0 border-l border-line",
            )}
        >
            <div className="flex shrink-0 items-center gap-1 border-b border-line px-2 py-1.5">
                {tabList}
                <IconButton size="sm" label="Свернуть панель" title="Свернуть панель — календарь займёт всю ширину" onClick={onClose}>
                    <X size={14} aria-hidden="true" />
                </IconButton>
            </div>
            {content}
        </aside>
    );
}
