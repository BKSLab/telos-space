import { useId, useRef, useState, type KeyboardEvent } from "react";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/cn";
import { fullName, type UserSummary } from "@/lib/types";
import { Input } from "@/components/ui/Field";
import { Popover } from "@/components/ui/Popover";
import { UserIdentity } from "./UserIdentity";

interface UserSelectProps {
    label: string;
    id?: string;
    users: readonly UserSummary[];
    selected: readonly UserSummary[];
    onChange: (users: UserSummary[]) => void;
    multiple?: boolean;
    disabled?: boolean;
    emptyLabel?: string;
    clearLabel?: string;
    loading?: boolean;
    error?: string | null;
    searchValue?: string;
    onSearchChange?: (value: string) => void;
    describeUser?: (user: UserSummary) => string | undefined;
    fallbackSelection?: { userId?: number | null; name: string };
    onClear?: () => void;
}

/** Options are supplied by the caller; displaying an old selection never adds access. */
export function UserSelect({
    label, id, users, selected, onChange, multiple = false, disabled = false,
    emptyLabel = "Не выбраны", clearLabel = emptyLabel, loading = false, error,
    searchValue, onSearchChange, describeUser, fallbackSelection, onClear,
}: UserSelectProps) {
    const valueId = useId();
    const [localSearch, setLocalSearch] = useState("");
    const search = searchValue ?? localSearch;
    const current = [...new Map(selected.map(user => [user.id, user])).values()];
    const choices = [...new Map(users.map(user => [user.id, user])).values()];
    const query = search.trim().toLocaleLowerCase("ru-RU");
    const filtered = onSearchChange ? choices : choices.filter(user =>
        `${fullName(user)} ${user.username}`.toLocaleLowerCase("ru-RU").includes(query));
    const selection = current.length ? current.map(user => fullName(user) || user.username).join(", ")
        : fallbackSelection?.name ?? emptyLabel;

    return <div className="min-w-0">
        <span id={valueId} className="sr-only">{selection}</span>
        <Popover label={label} align="start" disabled={disabled} triggerId={id} triggerDescriptionId={valueId}
            width={336} triggerClassName="h-auto min-h-8 w-full justify-between gap-2 py-1 text-left font-normal"
            trigger={<>
                <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
                    {current.length > 0 ? <>
                        {current.slice(0, 2).map(user => <UserIdentity key={user.id} user={user} size="xs" nameClassName="max-w-36" />)}
                        {current.length > 2 && <span className="text-xs text-muted">ещё {current.length - 2}</span>}
                    </> : fallbackSelection ? <UserIdentity userId={fallbackSelection.userId} name={fallbackSelection.name} size="xs" />
                        : <span className="text-muted">{emptyLabel}</span>}
                </span>
                <ChevronDown size={14} className="shrink-0 text-muted" aria-hidden="true" />
            </>}>
            {({ close }) => <UserSelectionPanel label={label} users={filtered} selected={current} multiple={multiple}
                disabled={disabled} clearLabel={clearLabel} hasSelection={current.length > 0 || !!fallbackSelection}
                loading={loading} error={error} search={search}
                onSearchChange={onSearchChange ?? setLocalSearch} describeUser={describeUser}
                onChange={onChange} onClear={onClear} close={close} />}
        </Popover>
    </div>;
}

interface UserSelectionPanelProps {
    label: string;
    users: UserSummary[];
    selected: UserSummary[];
    multiple: boolean;
    disabled: boolean;
    clearLabel: string;
    hasSelection: boolean;
    loading: boolean;
    error?: string | null;
    search: string;
    onSearchChange: (value: string) => void;
    describeUser?: (user: UserSummary) => string | undefined;
    onChange: (users: UserSummary[]) => void;
    onClear?: () => void;
    close: () => void;
}

