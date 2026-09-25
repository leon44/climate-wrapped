const cards = JSON.parse(document.getElementById("cards-data").textContent);

const CHART_DEFAULTS = {
  color: "rgba(255,255,255,0.9)",
  gridColor: "rgba(255,255,255,0.15)",
};

Chart.defaults.color = CHART_DEFAULTS.color;
Chart.defaults.borderColor = CHART_DEFAULTS.gridColor;
Chart.defaults.font.family = "-apple-system, 'Helvetica Neue', Arial, sans-serif";

cards.forEach((card) => {
  if (!card.chart) return;
  const canvas = document.getElementById(`chart-${card.id}`);
  if (!canvas) return;

  const values = card.chart.data.filter((v) => v !== null && v !== undefined);
  const dataMin = Math.min(...values);
  const dataMax = Math.max(...values);
  const pad = Math.max((dataMax - dataMin) * 0.25, 0.5);

  // Bar charts here are day/night counts -- always start the axis at zero
  // so bar heights stay comparable and never imply a negative count. Line
  // charts (unused today, kept for future stat types) are narrow-range
  // series where a zero baseline would flatten the trend to a barely-visible
  // sliver, so those still scale to the data's own range.
  const yMin = card.chart.type === "bar" ? 0 : Math.floor(dataMin - pad);

  new Chart(canvas, {
    type: card.chart.type,
    data: {
      labels: card.chart.labels,
      datasets: [
        {
          data: card.chart.data,
          backgroundColor: "rgba(255,255,255,0.85)",
          borderColor: "rgba(255,255,255,0.95)",
          borderWidth: card.chart.type === "line" ? 3 : 0,
          borderRadius: card.chart.type === "bar" ? 6 : 0,
          tension: 0.35,
          pointRadius: card.chart.point_radius ?? (card.chart.type === "line" ? 3 : 0),
          spanGaps: card.chart.span_gaps ?? false,
          fill: false,
        },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false } },
        y: {
          grid: { color: CHART_DEFAULTS.gridColor },
          min: yMin,
          max: Math.ceil(dataMax + pad),
        },
      },
    },
  });
});
