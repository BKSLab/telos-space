import { Check, Download, Smartphone } from "lucide-react";
import { useInstallApp } from "@/lib/useInstallApp";
import { BrandLogo } from "@/components/brand/BrandLogo";
import { Button } from "@/components/ui/Button";
import { Section } from "@/components/ui/Card";

export function InstallAppCard() {
    const { available, installed, pending, error, install } = useInstallApp();
    return (
        <Section title="На главный экран" className="border-t border-line-subtle pt-5">
            <div id="install" className="scroll-mt-4 flex min-w-0 flex-col gap-3 rounded-[var(--radius-card)] border border-line bg-surface p-4">
                <div className="flex items-center gap-3">
                    <BrandLogo variant="symbol" className="size-12 shrink-0" />
                    <div className="min-w-0">
                        <p className="font-semibold text-primary">TELOS SPACE</p>
                        <p className="text-[13px] text-muted">Проекты и Арис под рукой — с фирменной иконкой.</p>
                    </div>
                </div>
                {installed ? <p className="flex items-center gap-2 text-[13px] text-success"><Check size={16} />Приложение установлено</p> : <>
                    {(available || pending) && <Button className="self-start" icon={<Download size={16} />} variant="primary" disabled={pending} onClick={() => void install()}>
                        {pending ? "Открываем установку…" : "Установить приложение"}
                    </Button>}
                    <p className="flex items-start gap-2 text-[13px] leading-relaxed text-secondary">
                        <Smartphone size={17} className="mt-0.5 shrink-0" aria-hidden="true" />
                        <span>На Android откройте меню браузера ⋮ → «Установить приложение» или «Добавить на главный экран». На iPhone: «Поделиться» → «На экран Домой».</span>
                    </p>
                    {!window.isSecureContext && <p className="text-[12px] text-muted">Для установки приложения откройте TELOS SPACE по HTTPS-адресу.</p>}
                    {error && <p role="alert" className="text-[13px] text-danger">{error}</p>}
                </>}
            </div>
        </Section>
    );
}
