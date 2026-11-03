import type { ReactNode } from "react";
import { CalendarDays, ChevronLeft, ChevronRight, GanttChartSquare, LayoutGrid, Rows2, SlidersHorizontal } from "lucide-react";
import { Button, IconButton } from "@/components/ui/Button";
import { Select } from "@/components/ui/Field";
import { Popover } from "@/components/ui/Popover";
import { cn } from "@/lib/cn";
import type { CalendarViewMode } from "@/lib/calendarGrid";
import { useMediaQuery } from "@/lib/useCalendarView";

interface ScaleOption<T extends string> {
    value: T;
    label: string;
    hotkey: string;
}

interface CalendarToolbarProps<T extends string> {
    title: string;
    onToday: () => void;
    onPrevious: () => void;
    onNext: () => void;
    scale: T;
    scales: ScaleOption<T>[];
    onScaleChange: (scale: T) => void;
    /** Переключатель «Сетка | Шкала»; нет — вид один. */
    view?: CalendarViewMode;
    onViewChange?: (view: CalendarViewMode) => void;
    /** Показать вариант «Рядом» (сетка + шкала): только на широком экране. */
    allowSplit?: boolean;
    /** Второй масштаб рядом с первым — в «Рядом» это масштаб шкалы. */
    extraScale?: ReactNode;
    /** Кнопки справа: фильтры, цвет, панель. */
    children?: ReactNode;
    activeFilterCount?: number;
}

export function Segmented<T extends string>({
    label,
    value,
    options,
    onChange,
}: {
    label: string;
    value: T;
    options: { value: T; label: ReactNode; title: string }[];
    onChange: (value: T) => void;
}) {
    return (
        <div className="material-metal flex rounded-[var(--radius-control)] border border-line-subtle p-0.5" role="group" aria-label={label}>
            {options.map((item) => (
                <button
                    key={item.value}
                    type="button"
                    aria-pressed={value === item.value}
                    title={item.title}
                    onClick={() => onChange(item.value)}
                    className={cn(
                        "flex h-6 items-center gap-1 rounded-[5px] px-2 text-[11px] transition-colors",
                        value === item.value ? "bg-elevated text-primary shadow-card" : "text-muted hover:text-primary",
                    )}
                >
                    {item.label}
                </button>
            ))}
        </div>
    );
}

