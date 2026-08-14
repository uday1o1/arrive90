const elements = Object.fromEntries(
  [
    "system-pill", "search-form", "origin", "destination", "ready-at", "deadline", "target",
    "cap", "cap-output", "search-button", "form-error", "results", "decision-status",
    "normalization", "requested-ready", "effective-ready", "requested-deadline",
    "effective-deadline", "fastest-summary", "fastest-time", "fastest-transfers",
    "fastest-model-status", "safer-summary", "safer-time", "safer-extra", "safer-transfers",
    "probability-panel", "deadline-probability", "probability-meter", "selected-model-status",
    "quantile-rows", "quantile-empty", "timeline", "backup-summary", "explanations",
    "start-trip", "confirm-boarded", "confirm-transfer", "stop-trip", "trip-state",
    "trip-guidance", "event-log", "recovery-card", "recovery-reason", "recovery-route",
    "activate-recovery",
  ].map((id) => [id, document.getElementById(id)]),
);

const session = {
  decisionCapability: null,
  selectedItineraryId: null,
  selectedTransferCount: 0,
  tripId: null,
  tripBearer: null,
  stateVersion: 0,
  lastEventId: 0,
  recoveryDecision: null,
};

const explanationText = {
  EXTRA_TIME_FOR_RELIABILITY: "The selected route adds scheduled time in exchange for its higher modeled deadline estimate.",
  HISTORICAL_SUPPORT_SPARSE: "The required historical support cell is unavailable, so the model output is suppressed.",
  LIVE_FEED_STALE: "The applicable live evidence is stale and the result is schedule-only.",
};

function localInputValue(date) {
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return shifted.toISOString().slice(0, 16);
}

