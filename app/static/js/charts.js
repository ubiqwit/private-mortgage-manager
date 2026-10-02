/* Shared Chart.js styling: thin marks, hairline grid, muted axes, compact $ ticks.
   Colour slots follow a CVD-validated categorical order (blue, orange, aqua). */
window.PMM = window.PMM || {};
(function (PMM) {
  const INK = { primary: '#0b0b0b', secondary: '#52514e', muted: '#898781', grid: '#e1e0d9', axis: '#c3c2b7' };
  PMM.SERIES = ['#2a78d6', '#eb6834', '#1baf7a'];
  PMM.SURFACE = '#ffffff';

  const compact = new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', notation: 'compact', maximumFractionDigits: 1 });
  const full = new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD' });
  PMM.money = (v) => full.format(v);

  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';
  Chart.defaults.font.size = 12;
  Chart.defaults.color = INK.muted;
  Chart.defaults.plugins.legend.labels.color = INK.secondary;
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.legend.labels.pointStyle = 'rectRounded';
  Chart.defaults.plugins.legend.align = 'start';
  Chart.defaults.plugins.tooltip.callbacks.label = (ctx) =>
    `${ctx.dataset.label ? ctx.dataset.label + ': ' : ''}${full.format(ctx.parsed[ctx.chart.options.indexAxis === 'y' ? 'x' : 'y'])}`;

  function valueAxis(stacked) {
    return {
      stacked: !!stacked, beginAtZero: true,
      grid: { color: INK.grid, lineWidth: 1, drawTicks: false },
      border: { display: false },
      ticks: { color: INK.muted, padding: 6, callback: (v) => compact.format(v), maxTicksLimit: 6 },
    };
  }
  function categoryAxis(stacked) {
    return { stacked: !!stacked, grid: { display: false }, border: { color: INK.axis }, ticks: { color: INK.muted } };
  }

  /* datasets: [{label, data}] — drawn as columns (or bars when horizontal). */
  PMM.barChart = function (canvas, labels, datasets, opts) {
    opts = opts || {};
    const horizontal = !!opts.horizontal;
    const stacked = datasets.length > 1 && opts.stacked !== false;
    const ds = datasets.map((d, i) => {
      const isTop = i === datasets.length - 1 || !stacked;
      // Round only the data end; a 2px surface-coloured edge forms the gap between stacked segments.
      const radius = isTop ? (horizontal ? { topRight: 4, bottomRight: 4 } : { topLeft: 4, topRight: 4 }) : 0;
      return {
        label: d.label, data: d.data, backgroundColor: d.color || PMM.SERIES[i],
        borderColor: PMM.SURFACE, borderWidth: stacked ? (horizontal ? { right: 2 } : { top: 2 }) : 0,
        borderRadius: radius, borderSkipped: false, maxBarThickness: 24,
      };
    });
    return new Chart(canvas, {
      type: 'bar',
      data: { labels, datasets: ds },
      options: {
        indexAxis: horizontal ? 'y' : 'x',
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: stacked ? 'index' : 'nearest', intersect: false, axis: horizontal ? 'y' : 'x' },
        plugins: { legend: { display: datasets.length > 1 } },
        scales: horizontal
          ? { x: valueAxis(stacked), y: categoryAxis(stacked) }
          : { x: categoryAxis(stacked), y: valueAxis(stacked) },
      },
    });
  };
})(window.PMM);
