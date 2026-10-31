// Hover on a chart: vertical cursor line plus the value valid at the mouse position.
// Bars: the month under the mouse. Lines: the last price before the mouse (it holds until the next
// purchase), single events (discounted purchases) only when the mouse is right at them.
const SVG_NS = "http://www.w3.org/2000/svg";
const NEAR = 8; // SVG units

for (const svg of document.querySelectorAll("svg.chart[data-chart]")) {
  const wrap = svg.parentElement;
  const top = Number(svg.dataset.top), bottom = Number(svg.dataset.bottom);
  const left = Number(svg.dataset.left), right = Number(svg.dataset.right);
  const bars = [...svg.querySelectorAll("rect.bar")];
  const points = [...svg.querySelectorAll("circle.point")]
    .map(el => ({ el, x: Number(el.getAttribute("cx")), series: el.dataset.series, step: el.dataset.step === "1" }))
    .sort((a, b) => a.x - b.x);

  // The custom tooltip replaces the slow native one
  svg.querySelectorAll("title").forEach(t => t.remove());

  const cursor = document.createElementNS(SVG_NS, "line");
  cursor.setAttribute("class", "cursor");
  cursor.setAttribute("y1", top);
  cursor.setAttribute("y2", bottom);
  svg.appendChild(cursor);
  const tip = document.createElement("div");
  tip.className = "chart-tip";
  wrap.appendChild(tip);

  let active = [];
  const hide = () => {
    cursor.style.display = tip.style.display = "none";
    active.forEach(el => el.classList.remove("active"));
    active = [];
  };
  hide();

  const pick = x => {
    if (bars.length) {
      const bar = bars.find(b => x >= Number(b.dataset.x0) && x < Number(b.dataset.x1));
      return bar ? [bar] : [];
    }
    const found = [];
    for (const series of new Set(points.map(p => p.series))) {
      const own = points.filter(p => p.series === series);
      if (own[0].step) {
        // Last point at or before the mouse; the price line ends at the last purchase
        const before = own.filter(p => p.x <= x + 0.5);
        if (before.length && x <= own[own.length - 1].x + NEAR) found.push(before[before.length - 1].el);
      } else {
        const near = own.filter(p => Math.abs(p.x - x) <= NEAR);
        near.sort((a, b) => Math.abs(a.x - x) - Math.abs(b.x - x));
        if (near.length) found.push(near[0].el);
      }
    }
    return found;
  };

  svg.addEventListener("pointermove", event => {
    const matrix = svg.getScreenCTM();
    if (!matrix) return;
    const x = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse()).x;
    if (x < left || x > right) return hide();
    const hits = pick(x);
    if (!hits.length) return hide();

    active.forEach(el => el.classList.remove("active"));
    active = hits;
    hits.forEach(el => el.classList.add("active"));
    const lineX = bars.length ? (Number(hits[0].dataset.x0) + Number(hits[0].dataset.x1)) / 2 : x;
    cursor.setAttribute("x1", lineX);
    cursor.setAttribute("x2", lineX);
    cursor.style.display = "";

    tip.replaceChildren(...hits.map(el => {
      const row = document.createElement("div");
      row.textContent = el.dataset.tip;
      if (el.dataset.series) row.className = el.dataset.series;
      return row;
    }));
    tip.style.display = "block";
    // Next to the mouse, kept inside the chart
    const box = wrap.getBoundingClientRect();
    const px = event.clientX - box.left, py = event.clientY - box.top;
    const fitsRight = px + 14 + tip.offsetWidth <= wrap.clientWidth;
    tip.style.left = `${fitsRight ? px + 14 : Math.max(0, px - 14 - tip.offsetWidth)}px`;
    tip.style.top = `${Math.max(0, Math.min(py - tip.offsetHeight - 10, wrap.clientHeight - tip.offsetHeight))}px`;
  });
  svg.addEventListener("pointerleave", hide);
}