function formatTime(value) {
  if (!value) return "Unavailable";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function minutes(seconds) {
  if (seconds === null || seconds === undefined) return "Unavailable";
  return `${Math.round(seconds / 60)} min`;
}

function clearChildren(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function appendListItem(parent, text) {
  const item = document.createElement("li");
  item.textContent = text;
  parent.append(item);
}

function resetSession() {
  session.decisionCapability = null;
  session.selectedItineraryId = null;
  session.selectedTransferCount = 0;
  session.tripId = null;
  session.tripBearer = null;
  session.stateVersion = 0;
  session.lastEventId = 0;
  session.recoveryDecision = null;
  elements["trip-state"].textContent = "No active trip.";
  elements["start-trip"].hidden = false;
  elements["confirm-boarded"].hidden = true;
  elements["confirm-transfer"].hidden = true;
  elements["stop-trip"].hidden = true;
  elements["recovery-card"].hidden = true;
  clearChildren(elements["event-log"]);
}

function renderSlot(prefix, slot, selected) {
  const unavailable = prefix === "safer" ? "selected-model-status" : `${prefix}-model-status`;
  if (!slot) {
    elements[`${prefix}-summary`].textContent = "No eligible itinerary.";
    elements[`${prefix}-time`].textContent = "Unavailable";
    elements[`${prefix}-transfers`].textContent = "Unavailable";
    if (prefix === "safer") elements["safer-extra"].textContent = "Unavailable";
    elements[unavailable].textContent = "No route is available for this decision state.";
    return;
  }
  const routeType = slot.transfer_count === 0 ? "Direct itinerary" : "One-transfer itinerary";
  elements[`${prefix}-summary`].textContent = routeType;
  elements[`${prefix}-time`].textContent = minutes(slot.planned_time_seconds);
  elements[`${prefix}-transfers`].textContent = String(slot.transfer_count);
  if (prefix === "safer") elements["safer-extra"].textContent = minutes(slot.extra_planned_time_seconds);
  if (!selected || slot.deadline_probability === null) {
    elements[unavailable].textContent = selected
      ? "Model probability and quantiles are unavailable for this result."
      : "Probability unavailable: this comparator output has not been validated.";
  } else {
    elements[unavailable].textContent = "Selected model output passed the configured support lookup for this fixture.";
  }
}

function renderNormalization(body) {
  const changed = body.ready_time_status !== "AS_REQUESTED" || body.deadline_time_status !== "AS_REQUESTED";
  elements.normalization.hidden = !changed;
  elements["requested-ready"].textContent = formatTime(body.requested_ready_at);
  elements["effective-ready"].textContent = formatTime(body.effective_ready_at);
  elements["requested-deadline"].textContent = formatTime(body.requested_deadline_at);
  elements["effective-deadline"].textContent = formatTime(body.effective_deadline_at);
}

function renderProbability(slot) {
  clearChildren(elements["quantile-rows"]);
  const probability = slot?.deadline_probability;
  elements["probability-panel"].hidden = probability === null || probability === undefined;
  if (probability !== null && probability !== undefined) {
    const numeric = Number(probability);
    elements["deadline-probability"].textContent = `${Math.round(numeric * 100)}% estimate`;
    elements["probability-meter"].value = numeric;
  }
  const quantiles = Object.entries(slot?.arrival_quantiles || {});
  elements["quantile-empty"].hidden = quantiles.length > 0;
  for (const [level, arrival] of quantiles) {
    const row = document.createElement("tr");
    const label = document.createElement("th");
    const value = document.createElement("td");
    label.scope = "row";
    label.textContent = level;
    value.textContent = formatTime(arrival);
    row.append(label, value);
    elements["quantile-rows"].append(row);
  }
}

function renderDetails(body) {
  clearChildren(elements.timeline);
  const recommendation = body.recommended_itinerary;
  if (recommendation) {
    appendListItem(elements.timeline, recommendation.transfer_count === 0 ? "Board the direct service." : "Board the first leg.");
    if (recommendation.transfer_count === 1) appendListItem(elements.timeline, "Transfer once at the scheduled interchange.");
    appendListItem(elements.timeline, `Scheduled arrival after ${minutes(recommendation.planned_time_seconds)}.`);
  } else {
    appendListItem(elements.timeline, "No itinerary timeline is available.");
  }
  elements["backup-summary"].textContent = body.backup_itinerary
    ? `Backup: ${body.backup_itinerary.transfer_count === 0 ? "direct" : "one transfer"}, ${minutes(body.backup_itinerary.planned_time_seconds)} planned.`
    : "No distinct backup itinerary is available.";
  clearChildren(elements.explanations);
  const codes = body.explanation_codes || [];
  if (body.feed_status === "STALE") codes.push("LIVE_FEED_STALE");
  if (codes.length === 0) appendListItem(elements.explanations, "The deterministic policy selected the earliest cap-eligible route meeting the requested target.");
  for (const code of [...new Set(codes)]) appendListItem(elements.explanations, explanationText[code] || code.replaceAll("_", " ").toLowerCase());
  if (body.limitations.length > 0) {
    for (const limitation of body.limitations) appendListItem(elements.explanations, limitation.replaceAll("_", " ").toLowerCase());
  }
}

function statusText(body) {
  const labels = {
    TARGET_MET: "Estimated target met",
    TARGET_NOT_MET: "Target not met",
    DEGRADED_SCHEDULE_ONLY: "Future request: schedule only",
    STALE_LIVE_DATA: "Stale feed: schedule only",
    MODEL_ABSTAINED: "Model abstained",
    INSUFFICIENT_EVIDENCE: "Insufficient evidence",
    NO_SUPPORTED_ITINERARY: "No supported itinerary",
  };
  const evidence = body.model_version.startsWith("SYNTHETIC_") ? "Synthetic fixture. " : "";
  return `${evidence}${labels[body.target_status] || body.target_status}. Feed: ${body.feed_status.toLowerCase()}.`;
}

function renderSearch(body) {
  resetSession();
  session.decisionCapability = body.decision_id;
  session.selectedItineraryId = body.recommended_itinerary?.itinerary_id || null;
  session.selectedTransferCount = body.recommended_itinerary?.transfer_count || 0;
  elements.results.hidden = false;
  elements["decision-status"].textContent = statusText(body);
  elements["decision-status"].classList.toggle("warning", body.target_status !== "TARGET_MET");
  renderNormalization(body);
  renderSlot("fastest", body.fastest_itinerary, false);
  renderSlot("safer", body.recommended_itinerary, true);
  renderProbability(body.recommended_itinerary);
  renderDetails(body);
  const startable = body.trip_start_supported && Boolean(body.decision_id);
  elements["start-trip"].disabled = !startable;
  elements["trip-guidance"].textContent = startable
    ? "Trip state changes only after your explicit confirmation."
    : body.support_status === "UNSUPPORTED_READY_HORIZON"
      ? "Search again within 15 minutes of readiness before starting a trip."
      : "Trip start is unavailable for this degraded or unsupported result.";
  elements.results.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || "Request failed");
  return body;
}

async function loadStations() {
  const [stationBody, statusBody] = await Promise.all([api("/v1/stations"), api("/v1/system/status")]);
  for (const station of stationBody.stations) {
    for (const select of [elements.origin, elements.destination]) {
      const option = document.createElement("option");
      option.value = station.station_id;
      option.textContent = station.name;
      select.append(option);
    }
  }
  if (stationBody.stations.length > 1) elements.destination.selectedIndex = stationBody.stations.length - 1;
  elements["system-pill"].textContent = `${statusBody.status.toLowerCase()} · ${statusBody.release_mode.replaceAll("_", " ").toLowerCase()}`;
}

async function search(event) {
  event.preventDefault();
  elements["form-error"].hidden = true;
  elements["search-button"].disabled = true;
  try {
    const body = await api("/v1/journeys/search", {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: window.location.origin },
      body: JSON.stringify({
        origin_station_id: elements.origin.value,
        destination_station_id: elements.destination.value,
        ready_at: new Date(elements["ready-at"].value).toISOString(),
        deadline: new Date(elements.deadline.value).toISOString(),
        reliability_target: elements.target.value,
        maximum_extra_minutes: Number(elements.cap.value),
      }),
    });
    renderSearch(body);
  } catch (error) {
    elements["form-error"].textContent = error instanceof Error ? error.message : "Search failed";
    elements["form-error"].hidden = false;
  } finally {
    elements["search-button"].disabled = false;
  }
}

