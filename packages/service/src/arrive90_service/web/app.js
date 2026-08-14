const elements = Object.fromEntries([...document.querySelectorAll("[id]")].map((node) => [node.id, node]));
const SVG = "http://www.w3.org/2000/svg";
const state = { inventory: null, metadata: null, prediction: null, replayId: null };

async function api(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const detail = typeof payload.detail === "string" ? payload.detail : payload.detail?.message;
    throw new Error(detail || `Local explorer request failed with status ${response.status}.`);
  }
  return response.json();
}

function clear(node) {
  node.replaceChildren();
}

function option(value, label) {
  const node = document.createElement("option");
  node.value = value;
  node.textContent = label;
  return node;
}

function addDefinition(list, term, description) {
  const wrapper = document.createElement("div");
  const dt = document.createElement("dt");
  const dd = document.createElement("dd");
  dt.textContent = term;
  dd.textContent = description;
  wrapper.append(dt, dd);
  list.append(wrapper);
}

function seconds(value) {
  if (value === null || value === undefined) return "Unavailable";
  const rounded = Math.round(Number(value));
  const minutes = Math.floor(rounded / 60);
  const remainder = rounded % 60;
  return `${minutes}m ${String(remainder).padStart(2, "0")}s`;
}

function percent(value) {
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function fillSelect(select, values, makeLabel, includeAll = true) {
  const previous = select.value;
  clear(select);
  if (includeAll) select.append(option("", "All"));
  for (const value of values) select.append(option(String(value), makeLabel(value)));
  if ([...select.options].some((item) => item.value === previous)) select.value = previous;
}

function matchingReplays() {
  return state.inventory.replays.filter((row) =>
    (!elements.direction.value || row.direction_id === elements.direction.value) &&
    (!elements.origin.value || row.origin.stop_id === elements.origin.value) &&
    (!elements.destination.value || row.destination.stop_id === elements.destination.value));
}

function refreshReplayOptions() {
  const rows = matchingReplays();
  const previous = elements.replay.value;
  clear(elements.replay);
  for (const row of rows) {
    elements.replay.append(option(
      row.replay_id,
      `${row.service_date} · ${row.origin.name} to ${row.destination.name} · ${row.replay_id.slice(0, 8)}`,
    ));
  }
  if ([...elements.replay.options].some((item) => item.value === previous)) elements.replay.value = previous;
  elements["control-error"].hidden = rows.length > 0;
  elements["control-error"].textContent = rows.length ? "" : "No held-out replays match those controls.";
  elements["replay-form"].querySelector("button").disabled = rows.length === 0;
}

function svgNode(name, attributes = {}) {
  const node = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  return node;
}

function renderCdf(rows) {
  const chart = elements["cdf-chart"];
  clear(chart);
  chart.append(
    svgNode("title", { id: "cdf-chart-title" }),
    svgNode("desc", { id: "cdf-chart-desc" }),
  );
  chart.children[0].textContent = "Predicted cumulative arrival probability by time horizon";
  chart.children[1].textContent = "A line chart with a point for every fixed evaluation horizon.";
  chart.append(svgNode("line", { x1: 56, x2: 612, y1: 220, y2: 220, class: "axis" }));
  chart.append(svgNode("line", { x1: 56, x2: 56, y1: 20, y2: 220, class: "axis" }));
  const points = rows.map((row, index) => {
    const x = 56 + index * (556 / (rows.length - 1));
    const y = 220 - row.probability * 190;
    return { ...row, x, y };
  });
  chart.append(svgNode("polyline", { points: points.map((row) => `${row.x},${row.y}`).join(" "), class: "cdf-line" }));
  for (const row of points) {
    const point = svgNode("circle", { cx: row.x, cy: row.y, r: 5, class: "cdf-point" });
    const title = svgNode("title");
    title.textContent = `${seconds(row.seconds)}: ${percent(row.probability)}`;
    point.append(title);
    chart.append(point);
  }
  clear(elements["cdf-rows"]);
  for (const row of rows) {
    const tr = document.createElement("tr");
    const horizon = document.createElement("td");
    const probability = document.createElement("td");
    horizon.textContent = seconds(row.seconds);
    probability.textContent = percent(row.probability);
    tr.append(horizon, probability);
    elements["cdf-rows"].append(tr);
  }
}

function renderQuantiles(rows) {
  clear(elements["quantile-rows"]);
  clear(elements["interval-chart"]);
  const resolved = rows.filter((row) => row.resolved_within_60_minutes);
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const value of [row.level, seconds(row.seconds), row.resolved_within_60_minutes ? "Resolved" : "Unresolved beyond 60m"]) {
      const td = document.createElement("td");
      td.textContent = value;
      tr.append(td);
    }
    elements["quantile-rows"].append(tr);
  }
  if (!resolved.length) {
    elements["interval-text"].textContent = "All promoted-model quantiles are unresolved beyond the frozen 60-minute model horizon.";
    return;
  }
  const maximum = 3600;
  for (const row of rows) {
    const marker = document.createElement("div");
    marker.className = row.seconds === null ? "quantile-marker unresolved" : "quantile-marker";
    marker.style.left = `${Math.min(100, Number(row.seconds ?? maximum) / maximum * 100)}%`;
    marker.textContent = row.level;
    marker.title = row.seconds === null ? `${row.level} unresolved beyond 60 minutes` : `${row.level} ${seconds(row.seconds)}`;
    elements["interval-chart"].append(marker);
  }
  elements["interval-text"].textContent = rows.map((row) =>
    `${row.level}: ${row.seconds === null ? "unresolved beyond 60 minutes" : seconds(row.seconds)}`).join("; ");
}

