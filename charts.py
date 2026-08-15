"""Графики отчётов. Единый стиль: светлая тема, минимализм в духе Apple Health.

Правила:
- один акцентный цвет там, где категории подписаны на оси (bar-чарты);
- категориальная палитра (валидированная, фикс. порядок) там, где цвет несёт
  смысл: доли и стек по дням; больше 8 серий — сворачиваются в «Другое»;
- подписи значений — вторичным цветом текста, не цветом серии;
- лёгкая горизонтальная сетка, без осевых линий и рамок.
"""

import plotly.graph_objects as go

MONTH_NAMES = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}

# категориальная палитра (фиксированный порядок, валидирована для светлой темы)
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
           "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
ACCENT = PALETTE[0]            # единственный цвет для «одноцветных» графиков
SURFACE = "#fcfcfb"
INK = "#0b0b0b"                # основной текст
INK2 = "#52514e"               # вторичный текст (подписи значений, оси)
GRID = "#ebebe9"
FONT = "Helvetica Neue, Helvetica, Arial, sans-serif"
MAX_SERIES = 8                 # дальше — «Другое»


def _rub(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ")


def _base_layout(fig, title: str, subtitle: str = ""):
    text = f"<b>{title}</b>"
    if subtitle:
        text += f"<br><span style='font-size:13px;color:{INK2}'>{subtitle}</span>"
    fig.update_layout(
        title=dict(text=text, x=0.045, xanchor="left", y=0.94, yanchor="top",
                   font=dict(size=20, color=INK)),
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(color=INK, family=FONT, size=13),
        margin=dict(l=48, r=36, t=86, b=44),
        xaxis=dict(showgrid=False, zeroline=False, showline=False,
                   tickfont=dict(color=INK2, size=12), title=None),
        yaxis=dict(gridcolor=GRID, gridwidth=1, zeroline=False, showline=False,
                   tickfont=dict(color=INK2, size=12), title=None),
        showlegend=False,
    )


def _fig_to_bytes(fig, height: int = 500) -> bytes:
    return fig.to_image(format="png", width=900, height=height, scale=2)


def _fold_other(data: list[tuple], key_idx: int, val_idx: int) -> dict[str, float]:
    """Суммы по категориям; всё после MAX_SERIES-1 крупнейших -> «Другое»."""
    totals: dict[str, float] = {}
    for row in data:
        totals[row[key_idx]] = totals.get(row[key_idx], 0) + row[val_idx]
    ranked = sorted(totals, key=totals.get, reverse=True)
    keep = set(ranked[:MAX_SERIES - 1]) if len(ranked) > MAX_SERIES else set(ranked)
    return {cat: (cat if cat in keep else "Другое") for cat in totals}


def chart_monthly_by_category(data: list[tuple], month: int, year: int) -> bytes:
    """Bar chart: категория × сумма за месяц. Один цвет — имена уже на оси."""
    categories = [r[0] for r in data]
    totals = [r[1] for r in data]

    fig = go.Figure(go.Bar(
        x=categories,
        y=totals,
        marker=dict(color=ACCENT),
        text=[f"{_rub(v)}" for v in totals],
        textposition="outside",
        textfont=dict(size=12, color=INK2),
        cliponaxis=False,
    ))
    _base_layout(fig, "Расходы по категориям",
                 f"{MONTH_NAMES[month]} {year} · итого {_rub(sum(totals))} ₽")
    fig.update_layout(barcornerradius=6, bargap=0.42)
    fig.update_yaxes(tickformat="~s")
    return _fig_to_bytes(fig)


def chart_monthly_pie(data: list[tuple], month: int, year: int) -> bytes:
    """Donut: доли категорий за месяц, топ-7 + «Другое»."""
    mapping = _fold_other(data, 0, 1)
    totals: dict[str, float] = {}
    for cat, value in data:
        totals[mapping[cat]] = totals.get(mapping[cat], 0) + value
    items = sorted(totals.items(), key=lambda x: -x[1])
    if "Другое" in totals:  # «Другое» всегда последним
        items = [i for i in items if i[0] != "Другое"] + [("Другое", totals["Другое"])]
    labels = [i[0] for i in items]
    values = [i[1] for i in items]

    fig = go.Figure(go.Pie(
        labels=labels,
        values=values,
        hole=0.62,
        sort=False,
        direction="clockwise",
        marker=dict(colors=PALETTE[:len(labels)],
                    line=dict(color=SURFACE, width=2)),
        textinfo="label+percent",
        textposition="outside",
        textfont=dict(size=13, color=INK),
    ))
    fig.add_annotation(
        text=f"<b>{_rub(sum(values))} ₽</b><br>"
             f"<span style='font-size:13px;color:{INK2}'>итого</span>",
        showarrow=False, font=dict(size=22, color=INK, family=FONT),
    )
    _base_layout(fig, "Доли категорий", f"{MONTH_NAMES[month]} {year}")
    fig.update_layout(margin=dict(l=80, r=80, t=90, b=60))
    return _fig_to_bytes(fig, height=560)


def chart_daily(data: list[tuple], month: int, year: int) -> bytes:
    """Stacked bar: день × сумма, разбивка по категориям (топ-7 + «Другое»)."""
    mapping = _fold_other(data, 1, 2)
    all_days = sorted({r[0] for r in data})
    amounts: dict[str, dict[int, float]] = {}
    order_totals: dict[str, float] = {}
    for day, cat, amount in data:
        folded = mapping[cat]
        amounts.setdefault(folded, {})
        amounts[folded][day] = amounts[folded].get(day, 0) + amount
        order_totals[folded] = order_totals.get(folded, 0) + amount

    series = sorted(order_totals, key=order_totals.get, reverse=True)
    if "Другое" in series:
        series = [s for s in series if s != "Другое"] + ["Другое"]

    day_totals = [sum(amounts[s].get(d, 0) for s in series) for d in all_days]

    fig = go.Figure()
    for i, cat in enumerate(series):
        fig.add_trace(go.Bar(
            name=cat,
            x=all_days,
            y=[amounts[cat].get(d, 0) for d in all_days],
            marker=dict(color=PALETTE[i % len(PALETTE)],
                        line=dict(color=SURFACE, width=1)),
        ))
    fig.add_trace(go.Scatter(
        x=all_days,
        y=day_totals,
        mode="text",
        text=[_rub(v) if v else "" for v in day_totals],
        textposition="top center",
        textfont=dict(size=10, color=INK2),
        showlegend=False,
        cliponaxis=False,
    ))
    _base_layout(fig, "Расходы по дням",
                 f"{MONTH_NAMES[month]} {year} · итого {_rub(sum(day_totals))} ₽")
    fig.update_layout(
        barmode="stack",
        barcornerradius=3,
        bargap=0.35,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1,
                    font=dict(size=12, color=INK2), bgcolor="rgba(0,0,0,0)"),
        xaxis=dict(tickmode="linear", dtick=1, tickfont=dict(size=10, color=INK2)),
    )
    fig.update_yaxes(tickformat="~s")
    return _fig_to_bytes(fig, height=520)


