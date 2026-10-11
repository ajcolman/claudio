"use strict";
const $ = (id) => document.getElementById(id);
let state = null,
  refreshPromise = null,
  busy = false,
  autoDirty = false;
let linkTab = "session",
  selectedAccount = null,
  selectedProject = null,
  projectLimit = 6;
let toastTimer;
function readPreference(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem(`claudio:${key}`)) ?? fallback;
  } catch {
    return fallback;
  }
}
function savePreference(key, value) {
  try {
    localStorage.setItem(`claudio:${key}`, JSON.stringify(value));
  } catch {
    /* Private storage can be unavailable. */
  }
}
const storedFavorites = readPreference("favorites", []);
const favorites = new Set(
  Array.isArray(storedFavorites)
    ? storedFavorites.filter((x) => typeof x === "string")
    : [],
);
const storedTheme = readPreference("theme", "dark");
document.documentElement.dataset.theme =
  storedTheme === "light" ? "light" : "dark";
function escapeHTML(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
}
function pct(value) {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.max(0, Math.min(100, value))
    : null;
}
function usage(account, window) {
  return pct(account?.usage?.[window]?.pct);
}
function peak(account) {
  const values = [
    usage(account, "fiveHour"),
    usage(account, "sevenDay"),
  ].filter((x) => x !== null);
  return values.length ? Math.max(...values) : null;
}
function isActive(account) {
  return (
    account.active === true ||
    String(account.number) === String(state?.list?.activeAccountNumber)
  );
}
function accounts() {
  return state?.list?.accounts || [];
}
function showToast(message, error = false) {
  $("toast").textContent = message;
  $("toast").classList.toggle("error", error);
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(
    () => {
      $("toast").hidden = true;
    },
    error ? 6500 : 4000,
  );
}
async function request(path, payload) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 40000);
  try {
    const response = await fetch(path, {
      signal: controller.signal,
      cache: "no-store",
      ...(payload === undefined
        ? {}
        : {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          }),
    });
    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error("El servidor devolvió una respuesta inválida.");
    }
    if (!response.ok || data.success === false)
      throw new Error(
        data.error || `No se pudo completar la operación (${response.status}).`,
      );
    return data;
  } catch (error) {
    if (error.name === "AbortError")
      throw new Error(
        "El servidor tardó demasiado. Intenta actualizar de nuevo.",
      );
    if (error instanceof TypeError)
      throw new Error(
        "No se pudo conectar con Claudio. Comprueba que el servidor esté activo.",
      );
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}
async function fetchStatus() {
  if (refreshPromise) return refreshPromise;
  $("refresh-button").disabled = true;
  $("connection-text").textContent = "Actualizando…";
  refreshPromise = (async () => {
    try {
      const data = await request("/api/status");
      if (
        !data.status ||
        !Array.isArray(data.list?.accounts) ||
        !Array.isArray(data.projects)
      )
        throw new Error("El estado recibido no tiene el formato esperado.");
      state = data;
      renderAll();
      $("connection-error").hidden = true;
      $("connection-dot").className = "status-dot online";
      $("connection-text").textContent =
        `Actualizado ${new Date().toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" })}`;
    } catch (error) {
      $("connection-dot").className = "status-dot offline";
      $("connection-text").textContent = state
        ? "Datos sin actualizar"
        : "Sin conexión";
      $("connection-error").replaceChildren(
        document.createTextNode(
          `${error.message} ${state ? "Se muestran los últimos datos recibidos." : ""} `,
        ),
      );
      const retry = document.createElement("button");
      retry.className = "button";
      retry.textContent = "Reintentar";
      retry.onclick = fetchStatus;
      $("connection-error").append(retry);
      $("connection-error").hidden = false;
      if (!state) {
        $("active-badge").textContent = "Sin datos";
        $("active-content").innerHTML =
          '<h3>No pudimos cargar tu espacio</h3><p class="muted small">Comprueba la conexión y vuelve a intentarlo.</p>';
        $("accounts-container").innerHTML =
          '<div class="empty muted">Las cuentas aparecerán cuando se recupere la conexión.</div>';
        $("projects-grid").innerHTML =
          '<div class="empty muted">Los proyectos aparecerán cuando se recupere la conexión.</div>';
      }
    } finally {
      $("refresh-button").disabled = false;
      $("accounts-container").setAttribute("aria-busy", "false");
      $("projects-grid").setAttribute("aria-busy", "false");
      refreshPromise = null;
      updateControls();
    }
  })();
  return refreshPromise;
}
function updateControls() {
  document
    .querySelectorAll(
      '[data-action="quick-switch"], [data-action="switch"], [data-action="disable"], [data-action="alias"], [data-action="remove"], [data-action="link"]',
    )
    .forEach((button) => {
      button.disabled = busy || (!state && button.dataset.action !== "link");
    });
  $("auto-button").disabled = busy || !state;
  document
    .querySelectorAll('[data-action="terminal"], [data-action="parallel"]')
    .forEach((button) => {
      button.disabled = busy || button.dataset.unavailable === "true";
    });
  $("threshold-slider").disabled = busy || !!state?.auto_running;
  $("auto-strategy").disabled = busy || !!state?.auto_running;
}
async function mutate(path, payload, message, form = null) {
  if (busy) return false;
  busy = true;
  updateControls();
  const submit = form?.querySelector('[type="submit"]');
  const errorEl = form?.querySelector(".form-error");
  const dialog = form?.closest("dialog");
  if (errorEl) errorEl.hidden = true;
  if (dialog) dialog.dataset.busy = "true";
  if (submit) {
    submit.disabled = true;
    submit.dataset.label = submit.textContent;
    submit.textContent = "Procesando…";
  }
  try {
    // Wait for an earlier read so it cannot overwrite the result of this action.
    if (refreshPromise) await refreshPromise;
    const result = await request(path, payload);
    if (path === "/api/toggle-auto") autoDirty = false;
    if (dialog) dialog.close();
    showToast(result.warning || message, !!result.warning);
    await fetchStatus();
    return true;
  } catch (error) {
    if (errorEl) {
      errorEl.textContent = error.message;
      errorEl.hidden = false;
    } else showToast(error.message, true);
    return false;
  } finally {
    busy = false;
    if (dialog) delete dialog.dataset.busy;
    if (submit) {
      submit.disabled = false;
      submit.textContent = submit.dataset.label;
    }
    updateControls();
  }
}
function resetTime(value) {
  if (!value) return "Reinicio sin datos";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "Reinicio sin datos";
  const mins = Math.ceil((date - Date.now()) / 60000);
  if (mins <= 0) return "Pendiente de actualizar";
  if (mins < 60) return `Reinicia en ${mins} min`;
  const hours = Math.floor(mins / 60);
  return hours >= 24
    ? `Reinicia en ${Math.floor(hours / 24)} d ${hours % 24} h`
    : `Reinicia en ${hours} h ${mins % 60} min`;
}
function metric(account, window, label) {
  const value = usage(account, window);
  return `<div class="metric"><div class="metric-head"><span>${label}</span><strong>${value === null ? "Sin datos" : `${value.toLocaleString("es", { maximumFractionDigits: 1 })} %`}</strong></div><div class="meter ${value >= 90 ? "high" : value >= 70 ? "medium" : ""}" ${value === null ? "" : `role="progressbar" aria-label="Consumo ${label}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${value}"`}><span style="width:${value ?? 0}%"></span></div><div class="reset">${resetTime(account?.usage?.[window]?.resetsAt)}</div></div>`;
}
function identity(account, active = false) {
  const label = account.alias || account.email || `Cuenta #${account.number}`;
  return `<div class="identity"><span class="avatar" aria-hidden="true">${escapeHTML(active ? label.charAt(0).toUpperCase() : `#${account.number}`)}</span><div class="identity-info"><h3>${escapeHTML(label)}</h3><p>${escapeHTML(account.alias ? account.email : active ? "Sesión de Claude Code en este equipo" : "Sin alias asignado")}</p></div></div>`;
}
function renderAll() {
  const list = accounts();
  $("stat-accounts").textContent = list.length;
  $("stat-enabled").textContent = list.filter((a) => !a.disabled).length;
  $("stat-warning").textContent = list.filter((a) => peak(a) >= 90).length;
  $("stat-projects").textContent = state.projects.length;
  renderActive();
  renderAuto();
  renderAccounts();
  renderProjects();
  updateControls();
}
function renderActive() {
  const active = state.status.active || {};
  const account =
    accounts().find((a) => isActive(a)) ||
    accounts().find((a) => active.email && a.email === active.email);
  const present = !!(active.email || account);
  $("active-badge").textContent = present
    ? account
      ? "● Vinculada"
      : "Sin vincular"
    : "Sin sesión";
  $("active-badge").className = `badge ${present && account ? "good" : ""}`;
  $("register-active").hidden = !present || !!account;
  $("active-content").innerHTML = present
    ? `${identity({ ...account, email: active.email || account?.email }, true)}<div class="metrics">${metric(account, "fiveHour", "Ventana de 5 horas")}${metric(account, "sevenDay", "Ventana semanal")}</div>`
    : '<h3>Conecta tu primera cuenta</h3><p class="muted">Abre una nueva sesión de Claude Code e inicia sesión para comenzar.</p>';
}
function renderAuto() {
  const running = !!state.auto_running;
  $("auto-badge").textContent = running ? "● Activa" : "Inactiva";
  $("auto-badge").className = `badge ${running ? "good" : ""}`;
  $("auto-button").textContent = running
    ? "Detener rotación"
    : "Activar rotación";
  $("auto-button").className = `button full ${running ? "danger" : ""}`;
  $("auto-help").textContent = running
    ? "Detén la rotación para cambiar su configuración."
    : "Configura el umbral y activa la rotación.";
  if (!autoDirty || running) {
    $("threshold-slider").value = state.auto_config?.threshold ?? 90;
    $("auto-strategy").value = state.auto_config?.strategy || "best";
    $("threshold-value").textContent = `${$("threshold-slider").value} %`;
  }
}
function renderAccounts() {
  const query = $("account-search").value.trim().toLocaleLowerCase();
  const filter = $("account-filter").value;
  const list = accounts()
    .filter((a) =>
      `${a.alias || ""} ${a.email || ""}`.toLocaleLowerCase().includes(query),
    )
    .filter(
      (a) =>
        filter === "all" ||
        (filter === "enabled" && !a.disabled) ||
        (filter === "disabled" && a.disabled) ||
        (filter === "warning" && peak(a) >= 90),
    );
  const sort = $("account-sort").value;
  list.sort((a, b) =>
    sort === "name"
      ? (a.alias || a.email || "").localeCompare(b.alias || b.email || "", "es")
      : sort === "quota"
        ? (peak(a) ?? 101) - (peak(b) ?? 101)
        : Number(isActive(b)) - Number(isActive(a)) ||
          Number(a.number) - Number(b.number),
  );
  $("accounts-count").textContent = `${list.length} / ${accounts().length}`;
  $("accounts-container").innerHTML = list.length
    ? list
        .map((a) => {
          const id = escapeHTML(a.number);
          return `<article class="panel account-card ${isActive(a) ? "is-active" : ""} ${a.disabled ? "is-disabled" : ""}">${identity(a)}<div class="account-label">${isActive(a) ? '<span class="badge good">● En uso</span>' : ""}<span class="badge ${peak(a) >= 90 ? "warn" : ""}">${a.disabled ? "Pausada" : peak(a) >= 90 ? "Consumo alto" : "En rotación"}</span></div><div class="metrics">${metric(a, "fiveHour", "Ventana de 5 horas")}${metric(a, "sevenDay", "Ventana semanal")}</div><div class="card-actions"><button class="button subtle icon-button" data-action="alias" data-account="${id}" aria-label="Editar alias de ${escapeHTML(a.email)}" title="Editar alias">✎</button><button class="button subtle icon-button" data-action="disable" data-account="${id}" title="${a.disabled ? "Reanudar" : "Pausar"} rotación" aria-label="${a.disabled ? "Reanudar" : "Pausar"} rotación de ${escapeHTML(a.email)}">${a.disabled ? "▷" : "Ⅱ"}</button><button class="button subtle icon-button" data-action="remove" data-account="${id}" title="Desvincular cuenta" aria-label="Desvincular ${escapeHTML(a.email)}">×</button>${isActive(a) ? '<span class="muted small switch">Cuenta activa</span>' : `<button class="button switch" data-action="switch" data-account="${id}">Usar cuenta →</button>`}</div></article>`;
        })
        .join("")
    : `<div class="empty"><h3>${accounts().length ? "No hay cuentas que coincidan" : "Tu próxima sesión empieza aquí"}</h3><p class="muted small">${accounts().length ? "Prueba con otro nombre o cambia el filtro." : "Vincula tu cuenta actual para consultar su consumo y alternar entre cuentas."}</p><button class="button" data-action="${accounts().length ? "clear-accounts" : "link"}">${accounts().length ? "Limpiar filtros" : "＋ Vincular primera cuenta"}</button></div>`;
  updateControls();
}
function renderProjects() {
  const query = $("project-search").value.trim().toLocaleLowerCase();
  const filter = $("project-filter").value;
  const list = (state?.projects || [])
    .filter((p) => `${p.name} ${p.path}`.toLocaleLowerCase().includes(query))
    .filter(
      (p) =>
        filter === "all" ||
        (filter === "favorites" && favorites.has(p.path)) ||
        (filter === "available" && p.exists),
    )
    .sort(
      (a, b) => Number(favorites.has(b.path)) - Number(favorites.has(a.path)),
    );
  $("projects-count").textContent =
    `${list.length} / ${state?.projects.length || 0}`;
  $("projects-grid").innerHTML = list.length
    ? list
        .slice(0, projectLimit)
        .map(
          (p) =>
            `<article class="project-card"><div class="section-top"><div class="project-title"><span class="folder-icon" aria-hidden="true">▱</span><h3>${escapeHTML(p.name)}</h3></div><button class="button subtle icon-button favorite" data-action="favorite" data-path="${escapeHTML(p.path)}" aria-label="${favorites.has(p.path) ? "Quitar de" : "Añadir a"} favoritos: ${escapeHTML(p.name)}" aria-pressed="${favorites.has(p.path)}">${favorites.has(p.path) ? "★" : "☆"}</button></div><code class="project-path" title="${escapeHTML(p.path)}">${escapeHTML(p.path)}</code>${!p.exists ? '<p class="small" style="color:var(--warning);margin-top:8px">Carpeta no disponible</p>' : ""}<div class="project-actions"><button class="button" data-action="terminal" data-mode="resume" data-path="${escapeHTML(p.path)}" data-unavailable="${!p.exists}" ${!p.exists ? "disabled" : ""}>↗ Reanudar</button><button class="button subtle" data-action="parallel" data-path="${escapeHTML(p.path)}" data-unavailable="${!p.exists || !accounts().length}" ${!p.exists || !accounts().length ? "disabled" : ""}>Sesión paralela</button><button class="button subtle" data-action="copy-path" data-path="${escapeHTML(p.path)}" aria-label="Copiar ruta de ${escapeHTML(p.name)}" title="Copiar ruta">Copiar ruta</button></div></article>`,
        )
        .join("")
    : `<div class="empty"><h3>${state?.projects.length ? "No hay proyectos que coincidan" : "Tus proyectos aparecerán aquí"}</h3><p class="muted small">${state?.projects.length ? "Cambia la búsqueda o el filtro para ver otros proyectos." : "Abre Claude Code en una carpeta para que quede registrada en tu historial local."}</p>${state?.projects.length ? '<button class="button" data-action="clear-projects">Limpiar filtros</button>' : ""}</div>`;
  $("show-projects").hidden = list.length <= projectLimit;
  $("show-projects").textContent =
    `Mostrar más proyectos (${Math.max(0, list.length - projectLimit)} restantes)`;
  updateControls();
}
function openDialog(id) {
  const dialog = $(id);
  const error = dialog.querySelector(".form-error");
  if (error) error.hidden = true;
  dialog.showModal();
}
function switchTab(tab) {
  linkTab = tab;
  ["session", "token"].forEach((name) => {
    $(`tab-${name}`).setAttribute("aria-selected", String(name === tab));
    $(`tab-${name}`).tabIndex = name === tab ? 0 : -1;
    $(`${name}-panel`).hidden = name !== tab;
  });
  $("token-value").required = tab === "token";
  $("link-form").querySelector(".form-error").hidden = true;
}
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    showToast("Ruta copiada al portapapeles.");
  } catch {
    showToast(
      "No se pudo copiar. Puedes seleccionar la ruta del proyecto manualmente.",
      true,
    );
  }
}
document.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button || button.disabled) return;
  if (button.classList.contains("close-dialog")) {
    const dialog = button.closest("dialog");
    if (!dialog.dataset.busy) dialog.close();
    return;
  }
  if (button.dataset.tab) {
    switchTab(button.dataset.tab);
    return;
  }
  const action = button.dataset.action;
  const account = accounts().find(
    (a) => String(a.number) === button.dataset.account,
  );
  if (action === "link") {
    switchTab("session");
    openDialog("link-dialog");
  }
  if (action === "clear-accounts") {
    $("account-search").value = "";
    $("account-filter").value = "all";
    renderAccounts();
  }
  if (action === "clear-projects") {
    $("project-search").value = "";
    $("project-filter").value = "all";
    projectLimit = 6;
    renderProjects();
  }
  if (action === "switch" && account)
    mutate(
      "/api/switch",
      { target: account.number },
      "Cuenta cambiada. Ya puedes reanudar tu sesión.",
    );
  if (action === "quick-switch")
    mutate(
      "/api/switch",
      { strategy: button.dataset.strategy },
      "Rotación completada.",
    );
  if (action === "disable" && account)
    mutate(
      "/api/toggle-account",
      { account: account.number, disable: !account.disabled },
      account.disabled
        ? "Cuenta incorporada a la rotación."
        : "Cuenta pausada de la rotación.",
    );
  if (action === "alias" && account) {
    selectedAccount = account;
    $("alias-email").textContent = account.email;
    $("alias-value").value = account.alias || "";
    openDialog("alias-dialog");
    $("alias-value").focus();
  }
  if (action === "remove" && account) {
    selectedAccount = account;
    $("remove-description").textContent =
      `¿Quieres desvincular ${account.alias || account.email}?`;
    openDialog("remove-dialog");
  }
  if (action === "terminal")
    mutate(
      "/api/open-terminal",
      { mode: button.dataset.mode, cwd: button.dataset.path },
      "Se solicitó la apertura de la terminal.",
    );
  if (action === "favorite") {
    const path = button.dataset.path;
    favorites.has(path) ? favorites.delete(path) : favorites.add(path);
    savePreference("favorites", [...favorites]);
    renderProjects();
  }
  if (action === "copy-path") copyText(button.dataset.path);
  if (action === "parallel") {
    selectedProject = button.dataset.path;
    $("parallel-project").textContent = selectedProject;
    $("parallel-account").innerHTML = accounts()
      .map(
        (a) =>
          `<option value="${escapeHTML(a.number)}">${escapeHTML(a.alias || a.email)}${isActive(a) ? " (activa)" : ""}</option>`,
      )
      .join("");
    openDialog("parallel-dialog");
  }
});
$("link-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const token = $("token-value").value.trim(),
    email = $("token-email").value.trim(),
    alias = $("token-alias").value.trim();
  if (linkTab === "token" && (!token || (alias && !email))) {
    const error = event.target.querySelector(".form-error");
    error.textContent = !token
      ? "Ingresa el token o API key."
      : "Indica un correo o identificador para asignar el alias.";
    error.hidden = false;
    return;
  }
  const ok = await mutate(
    linkTab === "session" ? "/api/add-current" : "/api/add-token",
    linkTab === "session"
      ? { alias: $("session-alias").value.trim() }
      : { token, email, alias },
    "Cuenta vinculada.",
    event.target,
  );
  if (ok) event.target.reset();
});
$("alias-form").addEventListener("submit", (event) => {
  event.preventDefault();
  mutate(
    "/api/alias",
    { account: selectedAccount.number, alias: $("alias-value").value.trim() },
    "Alias guardado.",
    event.target,
  );
});
$("remove-form").addEventListener("submit", (event) => {
  event.preventDefault();
  mutate(
    "/api/remove",
    { account: selectedAccount.number },
    "Cuenta desvinculada.",
    event.target,
  );
});
$("parallel-form").addEventListener("submit", (event) => {
  event.preventDefault();
  mutate(
    "/api/open-terminal",
    {
      mode: "parallel",
      account: $("parallel-account").value,
      cwd: selectedProject,
    },
    "Se solicitó la apertura de la sesión paralela.",
    event.target,
  );
});
$("auto-button").onclick = () =>
  mutate(
    "/api/toggle-auto",
    {
      enable: !state?.auto_running,
      threshold: Number($("threshold-slider").value),
      strategy: $("auto-strategy").value,
    },
    state?.auto_running ? "Rotación detenida." : "Rotación activada.",
  );
