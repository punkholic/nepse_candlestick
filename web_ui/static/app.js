const $ = (id) => document.getElementById(id);

function setStatus(msg, kind = "info") {
  const el = $("status");
  el.textContent = msg;
  el.style.color =
    kind === "good" ? "var(--good)" : kind === "bad" ? "var(--bad)" : "var(--muted)";
}

function pretty(obj, maxLen = 5000) {
  try {
    const s = JSON.stringify(obj, null, 2);
    return s.length > maxLen ? s.slice(0, maxLen) + "\n…(truncated)…" : s;
  } catch {
    return String(obj);
  }
}

async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return await r.json();
}

let chart, candleSeries, volChart, volSeries;
let currentSecurityId = null;
let currentPage = 0;
let hasMore = true;
let isLoadingMore = false;
let candlesStore = new Map(); // time -> candle
let volumeStore = new Map(); // time -> volume

function initCharts() {
  if (typeof window.LightweightCharts === "undefined") {
    throw new Error(
      "Chart library failed to load (LightweightCharts is undefined). Check /static/vendor/lightweight-charts.standalone.production.js"
    );
  }
  const chartEl = $("chart");
  const volEl = $("chart2");

  const addCandles = (chartApi, options) => {
    // v4 API
    if (typeof chartApi.addCandlestickSeries === "function") {
      return chartApi.addCandlestickSeries(options);
    }
    // v5+ API
    if (typeof chartApi.addSeries === "function" && window.LightweightCharts.CandlestickSeries) {
      return chartApi.addSeries(window.LightweightCharts.CandlestickSeries, options);
    }
    throw new Error("Unsupported LightweightCharts version: cannot create candlestick series");
  };

  const addHistogram = (chartApi, options) => {
    // v4 API
    if (typeof chartApi.addHistogramSeries === "function") {
      return chartApi.addHistogramSeries(options);
    }
    // v5+ API
    if (typeof chartApi.addSeries === "function" && window.LightweightCharts.HistogramSeries) {
      return chartApi.addSeries(window.LightweightCharts.HistogramSeries, options);
    }
    throw new Error("Unsupported LightweightCharts version: cannot create histogram series");
  };

  const common = {
    layout: {
      background: { color: "transparent" },
      textColor: "#cbd5e1",
      fontFamily:
        'ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, "Noto Sans", "Liberation Sans", sans-serif',
    },
    grid: {
      vertLines: { color: "rgba(255,255,255,0.06)" },
      horzLines: { color: "rgba(255,255,255,0.06)" },
    },
    timeScale: {
      borderColor: "rgba(255,255,255,0.08)",
    },
    rightPriceScale: {
      borderColor: "rgba(255,255,255,0.08)",
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Magnet,
    },
  };

  chart = LightweightCharts.createChart(chartEl, {
    ...common,
    width: chartEl.clientWidth,
    height: chartEl.clientHeight,
  });
  candleSeries = addCandles(chart, {
    upColor: "#34d399",
    downColor: "#fb7185",
    borderUpColor: "#34d399",
    borderDownColor: "#fb7185",
    wickUpColor: "#34d399",
    wickDownColor: "#fb7185",
  });

  volChart = LightweightCharts.createChart(volEl, {
    ...common,
    width: volEl.clientWidth,
    height: volEl.clientHeight,
  });
  volSeries = addHistogram(volChart, {
    color: "rgba(77,163,255,0.55)",
    priceFormat: { type: "volume" },
    priceScaleId: "",
  });
  volChart.priceScale("").applyOptions({
    scaleMargins: { top: 0.1, bottom: 0.0 },
  });

  // Keep time scales aligned.
  const sync = () => {
    const range = chart.timeScale().getVisibleRange();
    if (range) volChart.timeScale().setVisibleRange(range);
  };
  chart.timeScale().subscribeVisibleTimeRangeChange(sync);

  // Infinite scroll: when the visible range gets close to the left edge, fetch older page(s).
  if (typeof chart.timeScale().subscribeVisibleLogicalRangeChange === "function") {
    chart.timeScale().subscribeVisibleLogicalRangeChange(async (range) => {
      if (!range) return;
      // If user is near the beginning (left side), load more history.
      if (range.from < 30) {
        await maybeLoadMore();
      }
    });
  }

  const onResize = () => {
    chart.applyOptions({ width: chartEl.clientWidth, height: chartEl.clientHeight });
    volChart.applyOptions({ width: volEl.clientWidth, height: volEl.clientHeight });
  };
  window.addEventListener("resize", onResize);
}

