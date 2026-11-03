import {
    useCallback,
    useEffect,
    useId,
    useLayoutEffect,
    useRef,
    useState,
    type ReactNode,
} from "react";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/Button";

interface PopoverProps {
    label: string;
    trigger: ReactNode;
    children: ReactNode | ((controls: { close: () => void }) => ReactNode);
    align?: "start" | "end";
    className?: string;
    triggerClassName?: string;
    width?: number;
    onOpenChange?: (isOpen: boolean) => void;
    disabled?: boolean;
}

/**
 * Компактная floating surface для вторичных инструментов. Закрывается по
 * Escape/клику снаружи и возвращает фокус на trigger.
 */
export function Popover({
    label,
    trigger,
    children,
    align = "end",
    className,
    triggerClassName,
    width: panelWidth = 288,
    onOpenChange,
    disabled = false,
}: PopoverProps) {
    const [isOpen, setOpen] = useState(false);
    const id = useId();
    const rootRef = useRef<HTMLDivElement>(null);
    const triggerRef = useRef<HTMLButtonElement>(null);
    const panelRef = useRef<HTMLDivElement>(null);
    const [position, setPosition] = useState<{ left: number; top: number; width: number; maxHeight: number } | null>(
        null,
    );

    useLayoutEffect(() => {
        if (!isOpen) return;
        const updatePosition = () => {
            const triggerElement = triggerRef.current;
            const panelElement = panelRef.current;
            if (!triggerElement || !panelElement) return;
            const triggerRect = triggerElement.getBoundingClientRect();
            const margin = 12;
            const viewport = window.visualViewport;
            const viewportLeft = viewport?.offsetLeft ?? 0;
            const viewportTop = viewport?.offsetTop ?? 0;
            const viewportWidth = viewport?.width ?? window.innerWidth;
            const viewportHeight = viewport?.height ?? window.innerHeight;
            const width = Math.min(panelWidth, viewportWidth - margin * 2);
            const preferredLeft =
                align === "end" ? triggerRect.right - width : triggerRect.left;
            const left = Math.max(
                viewportLeft + margin,
                Math.min(preferredLeft, viewportLeft + viewportWidth - width - margin),
            );
            const below = triggerRect.bottom + 6;
            const above = triggerRect.top - panelElement.offsetHeight - 6;
            const preferredTop =
                below + panelElement.offsetHeight <= viewportTop + viewportHeight - margin || above < viewportTop + margin
                    ? below
                    : above;
            const maxHeight = Math.max(44, viewportHeight - margin * 2);
            const top = Math.max(viewportTop + margin, Math.min(preferredTop, viewportTop + viewportHeight - Math.min(panelElement.offsetHeight, maxHeight) - margin));
            setPosition({ left, top, width, maxHeight });
        };
        updatePosition();
        window.addEventListener("resize", updatePosition);
        window.addEventListener("scroll", updatePosition, true);
        window.visualViewport?.addEventListener("resize", updatePosition);
        window.visualViewport?.addEventListener("scroll", updatePosition);
        const observer = new ResizeObserver(updatePosition);
        if (panelRef.current) observer.observe(panelRef.current);
        return () => {
            window.removeEventListener("resize", updatePosition);
            window.removeEventListener("scroll", updatePosition, true);
            window.visualViewport?.removeEventListener("resize", updatePosition);
            window.visualViewport?.removeEventListener("scroll", updatePosition);
            observer.disconnect();
        };
    }, [align, isOpen, panelWidth]);

    const changeOpen = useCallback((open: boolean) => {
        setOpen(open);
        onOpenChange?.(open);
    }, [onOpenChange]);

    const close = () => {
        changeOpen(false);
        window.requestAnimationFrame(() => {
            document.getElementById(`${id}-trigger`)?.focus();
        });
    };

    useEffect(() => {
        if (!isOpen) return;

        const frame = window.requestAnimationFrame(() => {
            panelRef.current
                ?.querySelector<HTMLElement>(
                    "input:not([disabled]), select:not([disabled]), button:not([disabled]), [tabindex='0']",
                )
                ?.focus();
        });
        const onPointerDown = (event: PointerEvent) => {
            if (!rootRef.current?.contains(event.target as Node)) changeOpen(false);
        };
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                event.preventDefault();
                changeOpen(false);
                triggerRef.current?.focus();
            }
        };
        window.addEventListener("pointerdown", onPointerDown);
        window.addEventListener("keydown", onKeyDown);
        return () => {
            window.cancelAnimationFrame(frame);
            window.removeEventListener("pointerdown", onPointerDown);
            window.removeEventListener("keydown", onKeyDown);
        };
    }, [changeOpen, isOpen]);

    return (
        <div ref={rootRef} className="relative">
            <Button
                ref={triggerRef}
                id={`${id}-trigger`}
                size="sm"
                aria-haspopup="dialog"
                aria-label={label}
                aria-expanded={isOpen}
                aria-controls={isOpen ? id : undefined}
                disabled={disabled}
                className={triggerClassName}
                onClick={() => changeOpen(!isOpen)}
            >
                {trigger}
            </Button>
            {isOpen && (
                <div
                    ref={panelRef}
                    id={id}
                    role="dialog"
                    aria-label={label}
                    style={position ?? { visibility: "hidden" }}
                    className={cn(
                        "material-glass fixed z-[60] overflow-y-auto overscroll-contain",
                        "rounded-[var(--radius-panel)] p-3 shadow-panel",
                        className,
                    )}
                >
                    {typeof children === "function" ? children({ close }) : children}
                </div>
            )}
        </div>
    );
}
