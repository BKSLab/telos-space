import type {
    InputHTMLAttributes,
    ReactNode,
    SelectHTMLAttributes,
    TextareaHTMLAttributes,
} from "react";
import { useCallback, useId, useLayoutEffect, useRef } from "react";
import { cn } from "@/lib/cn";

const control =
    "control-surface w-full rounded-[var(--radius-control)] border border-line px-3 text-[13px] text-primary " +
    "placeholder:text-muted transition-[border-color,background-color,box-shadow] " +
    "duration-[var(--duration-fast)] ease-[var(--ease-standard)] " +
    "hover:border-line-strong focus-visible:border-accent/70 focus-visible:bg-surface-2 " +
    "focus-visible:shadow-focus focus-visible:outline-none aria-[invalid=true]:border-danger/60 " +
    "disabled:cursor-not-allowed disabled:bg-white/[0.015] disabled:text-disabled disabled:opacity-60";

interface FieldProps {
    label: string;
    hint?: string;
    error?: string;
    children: (id: string) => ReactNode;
    className?: string;
}

/** Обёртка поля: связывает подпись, подсказку и контрол одним идентификатором. */
export function Field({ label, hint, error, children, className }: FieldProps) {
    const id = useId();
    return (
        <div className={cn("flex flex-col gap-1.5", className)}>
            <label htmlFor={id} className="text-[11px] font-medium text-secondary">
                {label}
            </label>
            {children(id)}
            {error ? (
                <p className="text-xs text-danger">{error}</p>
            ) : hint ? (
                <p className="text-xs text-muted">{hint}</p>
            ) : null}
        </div>
    );
}

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
    return <input className={cn(control, "h-8", className)} {...props} />;
}

interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
    /** Для редакторов с заданной геометрией автоматический рост можно отключить. */
    autoGrow?: boolean;
}

/** Подстраивает высоту под текст и rows, сохраняя прокрутку у границы экрана. */
export function Textarea({ className, autoGrow = true, onInput, ...props }: TextareaProps) {
    const input = useRef<HTMLTextAreaElement>(null);
    const resize = useCallback(() => {
        const element = input.current;
        if (!autoGrow || !element || element.clientWidth === 0) return;
        const scrollTop = element.scrollTop;
        const overflow = element.style.overflowY;
        element.style.height = "auto";
        // Во время измерения полоса прокрутки не должна сужать строки.
        element.style.overflowY = "hidden";
        const computed = window.getComputedStyle(element);
        const border = parseFloat(computed.borderTopWidth) + parseFloat(computed.borderBottomWidth);
        const padding = parseFloat(computed.paddingTop) + parseFloat(computed.paddingBottom);
        const height = Math.max(element.offsetHeight, element.scrollHeight + border);
        element.style.height = `${Math.ceil(computed.boxSizing === "border-box" ? height : height - border - padding)}px`;
        element.style.overflowY = overflow;
        element.scrollTop = scrollTop;
    }, [autoGrow]);

    useLayoutEffect(() => {
        if (!autoGrow && input.current) {
            const height = props.style?.height;
            input.current.style.height = typeof height === "number" ? `${height}px` : height ?? "";
        }
        resize();
    }, [autoGrow, resize, props.value, props.defaultValue, props.rows, props.style, className]);

    useLayoutEffect(() => {
        const element = input.current;
        if (!autoGrow || !element) return;
        let width = element.clientWidth;
        let frame: number | undefined;
        const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => {
            if (width === element.clientWidth) return;
            width = element.clientWidth;
            // Высоту меняем после доставки уведомлений, чтобы не замкнуть observer.
            if (frame !== undefined) cancelAnimationFrame(frame);
            frame = requestAnimationFrame(() => { frame = undefined; resize(); });
        });
        observer?.observe(element);
        window.addEventListener("resize", resize);
        window.visualViewport?.addEventListener("resize", resize);
        return () => {
            if (frame !== undefined) cancelAnimationFrame(frame);
            observer?.disconnect();
            window.removeEventListener("resize", resize);
            window.visualViewport?.removeEventListener("resize", resize);
        };
    }, [autoGrow, resize]);

    return <textarea
        {...props}
        ref={input}
        className={cn(control, "py-2 leading-relaxed", autoGrow && "max-h-[min(32rem,calc(var(--app-height,100dvh)*0.6))] resize-none overflow-y-auto", className)}
        onInput={(event) => { onInput?.(event); resize(); }}
    />;
}

export function Select({ className, children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
    return (
        <select className={cn(control, "h-8 cursor-pointer pr-8", className)} {...props}>
            {children}
        </select>
    );
}
