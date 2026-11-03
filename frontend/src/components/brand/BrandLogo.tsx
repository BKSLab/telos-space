import { BRAND_ASSETS, PRODUCT_NAME, PRODUCT_TAGLINE } from "@/lib/brand";
import { cn } from "@/lib/cn";

interface Props { variant?: "logo" | "symbol" | "tagline"; className?: string }

export function BrandLogo({ variant = "logo", className }: Props) {
    const symbol = variant === "symbol";
    const tagline = variant === "tagline";
    return <img
        src={symbol ? BRAND_ASSETS.symbol : tagline ? BRAND_ASSETS.logoTagline : BRAND_ASSETS.logo}
        alt={tagline ? `${PRODUCT_NAME} — ${PRODUCT_TAGLINE}` : PRODUCT_NAME}
        width={symbol ? 128 : 900}
        height={symbol ? 128 : tagline ? 190 : 160}
        className={cn("block shrink-0 object-contain", symbol ? "size-8" : "h-auto w-44 max-w-full", className)}
        draggable={false}
    />;
}
