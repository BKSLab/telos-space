import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Link, type LinkProps } from "react-router-dom";
import { cn } from "@/lib/cn";

const base =
    "touch-target inline-flex items-center justify-center gap-2 rounded-[var(--radius-control)] border border-transparent font-medium " +
    "transition-[background-color,border-color,color,box-shadow,transform] duration-[var(--duration-fast)] " +
    "ease-[var(--ease-standard)] active:translate-y-px motion-reduce:transform-none " +
    "disabled:pointer-events-none disabled:opacity-45";

const variants = {
    primary:
        "border-action/60 bg-action text-on-action shadow-card font-semibold hover:border-action-hover hover:bg-action-hover",
    secondary:
        "material-metal border-line text-primary shadow-card hover:border-line-strong hover:bg-hover active:bg-active",
    ai: "brand-ai-button",
    ghost: "text-secondary hover:bg-white/[0.045] hover:text-primary active:bg-active",
    destructive:
        "border-danger/30 bg-danger/[0.075] text-danger hover:border-danger/50 hover:bg-danger/15",
} as const;

const sizes = {
    sm: "h-7 px-2.5 text-[13px]",
    md: "h-8 px-3 text-[13px]",
    lg: "h-9 px-4 text-sm",
} as const;

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
    variant?: keyof typeof variants;
    size?: keyof typeof sizes;
    icon?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
    {
        variant = "secondary",
        size = "md",
        icon,
        className,
        type = "button",
        children,
        ...props
    },
    ref,
) {
    return (
        <button
            ref={ref}
            type={type}
            className={cn(base, variants[variant], sizes[size], className)}
            {...props}
        >
            {icon}
            {children}
        </button>
    );
});

interface LinkButtonProps extends LinkProps {
    variant?: keyof typeof variants;
    size?: keyof typeof sizes;
    icon?: ReactNode;
}

/** Ссылка с видом кнопки: настоящий `a`, а не кнопка с вложенной ссылкой. */
export function LinkButton({
    variant = "secondary",
    size = "md",
    icon,
    className,
    children,
    ...props
}: LinkButtonProps) {
    return (
        <Link className={cn(base, variants[variant], sizes[size], className)} {...props}>
            {icon}
            {children}
        </Link>
    );
}

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
    /** Обязателен: кнопка без текста непонятна без доступного имени (раздел 15). */
    label: string;
    variant?: keyof typeof variants;
    size?: "sm" | "md";
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
    {
        label,
        variant = "ghost",
        size = "md",
        className,
        type = "button",
        children,
        ...props
    },
    ref,
) {
    return (
        <button
            ref={ref}
            type={type}
            aria-label={label}
            title={label}
            className={cn(
                base,
                variants[variant],
                size === "sm" ? "h-6 w-6" : "h-8 w-8",
                "shrink-0 p-0",
                className,
            )}
            {...props}
        >
            {children}
        </button>
    );
});