async function loadCompanies() {
  try {
    setStatus("Loading companies…");
    const verify = $("verifySslInput").checked ? "true" : "false";
    const data = await fetchJSON(`/api/companies?verify_ssl=${verify}`);
    const list = data.data || [];
    const dl = $("symbols");
    dl.innerHTML = "";
    for (const c of list) {
      const opt = document.createElement("option");
      opt.value = c.symbol;
      opt.label = `${c.symbol} — ${c.name || ""} (id=${c.id})`;
      opt.dataset.id = c.id;
      dl.appendChild(opt);
    }
    setStatus(`Loaded companies: ${list.length}`, "good");
  } catch (e) {
    setStatus(`Failed to load companies: ${e}`, "bad");
  }
}

function findSecurityIdForSymbol(symbol) {
  const dl = $("symbols");
  const opts = dl.querySelectorAll("option");
  for (const o of opts) {
    if ((o.value || "").toUpperCase() === symbol.toUpperCase()) {
      return o.label && o.label.includes("id=") ? Number(o.label.split("id=")[1].split(")")[0]) : null;
    }
  }
  return null;
}

async function loadCandles() {
  const verify = $("verifySslInput").checked ? "true" : "false";
  let sid = ($("securityIdInput").value || "").trim();
  const sym = ($("symbolInput").value || "").trim();

  if (!sid && sym) {
    const guessed = findSecurityIdForSymbol(sym);
    if (guessed) sid = String(guessed);
    $("securityIdInput").value = sid;
  }

  if (!sid) {
    setStatus("Please enter a Security ID (or pick a symbol).", "bad");
    return;
  }

  try {
    // Reset state for a fresh symbol load.
    currentSecurityId = sid;
    currentPage = 0;
    hasMore = true;
    candlesStore = new Map();
    volumeStore = new Map();

    setStatus(`Loading candles for security_id=${sid}…`);
    const data = await fetchJSON(
      `/api/candles?security_id=${encodeURIComponent(sid)}&page=0&size=200&verify_ssl=${verify}`
    );
    if (!data.ok) throw new Error(data.error || "Unknown error");

    const candles = data.candles || [];
    const volume = data.volume || [];
    for (const c of candles) candlesStore.set(c.time, c);
    for (const v of volume) volumeStore.set(v.time, v);

    const allCandles = Array.from(candlesStore.values()).sort((a, b) => a.time - b.time);
    const allVol = Array.from(volumeStore.values()).sort((a, b) => a.time - b.time);
    candleSeries.setData(allCandles);
    volSeries.setData(allVol);

    const meta = data.meta || {};
    const totalPages = typeof meta.total_pages === "number" ? meta.total_pages : null;
    hasMore = totalPages !== null ? currentPage + 1 < totalPages : meta.last === false || meta.last === "false";
    $("chartTitle").textContent = sym ? `Candles — ${sym} (id=${sid})` : `Candles — id=${sid}`;
    if (allCandles.length) {
      const a = new Date(allCandles[0].time * 1000).toISOString().slice(0, 10);
      const b = new Date(allCandles[allCandles.length - 1].time * 1000).toISOString().slice(0, 10);
      $("chartSub").textContent = `${a} → ${b}`;
    } else {
      $("chartSub").textContent = "—";
    }
    $("chartMeta").textContent = meta.points ? `${meta.points} points (page 0)` : "";

    chart.timeScale().fitContent();
    setStatus("Loaded candles.", "good");
  } catch (e) {
    setStatus(`Failed to load candles: ${e}`, "bad");
  }
}

