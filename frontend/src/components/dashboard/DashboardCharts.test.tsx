// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { DistributionRing } from "./DashboardCharts";
import { TaskCompletion } from "./OverviewCards";

afterEach(cleanup);

it("размещает сегменты по их долям без перекрытия", () => {
    const { container } = render(<DistributionRing label="Проекты" segments={[
        { id: 1, label: "В работе", value: 1, color: "#0078d4" },
        { id: 2, label: "Готово", value: 3, color: "#3fb950" },
    ]} centerValue={4} centerLabel="проекта" />);
    const arcs = [...container.querySelectorAll("circle[stroke-dashoffset]")];
    expect(arcs).toHaveLength(2);
    const circumference = 2 * Math.PI * Number(arcs[0].getAttribute("r"));
    expect(Number(arcs[0].getAttribute("stroke-dashoffset"))).toBe(0);
    expect(Number(arcs[1].getAttribute("stroke-dashoffset")) / circumference).toBeCloseTo(-.25);
    const firstLength = Number(arcs[0].getAttribute("stroke-dasharray")!.split(" ")[0]);
    const secondLength = Number(arcs[1].getAttribute("stroke-dasharray")!.split(" ")[0]);
    expect(firstLength / circumference).toBeLessThan(.25);
    expect(secondLength / circumference).toBeLessThan(.75);
});

it("показывает взаимоисключающие количества открытых задач без добавления просрочки в распределение", () => {
    const { container } = render(<TaskCompletion total={10} done={4} inProgress={3} />);
    expect(screen.getByRole("img", { name: "Выполнено 4 из 10 задач: 40%" })).toBeTruthy();
    const rows = container.querySelectorAll("dl > div");
    expect(rows).toHaveLength(3);
    expect(within(rows[0] as HTMLElement).getByText("4")).toBeTruthy();
    expect(within(rows[1] as HTMLElement).getByText("3")).toBeTruthy();
    expect(within(rows[2] as HTMLElement).getByText("Остальные открытые")).toBeTruthy();
    expect(within(rows[2] as HTMLElement).getByText("3")).toBeTruthy();
    expect(screen.queryByText(/Просрочено/)).toBeNull();
});

it("сохраняет реальные названия стадий вместо вымышленных этапов", () => {
    render(<TaskCompletion total={2} done={1} inProgress={0} stages={[
        { stage_id: 1, stage_name: "Уточнение", tasks_count: 1, color: "#abcdef", is_done_stage: false },
        { stage_id: 2, stage_name: "Согласовано", tasks_count: 1, color: "#123456", is_done_stage: true },
    ]} />);
    expect(screen.getByText("Уточнение")).toBeTruthy();
    expect(screen.getByText("Согласовано")).toBeTruthy();
    expect(screen.queryByText("Разработка MVP")).toBeNull();
});

it("при пустых данных не рисует сегменты и не показывает ложный процент", () => {
    const { container } = render(<DistributionRing label="Распределение проектов" segments={[]} centerValue="0" centerLabel="проектов" />);
    expect(screen.getByText("Нет данных")).toBeTruthy();
    expect(container.querySelectorAll("circle[stroke-dasharray]")).toHaveLength(0);
    expect(screen.queryByText("100%")).toBeNull();
});
