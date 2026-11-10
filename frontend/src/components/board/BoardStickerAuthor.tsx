import { apiUrl, endpoints } from "@/lib/api";
import {
    resolveStickerAuthor,
    stickerAuthorLabel,
    type BoardSticker,
} from "@/lib/board/stickers";
import type { ProjectMember } from "@/lib/types";
import { UserAvatar } from "@/components/users/UserAvatar";
import { Tooltip } from "@/components/ui/Tooltip";

interface BoardStickerAuthorProps {
    projectId?: number;
    sticker: BoardSticker;
    members: ProjectMember[];
}

export function BoardStickerAuthor({
    projectId,
    sticker,
    members,
}: BoardStickerAuthorProps) {
    const resolvedProjectId = projectId ?? sticker.project_id;
    const author = resolveStickerAuthor(sticker, members);
    const label = stickerAuthorLabel(author);
    const avatarUrl = resolvedProjectId != null && author.kind === "current" && author.user?.has_avatar
        ? apiUrl(endpoints.projectMemberAvatar(resolvedProjectId!, author.user.id))
        : undefined;

    return (
        <div className="project-sticker-author" aria-label={label}>
            <Tooltip content={label} placement="bottom">
                <span aria-hidden="true" className="shrink-0">
                    <UserAvatar
                        user={author.user ?? undefined}
                        size="xs"
                        avatarUrl={avatarUrl}
                        hasAvatar={author.user?.has_avatar ?? false}
                        fallbackInitials={author.initials}
                        className={author.kind !== "current" ? "opacity-75 grayscale" : undefined}
                    />
                </span>
            </Tooltip>
            <span className="min-w-0 truncate" title={label}>
                {author.displayName}
                {author.username && <span className="ml-1 opacity-70">@{author.username}</span>}
            </span>
        </div>
    );
}
