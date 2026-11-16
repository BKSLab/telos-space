import { useId } from "react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";

/** Существующая иконка Lucide с фирменным градиентом только по контуру. */
export function BrandIcon({ icon: Icon, size = 12, className }: { icon: LucideIcon; size?: number; className?: string }) {
    const gradientId = `brand-icon-${useId().replace(/:/g, "")}`;
    return <Icon size={size} className={cn("brand-accent-icon shrink-0 text-accent", className)} stroke={`url(#${gradientId})`} aria-hidden="true">
        <defs>
            <linearGradient id={gradientId} gradientUnits="userSpaceOnUse" x1="0" y1="8" x2="24" y2="16">
                <stop offset="0%" stopColor="var(--brand-cyan)" />
                <stop offset="52%" stopColor="var(--brand-blue)" />
                <stop offset="100%" stopColor="var(--brand-violet)" />
            </linearGradient>
        </defs>
    </Icon>;
}
