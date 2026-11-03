import { AgentAvatar } from "@/components/brand/AgentAvatar";

/** Совместимое имя компонента для аватара помощника. */
export function TimAvatar({ size = "md", className }: { size?: "sm" | "md"; className?: string }) {
    return <AgentAvatar size={size === "sm" ? 24 : 32} className={className} />;
}
