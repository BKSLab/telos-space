import { useState } from "react";
import { BRAND_ASSETS } from "@/lib/brand";
import { cn } from "@/lib/cn";

/** Decorative beside the assistant's visible name; stable initials if loading fails. */
export function ArisAvatar({ size = 32, className }: { size?: number; className?: string }) {
    const [failed, setFailed] = useState(false);
    return <span aria-hidden="true" style={{ width: size, height: size }} className={cn("brand-avatar inline-flex shrink-0 items-center justify-center overflow-hidden rounded-lg border border-transparent bg-surface-2 font-semibold text-ai-violet", className)}>
        {failed ? <span className="text-xs">AR</span> : <img
            src={size > 64 ? "/brand/aris-avatar-256.webp" : BRAND_ASSETS.avatar}
            srcSet={size > 64 ? "/brand/aris-avatar-256.webp 1x, /brand/aris-avatar-512.webp 2x" : `${BRAND_ASSETS.avatar} 1x, ${BRAND_ASSETS.avatarRetina} 2x`}
            alt=""
            width={size}
            height={size}
            className="size-full object-cover"
            decoding="async"
            onError={() => setFailed(true)}
        />}
    </span>;
}