function authHeaders() {
  return {
    Authorization: `Bearer ${session.tripBearer}`,
    "Content-Type": "application/json",
    Origin: window.location.origin,
  };
}

async function refreshEvents() {
  if (!session.tripId || !session.tripBearer) return;
  const response = await fetch(`/v1/trips/${session.tripId}/events`, {
    headers: {
      Authorization: `Bearer ${session.tripBearer}`,
      "Last-Event-ID": String(session.lastEventId),
    },
  });
  if (!response.ok) return;
  const text = await response.text();
  for (const block of text.split("\n\n")) {
    const dataLine = block.split("\n").find((line) => line.startsWith("data: "));
    if (!dataLine) continue;
    const event = JSON.parse(dataLine.slice(6));
    session.lastEventId = Math.max(session.lastEventId, event.sequence || 0);
    appendListItem(elements["event-log"], `${event.event_kind.replaceAll("_", " ").toLowerCase()} · ${event.value_provenance.replaceAll("_", " ").toLowerCase()}`);
  }
}

async function startTrip() {
  const body = await api("/v1/trips", {
    method: "POST",
    headers: { "Content-Type": "application/json", Origin: window.location.origin },
    body: JSON.stringify({ decision_id: session.decisionCapability, selected_itinerary_id: session.selectedItineraryId }),
  });
  session.decisionCapability = null;
  session.tripId = body.trip_id;
  session.tripBearer = body.trip_bearer;
  session.stateVersion = body.state_version;
  elements["start-trip"].hidden = true;
  elements["confirm-boarded"].hidden = false;
  elements["stop-trip"].hidden = false;
  elements["trip-state"].textContent = "Trip active. Awaiting boarding confirmation.";
  await refreshEvents();
}

