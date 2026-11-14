import { UserAvatar } from "@/components/users/UserAvatar";
import { cn } from "@/lib/cn";
import { fullName, type UserSummary } from "@/lib/types";

interface UserIdentityProps {
    user?: UserSummary | null;
    userId?: number | null;
    hasAvatar?: boolean;
    name?: string | null;
    size?: "xs" | "sm" | "md";
    className?: string;
    nameClassName?: string;
}

/** Одинаковое имя и фото во всех представлениях; snapshot без ID остаётся с инициалами. */
export function UserIdentity({ user, userId, hasAvatar, name, size = "xs", className, nameClassName }: UserIdentityProps) {
    const label = name?.trim() || (user && (fullName(user) || user.username)) || "Участник";
    const initials = label.split(/\s+/).slice(0, 2).map(part => Array.from(part)[0]).join("").toUpperCase();
    return <span title={label} className={cn("inline-flex max-w-full min-w-0 items-center gap-2 align-middle", className)}>
        <UserAvatar user={user ?? undefined} userId={userId} hasAvatar={hasAvatar} size={size} fallbackInitials={initials} />
        <span className={cn("min-w-0 truncate", nameClassName)}>{label}</span>
    </span>;
}