function renderCalibration(report) {
  const chart = elements["calibration-chart"];
  clear(chart);
  const title = svgNode("title", { id: "calibration-chart-title" });
  const desc = svgNode("desc", { id: "calibration-chart-desc" });
  title.textContent = "Predicted probability compared with identified observed success rate";
  desc.textContent = "A diagonal reference and one labeled point for each supported calibration bin.";
  chart.append(title, desc);
  chart.append(svgNode("line", { x1: 50, x2: 610, y1: 250, y2: 20, class: "calibration-reference" }));
  clear(elements["calibration-rows"]);
  for (const bin of report.bins) {
    const x = 50 + bin.mean_predicted_probability * 560;
    const y = 250 - bin.observed_success_rate * 230;
    const circle = svgNode("circle", { cx: x, cy: y, r: 6, class: "calibration-point" });
    const label = svgNode("title");
    label.textContent = `Bin ${bin.bin_index + 1}: predicted ${percent(bin.mean_predicted_probability)}, observed ${percent(bin.observed_success_rate)}`;
    circle.append(label);
    chart.append(circle);
    const tr = document.createElement("tr");
    for (const value of [
      `Supported bin ${bin.bin_index + 1}`,
      percent(bin.mean_predicted_probability),
      percent(bin.observed_success_rate),
      `${percent(bin.population_success_rate_lower)} to ${percent(Math.min(1, bin.population_success_rate_upper))}`,
    ]) {
      const td = document.createElement("td");
      td.textContent = value;
      tr.append(td);
    }
    elements["calibration-rows"].append(tr);
  }
  elements["calibration-summary"].textContent = `ECE ${percent(report.expected_calibration_error)} · maximum error ${percent(report.maximum_calibration_error)}`;
}

function renderHistory(history) {
  clear(elements["cutoff-history"]);
  for (const [key, value] of Object.entries(history)) {
    addDefinition(elements["cutoff-history"], key.replaceAll("_", " "), value === null ? "Missing, with an explicit missingness feature" : String(value));
  }
}

function renderLineage(lineage, prediction) {
  clear(elements.lineage);
  addDefinition(elements.lineage, "Model bundle", prediction.model.bundle_id);
  addDefinition(elements.lineage, "Distribution", `${prediction.model.distribution}, scale ${prediction.model.scale}`);
  for (const [key, value] of Object.entries(lineage)) addDefinition(elements.lineage, key.replaceAll("_", " "), String(value));
}

async function scoreReplay(event) {
  event.preventDefault();
  const replayId = elements.replay.value;
  const horizon = elements.horizon.value;
  if (!replayId) return;
  elements["control-error"].hidden = true;
  try {
    const [prediction, calibration] = await Promise.all([
      api(`/v1/explorer/replays/${encodeURIComponent(replayId)}/prediction?horizon_seconds=${horizon}`),
      api(`/v1/explorer/reliability?horizon_seconds=${horizon}`),
    ]);
    state.prediction = prediction;
    state.replayId = replayId;
    elements.results.hidden = false;
    elements["replay-summary"].textContent = `${prediction.split} · evidence ${prediction.evidence_version} · ${prediction.replay.service_date} · ${prediction.replay.origin.name} to ${prediction.replay.destination.name} · direction ${prediction.replay.direction_id}`;
    elements["schedule-value"].textContent = seconds(prediction.baselines.official_schedule.seconds);
    elements["empirical-value"].textContent = seconds(prediction.baselines.empirical_midpoint.seconds);
    elements["empirical-detail"].textContent = prediction.baselines.empirical_midpoint.backoff_level ? `Training support: ${prediction.baselines.empirical_midpoint.backoff_level.replaceAll("_", " ").toLowerCase()}.` : "No training cell met frozen support; this diagnostic abstains.";
    elements["model-value"].textContent = percent(prediction.selected_horizon.probability);
    elements["model-detail"].textContent = `Probability of arrival within ${seconds(prediction.selected_horizon.seconds)}. Distribution: ${prediction.model.distribution}.`;
    renderCdf(prediction.fixed_horizon_probabilities);
    renderQuantiles(prediction.quantiles);
    renderCalibration(calibration);
    renderHistory(prediction.cutoff_visible_history);
    renderLineage(prediction.lineage, prediction);
    elements["outcome-status"].hidden = false;
    elements["reveal-button"].hidden = false;
    elements["outcome-result"].hidden = true;
    clear(elements["outcome-result"]);
    elements.results.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    elements["control-error"].textContent = error.message;
    elements["control-error"].hidden = false;
  }
}