async function transition(nextState, boardedIdentifier = null, recoveryDecisionId = null) {
  const body = await api(`/v1/trips/${session.tripId}/state`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({
      idempotency_key: crypto.randomUUID(),
      expected_state_version: session.stateVersion,
      next_state: nextState,
      boarded_itinerary_or_route_pattern_id: boardedIdentifier,
      recovery_decision_id: recoveryDecisionId,
    }),
  });
  session.stateVersion = body.state_version;
  elements["trip-state"].textContent = `Confirmed state: ${body.state.replaceAll("_", " ").toLowerCase()}.`;
  if (body.recovery_decision) renderRecovery(body.recovery_decision);
  await refreshEvents();
}

async function confirmBoarded() {
  const next = session.selectedTransferCount === 0 ? "ON_FINAL_LEG" : "ON_FIRST_LEG";
  await transition(next, session.selectedItineraryId);
  elements["confirm-boarded"].hidden = true;
  elements["confirm-transfer"].hidden = session.selectedTransferCount === 0;
}

async function confirmTransfer() {
  await transition("AT_TRANSFER");
  elements["confirm-transfer"].hidden = true;
}

function renderRecovery(recovery) {
  session.recoveryDecision = recovery;
  elements["recovery-card"].hidden = false;
  elements["recovery-reason"].textContent = `Reason: ${recovery.reason.replaceAll("_", " ").toLowerCase()}. This guidance is conditional on the confirmed transfer state.`;
  elements["recovery-route"].textContent = `Schedule option: ${recovery.recommendation.transfer_count === 0 ? "direct" : "one transfer"}, ${minutes(recovery.recommendation.planned_time_seconds)} planned. A distinct continuation comparator and cap reference remain recorded.`;
}

async function activateRecovery() {
  const recovery = session.recoveryDecision;
  if (!recovery) return;
  await transition(
    recovery.recommendation.transfer_count === 0 ? "ON_FINAL_LEG" : "ON_FIRST_LEG",
    recovery.recommendation.itinerary_id,
    recovery.recovery_decision_id,
  );
  elements["activate-recovery"].disabled = true;
}

async function stopTrip() {
  const body = await api(`/v1/trips/${session.tripId}/stop`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ idempotency_key: crypto.randomUUID(), expected_state_version: session.stateVersion }),
  });
  session.tripBearer = null;
  session.tripId = null;
  elements["trip-state"].textContent = `Trip stopped in state ${body.state.toLowerCase()}.`;
  elements["confirm-boarded"].hidden = true;
  elements["confirm-transfer"].hidden = true;
  elements["stop-trip"].hidden = true;
}

const now = new Date();
now.setSeconds(0, 0);
elements["ready-at"].value = localInputValue(new Date(now.getTime() + 60_000));
elements.deadline.value = localInputValue(new Date(now.getTime() + 31 * 60_000));
elements.cap.addEventListener("input", () => { elements["cap-output"].value = elements.cap.value; });
elements["search-form"].addEventListener("submit", search);
elements["start-trip"].addEventListener("click", () => startTrip().catch((error) => { elements["trip-state"].textContent = error.message; }));
elements["confirm-boarded"].addEventListener("click", () => confirmBoarded().catch((error) => { elements["trip-state"].textContent = error.message; }));
elements["confirm-transfer"].addEventListener("click", () => confirmTransfer().catch((error) => { elements["trip-state"].textContent = error.message; }));
elements["activate-recovery"].addEventListener("click", () => activateRecovery().catch((error) => { elements["trip-state"].textContent = error.message; }));
elements["stop-trip"].addEventListener("click", () => stopTrip().catch((error) => { elements["trip-state"].textContent = error.message; }));

loadStations().catch(() => {
  elements["system-pill"].textContent = "Local service unavailable";
  elements["form-error"].textContent = "Could not load station data.";
  elements["form-error"].hidden = false;
});
