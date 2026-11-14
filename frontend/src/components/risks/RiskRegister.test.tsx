// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ProjectMember } from "@/lib/types";
import { riskInput, type ProjectRisk } from "@/lib/risks";
import { RiskRegister } from "./RiskRegister";

afterEach(cleanup);
const member: ProjectMember = { id: 901, project_id: 1, role: "MEMBER", created_at: "2026-11-15T10:00:00Z", user: { id: 7, username: "oleg", first_name: "Олег", last_name: "Петров", middle_name: null, has_avatar: true } };
const risk: ProjectRisk = { ...riskInput({ title: "Задержка поставки", description: "Поставщик задерживает результат", owner_user_id: 7 }), id: 12, key: "RISK-12", project_id: 1, risk_level: "MEDIUM", created_at: member.created_at, updated_at: member.created_at };

it("показывает фото и имя владельца по user.id, сохраняя открытие риска", () => {
    const open = vi.fn();
    const { container } = render(<RiskRegister risks={[risk]} members={[member]} tasks={[]} onOpen={open} />);
    expect(screen.getByText("Петров Олег")).toBeTruthy();
    expect(container.querySelector('img[src*="/users/7/avatar"]')).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Открыть RISK-12: Задержка поставки" }));
    expect(open).toHaveBeenCalledExactlyOnceWith(12);
});

it("сохраняет отдельные fallback владельца вне команды и неназначенного риска", () => {
    const { container } = render(<RiskRegister risks={[risk, { ...risk, id: 13, owner_user_id: null }]} members={[]} tasks={[]} onOpen={vi.fn()} />);
    expect(screen.getByText("Участник проекта")).toBeTruthy();
    expect(screen.getByText("Не назначен")).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
});