async function revealOutcome() {
  if (!state.replayId) return;
  try {
    const payload = await api(`/v1/explorer/replays/${encodeURIComponent(state.replayId)}/outcome`);
    const outcome = payload.outcome;
    const heading = document.createElement("strong");
    heading.textContent = outcome.outcome_state.replaceAll("_", " ");
    const description = document.createElement("p");
    if (outcome.lower_bound_seconds === null && outcome.upper_bound_seconds === null) {
      description.textContent = "No finite travel-time bound is available for this later outcome.";
    } else {
      description.textContent = `Later outcome interval: ${seconds(outcome.lower_bound_seconds)} to ${seconds(outcome.upper_bound_seconds)}.`;
    }
    elements["outcome-result"].append(heading, description);
    elements["outcome-result"].hidden = false;
    elements["outcome-status"].hidden = true;
    elements["reveal-button"].hidden = true;
  } catch (error) {
    elements["outcome-result"].textContent = error.message;
    elements["outcome-result"].hidden = false;
  }
}

async function initialize() {
  const [status, metadata, inventory] = await Promise.all([
    api("/v1/system/status"),
    api("/v1/explorer/metadata"),
    api("/v1/explorer/inventory"),
  ]);
  state.metadata = metadata;
  state.inventory = inventory;
  elements["system-status"].textContent = `${status.status.toLowerCase()} · verified local artifacts`;
  elements["replay-count"].textContent = metadata.replay_count.toLocaleString();
  elements["test-count"].textContent = metadata.final_test.row_count.toLocaleString();
  fillSelect(elements.direction, inventory.directions, (value) => `Direction ${value}`);
  fillSelect(elements.origin, inventory.origins, (value) => `${value.name} · ${value.stop_id}`);
  fillSelect(elements.destination, inventory.destinations, (value) => `${value.name} · ${value.stop_id}`);
  elements.origin.querySelectorAll("option").forEach((node, index) => { if (index > 0) node.value = inventory.origins[index - 1].stop_id; });
  elements.destination.querySelectorAll("option").forEach((node, index) => { if (index > 0) node.value = inventory.destinations[index - 1].stop_id; });
  refreshReplayOptions();
  const comparison = metadata.point_results.PROMOTED_P50_MINUS_OFFICIAL_SCHEDULE?.mean_absolute_interval_distance_difference_seconds;
  const eligibility = metadata.point_diagnostics.models.PROMOTED_P50;
  elements["measured-result"].textContent = comparison ? `On ${eligibility.metric_eligible.raw_row_count.toLocaleString()} point-eligible held-out rows, with ${eligibility.excluded_censored_or_unavailable.raw_row_count.toLocaleString()} censored or unavailable rows excluded, promoted-model mean interval distance minus official schedule was ${comparison.estimate.toFixed(1)} seconds with a complete-service-day bootstrap interval from ${comparison.lower_95.toFixed(1)} to ${comparison.upper_95.toFixed(1)} seconds.` : "The final point diagnostic is unavailable.";
  for (const limitation of metadata.limitations) {
    const li = document.createElement("li");
    li.textContent = limitation;
    elements["limitation-list"].append(li);
  }
  elements.attribution.textContent = metadata.attribution;
  elements["model-footer"].textContent = `${metadata.model.bundle_id} · ${metadata.model.distribution} scale ${metadata.model.scale} · evidence ${metadata.evidence_version}`;
}

for (const control of [elements.direction, elements.origin, elements.destination]) control.addEventListener("change", refreshReplayOptions);
elements["replay-form"].addEventListener("submit", scoreReplay);
elements["reveal-button"].addEventListener("click", revealOutcome);

initialize().catch((error) => {
  elements["system-status"].textContent = "Local evidence unavailable";
  elements["control-error"].textContent = error.message;
  elements["control-error"].hidden = false;
  elements["replay-form"].querySelector("button").disabled = true;
});
