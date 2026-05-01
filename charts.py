import plotly.graph_objects as go
import plotly.express as px
import io
import calendar

MONTH_NAMES = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}

COLORS = px.colors.qualitative.Set2


def _fig_to_bytes(fig) -> bytes:
    fig.update_layout(
        paper_bgcolor="#1e1e2e",
        plot_bgcolor="#1e1e2e",
        font=dict(color="#cdd6f4", family="Arial", size=13),
        margin=dict(l=40, r=40, t=60, b=40),
    )
    return fig.to_image(format="png", width=900, height=500, scale=2)


def chart_monthly_by_category(data: list[tuple], month: int, year: int) -> bytes:
    """Bar chart: category vs total for a given month."""
    categories = [r[0] for r in data]
    totals = [r[1] for r in data]

    fig = go.Figure(go.Bar(
        x=categories,
        y=totals,
        marker_color=COLORS[:len(categories)],
        text=[f"{v:,.0f} ₽" for v in totals],
        textposition="outside",
        textfont=dict(size=12),
    ))
    fig.update_layout(
        title=dict(text=f"Расходы по категориям — {MONTH_NAMES[month]} {year}", font=dict(size=16)),
        xaxis_title="Категория",
        yaxis_title="Сумма",
        yaxis=dict(gridcolor="#313244"),
        showlegend=False,
    )
    return _fig_to_bytes(fig)


def chart_monthly_pie(data: list[tuple], month: int, year: int) -> bytes:
    """Pie chart: category shares for a month."""
    categories = [r[0] for r in data]
    totals = [r[1] for r in data]

    fig = go.Figure(go.Pie(
        labels=categories,
        values=totals,
        hole=0.4,
        marker=dict(colors=COLORS),
        textinfo="label+percent",
        textfont=dict(size=13),
    ))
    fig.update_layout(
        title=dict(text=f"Доли категорий — {MONTH_NAMES[month]} {year}", font=dict(size=16)),
    )
    return _fig_to_bytes(fig)


def chart_daily(data: list[tuple], month: int, year: int) -> bytes:
    """Stacked bar chart: day vs daily total, broken down by category."""
    # pivot: {category: {day: amount}}
    all_days = sorted({r[0] for r in data})
    categories = sorted({r[1] for r in data})
    amounts: dict[str, dict[int, float]] = {cat: {} for cat in categories}
    for day, cat, amount in data:
        amounts[cat][day] = amount

    fig = go.Figure()
    for i, cat in enumerate(categories):
        values = [amounts[cat].get(d, 0) for d in all_days]
        fig.add_trace(go.Bar(
            name=cat,
            x=all_days,
            y=values,
            marker_color=COLORS[i % len(COLORS)],
        ))

    fig.update_layout(
        barmode="stack",
        title=dict(text=f"Расходы по дням — {MONTH_NAMES[month]} {year}", font=dict(size=16)),
        xaxis=dict(title="День", tickmode="linear", dtick=1),
        yaxis=dict(title="Сумма", gridcolor="#313244"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return _fig_to_bytes(fig)


def chart_yearly_trend(data: list[tuple], year: int) -> bytes:
    """Line chart: monthly totals trend for a year."""
    months = [r[0] for r in data]
    totals = [r[1] for r in data]
    month_labels = [MONTH_NAMES[m] for m in months]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=month_labels,
        y=totals,
        mode="lines+markers+text",
        line=dict(color="#cba6f7", width=3),
        marker=dict(size=10, color="#cba6f7"),
        text=[f"{v:,.0f}" for v in totals],
        textposition="top center",
        textfont=dict(size=11),
        fill="tozeroy",
        fillcolor="rgba(203,166,247,0.15)",
    ))
    fig.update_layout(
        title=dict(text=f"Тренд расходов по месяцам — {year}", font=dict(size=16)),
        xaxis_title="Месяц",
        yaxis=dict(title="Сумма", gridcolor="#313244"),
        showlegend=False,
    )
    return _fig_to_bytes(fig)


def chart_alltime_by_category(data: list[tuple]) -> bytes:
    """Horizontal bar: all-time totals by category."""
    categories = [r[0] for r in data][::-1]
    totals = [r[1] for r in data][::-1]

    fig = go.Figure(go.Bar(
        x=totals,
        y=categories,
        orientation="h",
        marker_color=COLORS[:len(categories)],
        text=[f"{v:,.0f} ₽" for v in totals],
        textposition="outside",
        textfont=dict(size=12),
    ))
    fig.update_layout(
        title=dict(text="Все расходы по категориям (за всё время)", font=dict(size=16)),
        xaxis=dict(title="Сумма", gridcolor="#313244"),
        yaxis_title="Категория",
        showlegend=False,
        height=max(400, len(categories) * 55),
    )
    return _fig_to_bytes(fig)