def chart_yearly_trend(data: list[tuple], year: int) -> bytes:
    """Line: суммы по месяцам за год."""
    months = [r[0] for r in data]
    totals = [r[1] for r in data]
    month_labels = [MONTH_NAMES[m][:3] for m in months]

    fig = go.Figure(go.Scatter(
        x=month_labels,
        y=totals,
        mode="lines+markers+text",
        line=dict(color=ACCENT, width=2.5, shape="spline", smoothing=0.6),
        marker=dict(size=8, color=ACCENT, line=dict(color=SURFACE, width=2)),
        text=[_rub(v) for v in totals],
        textposition="top center",
        textfont=dict(size=11, color=INK2),
        fill="tozeroy",
        fillcolor="rgba(42,120,214,0.07)",
        cliponaxis=False,
    ))
    _base_layout(fig, "Тренд по месяцам", f"{year} год")
    fig.update_yaxes(tickformat="~s", rangemode="tozero")
    return _fig_to_bytes(fig)


def chart_alltime_by_category(data: list[tuple]) -> bytes:
    """Horizontal bar: суммы по категориям за всё время."""
    categories = [r[0] for r in data][::-1]
    totals = [r[1] for r in data][::-1]

    fig = go.Figure(go.Bar(
        x=totals,
        y=categories,
        orientation="h",
        marker=dict(color=ACCENT),
        text=[f"{_rub(v)} ₽" for v in totals],
        textposition="outside",
        textfont=dict(size=12, color=INK2),
        cliponaxis=False,
    ))
    _base_layout(fig, "За всё время", f"итого {_rub(sum(totals))} ₽")
    fig.update_layout(barcornerradius=5, bargap=0.38,
                      margin=dict(l=140, r=90, t=86, b=44))
    fig.update_xaxes(tickformat="~s", gridcolor=GRID, showgrid=True)
    fig.update_yaxes(showgrid=False, tickfont=dict(color=INK, size=13))
    return _fig_to_bytes(fig, height=max(420, len(categories) * 48 + 120))
