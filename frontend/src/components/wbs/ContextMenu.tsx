import { useEffect, useRef } from "react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";

export interface ContextMenuItem {
    key: string;
    label: string;
    icon?: LucideIcon;
    tone?: "default" | "danger";
    disabled?: boolean;
    onSelect: () => void;
}

interface ContextMenuProps {
    anchor: { x: number; y: number };
    items: ContextMenuItem[];
    onClose: () => void;
    label: string;
}

/**
 * Контекстное меню узла (§39 ТЗ): альтернатива drag & drop, доступная
 * с клавиатуры. Закрывается по Escape и клику вне меню.
 */
export function ContextMenu({ anchor, items, onClose, label }: ContextMenuProps) {
    const menuRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const trigger = document.activeElement;
        menuRef.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
        return () => { if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus(); };
    }, []);

    useEffect(() => {
        function handleKeyDown(event: KeyboardEvent) {
            if (event.key === "Escape") {
                onClose();
            }
        }
        function handlePointerDown(event: PointerEvent) {
            if (!menuRef.current?.contains(event.target as Node)) {
                onClose();
            }
        }
        window.addEventListener("keydown", handleKeyDown);
        window.addEventListener("pointerdown", handlePointerDown);
        return () => {
            window.removeEventListener("keydown", handleKeyDown);
            window.removeEventListener("pointerdown", handlePointerDown);
        };
    }, [onClose]);

    return (
        <div
            ref={menuRef}
            role="menu"
            aria-label={label}
            style={{
                left: Math.max(8, Math.min(anchor.x, window.innerWidth - 220)),
                top: Math.max(8, Math.min(anchor.y, window.innerHeight - (items.length + 1) * 44 - 16)),
            }}
            className="wbs-context-menu glass fixed z-[70] flex w-52 flex-col rounded-md p-1 shadow-panel"
            onKeyDown={event => {
                const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)")];
                const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
                const next = event.key === "ArrowDown" ? (index + 1) % buttons.length : event.key === "ArrowUp" ? (index + buttons.length - 1) % buttons.length : event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : null;
                if (next !== null) { event.preventDefault(); buttons[next]?.focus(); }
            }}
        >
            {items.map((item) => (
                <button
                    key={item.key}
                    type="button"
                    role="menuitem"
                    disabled={item.disabled}
                    onClick={() => {
                        item.onSelect();
                        onClose();
                    }}
                    className={cn(
                        "flex items-center gap-2 rounded-sm px-2 py-1.5 text-left text-[13px]",
                        "transition-colors duration-[var(--duration-fast)]",
                        "disabled:cursor-not-allowed disabled:opacity-50",
                        item.tone === "danger"
                            ? "text-danger hover:bg-danger/10"
                            : "text-secondary hover:bg-hover hover:text-primary",
                    )}
                >
                    {item.icon && <item.icon size={14} aria-hidden="true" className="shrink-0" />}
                    {item.label}
                </button>
            ))}
            <button type="button" role="menuitem" className="wbs-menu-close rounded-sm px-2 py-2 text-left text-[13px] text-muted hover:bg-hover" onClick={onClose}>Закрыть меню</button>
        </div>
    );
}
