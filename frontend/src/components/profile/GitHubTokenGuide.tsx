import { useState } from "react";
import { ChevronRight, ExternalLink } from "lucide-react";

const docs = "https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens";
const createTokenUrl = "https://github.com/settings/personal-access-tokens/new?" + new URLSearchParams({
    name: "TELOS SPACE",
    description: "Read repository sources and change evidence in TELOS SPACE",
    expires_in: "30",
    contents: "read",
    pull_requests: "read",
    statuses: "read",
    deployments: "read",
}).toString();

const permissions = [
    ["Contents", "Код, ветки и коммиты"],
    ["Pull requests", "Запросы на слияние"],
    ["Commit statuses", "Статусы проверок коммитов"],
    ["Deployments", "Развёртывания и их статусы"],
    ["Metadata", "Сведения о репозитории; добавляется автоматически"],
];
const linkClass = "text-accent underline decoration-accent/40 underline-offset-4 hover:decoration-accent focus-visible:outline-2 focus-visible:outline-accent";

export function GitHubTokenGuide({ initiallyOpen }: { initiallyOpen: boolean }) {
    const [open, setOpen] = useState(initiallyOpen);
    return <details open={open} onToggle={event => setOpen(event.currentTarget.open)} className="group/guide rounded-[var(--radius-control)] border border-line-subtle">
        <summary className="touch-target flex cursor-pointer list-none items-center justify-between gap-3 rounded-[var(--radius-control)] px-3 text-sm font-medium text-accent hover:bg-hover focus-visible:outline-2 focus-visible:outline-accent [&::-webkit-details-marker]:hidden">
            Как получить токен GitHub
            <ChevronRight size={16} aria-hidden="true" className="shrink-0 group-open/guide:rotate-90" />
        </summary>
        <div className="space-y-4 border-t border-line-subtle px-3 py-4 text-sm leading-relaxed text-secondary sm:px-4">
            <p>Создайте <strong className="text-primary">Fine-grained personal access token</strong> с чтением выбранных репозиториев.</p>
            <a href={createTokenUrl} target="_blank" rel="noopener noreferrer" className={`${linkClass} inline-flex min-h-11 items-center gap-2`}>
                Открыть форму GitHub <ExternalLink size={14} aria-hidden="true" />
            </a>
            <p className="text-xs text-muted">Ссылка подставит имя, срок 30 дней и права из таблицы. Войдите в GitHub и проверьте настройки перед созданием.</p>
            <ol className="list-decimal space-y-3 pl-5 marker:font-semibold marker:text-accent">
                <li>Путь вручную: аватар → <strong>Settings</strong> → <strong>Developer settings</strong> → <strong>Personal access tokens</strong> → <strong>Fine-grained tokens</strong> → <strong>Generate new token</strong>.</li>
                <li>В <strong>Token name</strong> укажите TELOS SPACE, в <strong>Expiration</strong> — срок действия.</li>
                <li>В <strong>Resource owner</strong> выберите владельца репозиториев: себя или организацию. В <strong>Repository access</strong> выберите <strong>Only select repositories</strong> и отметьте нужные в <strong>Selected repositories</strong>.</li>
                <li>В <strong>Permissions</strong> проверьте разрешения репозиториев ниже. У каждого — <strong>Read-only</strong>.</li>
                <li>Нажмите <strong>Generate token</strong> и сразу скопируйте токен.</li>
                <li>Вставьте его в поле «Токен GitHub» ниже и нажмите «Подключить GitHub» или «Заменить токен».</li>
            </ol>
            <div className="overflow-x-auto">
                <table className="w-full text-left text-xs sm:text-sm">
                    <caption className="sr-only">Разрешения репозиториев для TELOS SPACE: только чтение</caption>
                    <thead><tr className="border-b border-line text-primary"><th scope="col" className="py-2 pr-3 font-medium">Разрешение</th><th scope="col" className="py-2 font-medium">Для чего</th></tr></thead>
                    <tbody>{permissions.map(([name, purpose]) => <tr key={name} className="border-b border-line-subtle last:border-0"><th scope="row" className="py-2 pr-3 font-medium text-primary">{name}</th><td className="py-2">{purpose}</td></tr>)}</tbody>
                </table>
            </div>
            <p>Если токен организации получил статус <strong>pending</strong>, дождитесь одобрения администратора. Один fine-grained токен относится к одному владельцу репозиториев.</p>
            <details className="group/classic border-t border-line-subtle pt-2">
                <summary className="touch-target flex cursor-pointer list-none items-center gap-2 font-medium text-primary focus-visible:outline-2 focus-visible:outline-accent [&::-webkit-details-marker]:hidden">
                    <ChevronRight size={14} aria-hidden="true" className="shrink-0 group-open/classic:rotate-90" />
                    Нужны Checks или репозитории разных владельцев?
                </summary>
                <div className="space-y-3 pt-2">
                    <p>Fine-grained PAT не поддерживает Checks API для закрытых репозиториев. Поэтому часть результатов CI может быть недоступна. Разрешение Actions это ограничение не снимает.</p>
                    <p>Для Checks закрытых репозиториев, нескольких организаций или доступа внешнего участника используйте <strong>Tokens (classic)</strong>, если политика организации это разрешает.</p>
                    <ol className="list-decimal space-y-2 pl-5">
                        <li>В <strong>Personal access tokens → Tokens (classic)</strong> выберите <strong>Generate new token → Generate new token (classic)</strong>.</li>
                        <li>Задайте <strong>Note</strong> и <strong>Expiration</strong>, отметьте <strong>repo</strong>, затем нажмите <strong>Generate token</strong>.</li>
                        <li>Если организация использует SSO, в списке токенов откройте <strong>Configure SSO → Authorize</strong> для неё. Скопированный токен вставьте в поле ниже.</li>
                    </ol>
                    <p><strong className="text-primary">У classic-токена repo — широкий доступ, включая запись.</strong> TELOS SPACE использует его для чтения, но сам токен не ограничен чтением выбранных репозиториев.</p>
                    <a href="https://docs.github.com/en/rest/checks/runs#list-check-runs-for-a-git-reference" target="_blank" rel="noopener noreferrer" className={linkClass}>Ограничения Checks API в GitHub Docs</a>
                </div>
            </details>
            <a href={`${docs}#creating-a-fine-grained-personal-access-token`} target="_blank" rel="noopener noreferrer" className={linkClass}>Официальная инструкция GitHub</a>
        </div>
    </details>;
}