$("threshold-slider").oninput = () => {
  autoDirty = true;
  $("threshold-value").textContent = `${$("threshold-slider").value} %`;
};
$("auto-strategy").onchange = () => {
  autoDirty = true;
};
$("theme-button").onclick = () => {
  const theme =
    document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  savePreference("theme", theme);
};
$("refresh-button").onclick = fetchStatus;
["account-search", "account-filter", "account-sort"].forEach((id) =>
  $(id).addEventListener(id.includes("search") ? "input" : "change", () => {
    if (state) renderAccounts();
  }),
);
["project-search", "project-filter"].forEach((id) =>
  $(id).addEventListener(id.includes("search") ? "input" : "change", () => {
    projectLimit = 6;
    if (state) renderProjects();
  }),
);
$("show-projects").onclick = () => {
  projectLimit += 6;
  renderProjects();
};
$("show-token").onclick = () => {
  const visible = $("token-value").type === "password";
  $("token-value").type = visible ? "text" : "password";
  $("show-token").textContent = visible ? "Ocultar" : "Mostrar";
  $("show-token").setAttribute(
    "aria-label",
    visible ? "Ocultar token" : "Mostrar token",
  );
  $("show-token").setAttribute("aria-pressed", String(visible));
};
$("link-dialog").addEventListener("close", () => {
  $("token-value").value = "";
  $("token-value").type = "password";
  $("show-token").textContent = "Mostrar";
  $("show-token").setAttribute("aria-label", "Mostrar token");
  $("show-token").setAttribute("aria-pressed", "false");
});
document.querySelectorAll("dialog").forEach((dialog) =>
  dialog.addEventListener("cancel", (event) => {
    if (dialog.dataset.busy) event.preventDefault();
  }),
);
document
  .querySelector('[role="tablist"]')
  .addEventListener("keydown", (event) => {
    if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      switchTab(
        event.key === "Home"
          ? "session"
          : event.key === "End"
            ? "token"
            : linkTab === "session"
              ? "token"
              : "session",
      );
      $(`tab-${linkTab}`).focus();
    }
  });
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !busy) fetchStatus();
});
updateControls();
fetchStatus();
setInterval(() => {
  if (!document.hidden && !busy && !document.querySelector("dialog[open]"))
    fetchStatus();
}, 30000);
