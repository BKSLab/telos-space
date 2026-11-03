import type {
    InputHTMLAttributes,
    ReactNode,
    SelectHTMLAttributes,
    TextareaHTMLAttributes,
} from "react";
import { useId } from "react";
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

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
    return <textarea className={cn(control, "py-2 leading-relaxed", className)} {...props} />;
}

export function Select({ className, children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
    return (
        <select className={cn(control, "h-8 cursor-pointer pr-8", className)} {...props}>
            {children}
        </select>
    );
}