async function maybeLoadMore() {
  if (!currentSecurityId) return;
  if (!hasMore) return;
  if (isLoadingMore) return;

  isLoadingMore = true;
  const verify = $("verifySslInput").checked ? "true" : "false";
  const nextPage = currentPage + 1;
  try {
    setStatus(`Loading older candles… (page ${nextPage})`);
    const data = await fetchJSON(
      `/api/candles?security_id=${encodeURIComponent(currentSecurityId)}&page=${nextPage}&size=200&verify_ssl=${verify}`
    );
    if (!data.ok) throw new Error(data.error || "Unknown error");
    const candles = data.candles || [];
    const volume = data.volume || [];
    for (const c of candles) candlesStore.set(c.time, c);
    for (const v of volume) volumeStore.set(v.time, v);

    const allCandles = Array.from(candlesStore.values()).sort((a, b) => a.time - b.time);
    const allVol = Array.from(volumeStore.values()).sort((a, b) => a.time - b.time);
    candleSeries.setData(allCandles);
    volSeries.setData(allVol);

    const meta = data.meta || {};
    const totalPages = typeof meta.total_pages === "number" ? meta.total_pages : null;
    currentPage = nextPage;
    hasMore = totalPages !== null ? currentPage + 1 < totalPages : meta.last === false || meta.last === "false";
    $("chartMeta").textContent = `${allCandles.length} points (pages 0..${currentPage})`;
    if (allCandles.length) {
      const a = new Date(allCandles[0].time * 1000).toISOString().slice(0, 10);
      const b = new Date(allCandles[allCandles.length - 1].time * 1000).toISOString().slice(0, 10);
      $("chartSub").textContent = `${a} → ${b}`;
    }
    setStatus(`Loaded older candles (page ${nextPage}).`, "good");
  } catch (e) {
    setStatus(`Failed to load older candles: ${e}`, "bad");
    // If paging fails, stop trying automatically.
    hasMore = false;
  } finally {
    isLoadingMore = false;
  }
}

async function loadSnapshot() {
  const verify = $("verifySslInput").checked ? "true" : "false";
  try {
    $("snapshotBox").textContent = "Loading snapshot…";
    const data = await fetchJSON(`/api/snapshot?verify_ssl=${verify}`);
    // Only show per-endpoint ok/errors here; full JSON is downloadable.
    const rows = [];
    const results = data.results || {};
    for (const [k, v] of Object.entries(results)) {
      if (v && v.ok) rows.push({ endpoint: k, ok: true, seconds: v.seconds, summary: v.summary });
      else rows.push({ endpoint: k, ok: false, error: v && v.error ? v.error : "unknown" });
    }
    rows.sort((a, b) => a.endpoint.localeCompare(b.endpoint));
    $("snapshotBox").textContent = pretty({ meta: data.meta, endpoints: rows }, 9000);
    setStatus("Snapshot fetched (see box + download link).", "good");
  } catch (e) {
    $("snapshotBox").textContent = `Failed: ${e}`;
    setStatus(`Snapshot failed: ${e}`, "bad");
  }
}

function wire() {
  $("btnLoadCandles").addEventListener("click", loadCandles);
  $("btnSnapshot").addEventListener("click", loadSnapshot);

  $("symbolInput").addEventListener("change", () => {
    const sym = ($("symbolInput").value || "").trim();
    const guessed = sym ? findSecurityIdForSymbol(sym) : null;
    if (guessed) $("securityIdInput").value = String(guessed);
  });

  $("verifySslInput").addEventListener("change", () => {
    // refresh companies when SSL toggle changes
    loadCompanies();
  });
}

try {
  initCharts();
  wire();
  loadCompanies();

  // Default view so the chart isn't empty on first load.
  $("securityIdInput").value = "8122";
  setTimeout(() => loadCandles(), 400);
} catch (e) {
  setStatus(`Frontend error: ${e && e.message ? e.message : e}`, "bad");
}