/** Шапка календаря: период, вид, масштаб, листание; подсказки называют горячие клавиши. */
export function CalendarToolbar<T extends string>({
    title,
    onToday,
    onPrevious,
    onNext,
    scale,
    scales,
    onScaleChange,
    view,
    onViewChange,
    allowSplit,
    extraScale,
    children,
    activeFilterCount = 0,
}: CalendarToolbarProps<T>) {
    const compact = useMediaQuery("(max-width: 1023px)");
    const shortLandscape = useMediaQuery("(min-width: 640px) and (max-height: 500px)");

    if (compact) {
        return (
            <div className={cn("flex min-w-0 gap-1.5", shortLandscape ? "flex-row items-center gap-3" : "flex-col")} data-calendar-toolbar="mobile">
                <div className={cn("flex min-w-0 items-center gap-1", shortLandscape && "flex-1")}>
                    <h2 className="min-w-0 flex-1 text-[14px] font-semibold text-primary" aria-live="polite">{title}</h2>
                    <IconButton className="size-11 shrink-0" label="Предыдущий период" onClick={onPrevious}>
                        <ChevronLeft size={18} aria-hidden="true" />
                    </IconButton>
                    <IconButton className="size-11 shrink-0" label="Сегодня" title="Перейти к сегодняшнему дню" onClick={onToday}>
                        <CalendarDays size={18} aria-hidden="true" />
                    </IconButton>
                    <IconButton className="size-11 shrink-0" label="Следующий период" onClick={onNext}>
                        <ChevronRight size={18} aria-hidden="true" />
                    </IconButton>
                </div>
                <div className={cn("flex min-w-0 items-center gap-2", shortLandscape && "flex-1")}>
                    {view && onViewChange && (
                        <Select aria-label="Вид календаря" value={view} onChange={(event) => onViewChange(event.target.value as CalendarViewMode)} className="h-11 min-w-0 flex-1">
                            <option value="grid">Сетка</option>
                            <option value="timeline">Шкала</option>
                            {allowSplit && <option value="split">Рядом</option>}
                        </Select>
                    )}
                    <Select aria-label="Масштаб календаря" value={scale} onChange={(event) => onScaleChange(event.target.value as T)} className="h-11 min-w-0 flex-1">
                        {scales.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                    </Select>
                    {children && (
                        <Popover
                            label="Инструменты календаря"
                            trigger={<><SlidersHorizontal size={18} aria-hidden="true" />{activeFilterCount > 0 && <span className="font-mono text-[11px] text-accent">{activeFilterCount}</span>}</>}
                            triggerClassName="h-11 min-w-11 shrink-0 px-2"
                            className="bg-surface [backdrop-filter:none] [-webkit-backdrop-filter:none]"
                            width={340}
                        >
                            {({ close }) => (
                                <div className="flex flex-wrap items-center gap-2" onClick={(event) => {
                                    // Команда открывает свой диалог/панель. Вложенные popover и переключатели остаются доступны.
                                    const button = (event.target as HTMLElement).closest("button");
                                    if (button?.parentElement === event.currentTarget) close();
                                }}>
                                    {children}
                                </div>
                            )}
                        </Popover>
                    )}
                </div>
            </div>
        );
    }

    return (
        <div className="flex min-w-0 flex-wrap items-center gap-2">
            <h2 className="min-w-0 text-[15px] font-semibold tracking-[-0.02em] text-primary sm:min-w-44" aria-live="polite">
                {title}
            </h2>
            {view && onViewChange && (
                <Segmented
                    label="Вид календаря"
                    value={view}
                    onChange={onViewChange}
                    options={[
                        { value: "grid", label: <><LayoutGrid size={12} aria-hidden="true" /> Сетка</>, title: "Сетка: день, неделя, месяц, год — задачи в клетках дней" },
                        { value: "timeline", label: <><GanttChartSquare size={12} aria-hidden="true" /> Шкала</>, title: "Шкала (Гант): полосы задач по этапам, зависимости, «Что, если»" },
                        ...(allowSplit
                            ? [{ value: "split" as const, label: <><Rows2 size={12} aria-hidden="true" /> Рядом</>, title: "Рядом: сетка сверху, Гант снизу. Клик по дню в сетке прокручивает шкалу к нему; границу между ними можно тянуть" }]
                            : []),
                    ]}
                />
            )}
            <Segmented
                label="Масштаб"
                value={scale}
                onChange={onScaleChange}
                options={scales.map((item) => ({
                    value: item.value,
                    label: item.label,
                    title: item.hotkey ? `${item.label} — клавиша ${item.hotkey}` : item.label,
                }))}
            />
            {extraScale}
            <Button size="sm" variant="ghost" icon={<CalendarDays size={14} aria-hidden="true" />} onClick={onToday} title="Перейти к сегодняшнему дню — клавиша T / Е">
                Сегодня
            </Button>
            <div className="flex items-center">
                <IconButton label="Предыдущий период" title="Предыдущий период — клавиша ←" size="sm" onClick={onPrevious}>
                    <ChevronLeft size={14} aria-hidden="true" />
                </IconButton>
                <IconButton label="Следующий период" title="Следующий период — клавиша →" size="sm" onClick={onNext}>
                    <ChevronRight size={14} aria-hidden="true" />
                </IconButton>
            </div>
            {children && <div className="ml-auto flex flex-wrap items-center gap-1.5">{children}</div>}
        </div>
    );
}