function UserSelectionPanel({
    label, users, selected, multiple, disabled, clearLabel, hasSelection, loading, error,
    search, onSearchChange, describeUser, onChange, onClear, close,
}: UserSelectionPanelProps) {
    const listRef = useRef<HTMLDivElement>(null);
    const [focusedUserId, setFocusedUserId] = useState<number | null | undefined>();
    const selectedIds = new Set(selected.map(user => user.id));
    const tabStopId = (focusedUserId === null && !multiple) || users.some(user => user.id === focusedUserId)
        ? focusedUserId : users.find(user => selectedIds.has(user.id))?.id ?? (multiple ? users[0]?.id : null);
    const focusOption = (index: number) => {
        const options = listRef.current?.querySelectorAll<HTMLButtonElement>('[role="option"]:not(:disabled)');
        if (!options?.length) return;
        const option = options[(index + options.length) % options.length];
        option.focus();
        option.scrollIntoView?.({ block: "nearest" });
    };
    const navigate = (event: KeyboardEvent<HTMLDivElement>) => {
        if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            close();
            return;
        }
        const options = [...(listRef.current?.querySelectorAll<HTMLButtonElement>('[role="option"]:not(:disabled)') ?? [])];
        const currentIndex = options.indexOf(event.target as HTMLButtonElement);
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            focusOption(currentIndex < 0 ? (event.key === "ArrowDown" ? 0 : options.length - 1)
                : currentIndex + (event.key === "ArrowDown" ? 1 : -1));
        } else if (currentIndex >= 0 && (event.key === "Home" || event.key === "End")) {
            event.preventDefault();
            focusOption(event.key === "Home" ? 0 : options.length - 1);
        }
    };
    const clear = () => {
        if (disabled) return;
        if (onClear) onClear();
        else onChange([]);
        if (!multiple) close();
    };
    const select = (user: UserSummary) => {
        if (disabled) return;
        onChange(multiple ? selectedIds.has(user.id) ? selected.filter(item => item.id !== user.id) : [...selected, user] : [user]);
        if (!multiple) close();
    };

    return <div className="flex min-w-0 flex-col gap-2" onKeyDown={navigate}>
        <Input aria-label={`Поиск: ${label}`} value={search} placeholder="Имя или логин" disabled={disabled}
            className="min-h-11 sm:min-h-8" onChange={event => onSearchChange(event.target.value)} />
        {loading && <p role="status" className="px-2 text-xs text-muted">Загружаем пользователей…</p>}
        {error && <p role="alert" className="px-2 text-xs text-danger">{error}</p>}
        <div ref={listRef} role="listbox" aria-label={label} aria-multiselectable={multiple || undefined}
            className="max-h-64 overflow-y-auto overscroll-contain">
            {!multiple && <button type="button" role="option" aria-label={clearLabel} aria-selected={!hasSelection}
                tabIndex={!disabled && tabStopId === null ? 0 : -1} disabled={disabled} onClick={clear} onFocus={() => setFocusedUserId(null)}
                className="flex min-h-11 w-full items-center justify-between gap-2 rounded-control px-2 py-2 text-left text-[13px] text-muted hover:bg-surface focus-visible:bg-surface focus-visible:outline-none">
                {clearLabel}{!hasSelection && <Check size={14} aria-hidden="true" />}
            </button>}
            {users.map(user => <button key={user.id} type="button" role="option" aria-label={fullName(user) || user.username}
                aria-selected={selectedIds.has(user.id)} tabIndex={!disabled && tabStopId === user.id ? 0 : -1}
                disabled={disabled} onClick={() => select(user)} onFocus={() => setFocusedUserId(user.id)}
                className={cn("flex min-h-11 w-full items-center justify-between gap-2 rounded-control px-2 py-2 text-left text-[13px] text-secondary hover:bg-surface focus-visible:bg-surface focus-visible:outline-none", selectedIds.has(user.id) && "bg-surface")}>
                <span className="flex min-w-0 flex-col gap-0.5">
                    <UserIdentity user={user} size="sm" />
                    <span className="truncate pl-8 text-[11px] text-muted">{describeUser?.(user) ?? `@${user.username}`}</span>
                </span>
                {selectedIds.has(user.id) && <Check size={14} className="shrink-0 text-accent" aria-hidden="true" />}
            </button>)}
            {!users.length && !loading && !error && <p className="px-2 py-3 text-xs text-muted">Пользователь не найден.</p>}
        </div>
        {multiple && hasSelection && <button type="button" disabled={disabled} aria-label={`Очистить выбор: ${label}`}
            className="min-h-11 rounded-control px-2 text-left text-xs text-muted hover:bg-surface focus-visible:bg-surface focus-visible:outline-none"
            onClick={clear}>Очистить выбор</button>}
    </div>;
}
