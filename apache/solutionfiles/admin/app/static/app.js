"use strict";

const csrf = document.querySelector("meta[name=csrf]").content;
const $ = (selector) => document.querySelector(selector);

// Elements are always built with textContent, so file and site names can never inject HTML
function el(tag, props, ...children) {
	const node = document.createElement(tag);
	Object.assign(node, props || {});
	for (const child of children) node.append(child);
	return node;
}

async function api(url, options = {}) {
	options.headers = Object.assign({ "X-CSRF-Token": csrf }, options.headers || {});
	if (options.json !== undefined) {
		options.method = options.method || "POST";
		options.headers["Content-Type"] = "application/json";
		options.body = JSON.stringify(options.json);
	}
	const response = await fetch(url, options);
	if (response.status === 401) {
		location.href = "/login";
		throw new Error("Not logged in");
	}
	const data = await response.json().catch(() => ({}));
	if (!response.ok) throw new Error(data.error || response.statusText);
	return data;
}

function positionToasts() {
	const container = $("#toasts");
	const dialogs = document.querySelectorAll("dialog[open]");
	const host = dialogs.length ? dialogs[dialogs.length - 1] : document.body;
	if (container.hidePopover) container.hidePopover();
	host.append(container);
	if (container.childElementCount && container.showPopover) container.showPopover();
}
document.addEventListener("close", positionToasts, true);

function toast(message, type) {
	const container = $("#toasts");
	const node = el("div", { className: "toast " + (type || "") },
		el("span", { textContent: message }));
	node.setAttribute("role", type === "error" ? "alert" : "status");
	const dismiss = () => {
		node.remove();
		if (!container.childElementCount && container.hidePopover) container.hidePopover();
	};
	if (type === "error") {
		const close = el("button", { type: "button", textContent: "Close", onclick: dismiss });
		close.setAttribute("aria-label", "Dismiss error");
		node.append(close);
		console.error(message);
	} else {
		setTimeout(dismiss, 3500);
	}
	container.append(node);
	positionToasts();
}

async function run(action) {
	try {
		return await action();
	} catch (err) {
		toast(err.message, "error");
	}
}

async function copyText(text, container) {
	if (navigator.clipboard) {
		try {
			await navigator.clipboard.writeText(text);
			return;
		} catch (error) {
			if (error.name !== "NotAllowedError" && error.name !== "SecurityError") throw error;
		}
	}
	const previous = document.activeElement;
	const selection = previous instanceof HTMLInputElement
		? { start: previous.selectionStart, end: previous.selectionEnd } : null;
	const area = el("textarea", { value: text, className: "clipboard-transfer", readOnly: true });
	container.append(area);
	try {
		area.focus();
		area.select();
		if (!document.execCommand("copy")) {
			throw new Error("Copy was blocked by the browser. Select the confirmation text and copy it manually.");
		}
	} finally {
		area.remove();
		if (previous instanceof HTMLElement) previous.focus({ preventScroll: true });
		if (selection && selection.start !== null && selection.end !== null) {
			previous.setSelectionRange(selection.start, selection.end);
		}
	}
}

let copyStatusTimer = null;

// Replaces prompt()/confirm(). Resolves to the entered text (or true without an input), or null when cancelled.
function ask({ title, message = "", value = "", placeholder = "", ok = "OK", danger = false, input = true, match = null, extra = null }) {
	const dlg = $("#dlg-prompt");
	const field = $("#prompt-input");
	const extraRow = $("#prompt-extra-row");
	const extraField = $("#prompt-extra");
	const okButton = $("#prompt-ok");
	$("#prompt-title").textContent = title;
	const messageBox = $("#prompt-message");
	const copyStatus = $("#prompt-copy-status");
	clearTimeout(copyStatusTimer);
	copyStatus.textContent = "";
	copyStatus.title = "";
	messageBox.textContent = message;
	const matchIndex = match ? message.indexOf(match) : -1;
	if (matchIndex >= 0) {
		const copy = el("button", {
			type: "button", className: "confirmation-copy", textContent: match,
			title: "Copy confirmation text", ariaLabel: "Copy confirmation text: " + match,
			onclick: async () => {
				let status;
				try {
					await copyText(match, dlg);
					status = "Copied to clipboard.";
				} catch (error) {
					status = error.message;
				}
				if (!dlg.open || !copy.isConnected) return;
				if (input) field.focus({ preventScroll: true });
				clearTimeout(copyStatusTimer);
				copyStatus.textContent = status;
				copyStatus.title = status;
				copyStatusTimer = setTimeout(() => {
					copyStatus.textContent = "";
					copyStatus.title = "";
				}, 2500);
			},
		});
		messageBox.replaceChildren(message.slice(0, matchIndex), copy, message.slice(matchIndex + match.length));
	}
	$("#prompt-message").hidden = !message;
	field.hidden = !input;
	field.value = value;
	field.placeholder = match !== null ? "" : placeholder;
	extraRow.hidden = !extra;
	if (extra) {
		$("#prompt-extra-label").textContent = extra.label;
		extraField.value = extra.value || "";
		extraField.placeholder = extra.placeholder || "";
	}
	okButton.value = "ok";
	okButton.textContent = ok;
	okButton.disabled = match !== null;
	dlg.classList.toggle("danger", danger);
	field.oninput = () => { if (match !== null) okButton.disabled = field.value !== match; };
	$("#prompt-cancel").onclick = () => dlg.close("cancel");
	dlg.returnValue = "";
	return new Promise((resolve) => {
		dlg.onclose = () => resolve(dlg.returnValue === "ok"
			? (extra ? { value: field.value, extra: extraField.value } : input ? field.value : true)
			: null);
		dlg.showModal();
		if (input) { field.focus(); field.select(); } else if (extra) extraField.focus(); else okButton.focus();
	});
}

// ------------------------------------------------------------------ views

let remoteFilesSite = "";
let serverFiles = false;
let currentView = "sites";
const views = ["sites", "site", "sql"];

function showView(name) {
	currentView = name;
	for (const view of views) $("#view-" + view).hidden = view !== name;
	if (name === "sites" && $("#dock").hidden && !$("#search").hidden) $("#search").focus({ preventScroll: true });
}

// Hash routes: #sites, #site/<name>, #files/<path>
async function route() {
	const hash = decodeURIComponent(location.hash.slice(1));
	const slash = hash.indexOf("/");
	const view = slash < 0 ? hash : hash.slice(0, slash);
	const arg = slash < 0 ? "" : hash.slice(slash + 1);
	if (editorDialog.open && !(await discardOk())) {
		history.replaceState(null, "", currentView === "site" ? siteLink($("#site-head h2").textContent) : "#sites");
		return;
	}
	if (editorDialog.open) {
		openFile = null;
		setDirty(false);
		editorDialog.close();
	}
	if (view === "files" || view === "remote-files") {
		const parts = arg.split("/");
		const remote = view === "remote-files" ? parts.shift() : "";
		history.replaceState(null, "", "#sites");
		showView("sites");
		await run(() => openFiles(view === "remote-files" ? parts.join("/") : arg, remote));
	} else if (view === "sql") {
		if (filesDialog.open) filesDialog.close();
		showView("sql");
		await run(loadSqlConnections);
	} else if (view === "site" && arg) {
		if (filesDialog.open) filesDialog.close();
		showView("site");
		renderSite(arg);
	} else {
		if (filesDialog.open) filesDialog.close();
		showView("sites");
	}
}
window.addEventListener("hashchange", route);
const go = (hash) => { location.hash = hash; };
const siteLink = (name) => "#site/" + encodeURIComponent(name);
const fsUrl = (action) => remoteFilesSite ? "/api/remote-fs/" + encodeURIComponent(remoteFilesSite) + "/" + action : (serverFiles ? "/api/server-fs/" : "/api/fs/") + action;

// ------------------------------------------------------------------ sites

let allSites = [];
// The admin site itself is reached via Settings.
const isListed = (s) => !s.admin;
const listedSites = () => allSites.filter(isListed);
let filter = "all";
let query = "";

const kindLabel = { umbraco: "Umbraco", csharp: "C# / .NET", python: "Python", html: "HTML" };

let defaultUmbracoVersion = "";
let sitesLoaded = false;
let updateCheckTimer = null;
function monitorUpdateCheck(state) {
	const button = $("#btn-check-updates");
	button.disabled = state.running;
	button.replaceChildren(svgIcon("download"), el("span", { textContent: state.running ? `Checking updates (${state.completed}/${state.total})…` : "Check for updates" }));
	if (state.running && !updateCheckTimer) {
		updateCheckTimer = setTimeout(() => {
			updateCheckTimer = null;
			run(async () => {
				let result;
				try {
					result = await api("/api/package-updates");
				} catch (error) {
					monitorUpdateCheck({ running: false });
					throw error;
				}
				monitorUpdateCheck(result);
				if (!result.running) {
					await loadSites();
					if (result.errors.length) toast("Update checks failed: " + result.errors.join(", "), "error");
					else toast(`Checked ${result.total} sites for package updates.`, "ok");
				}
			});
		}, 2000);
	}
}

async function loadSites() {
	const data = await api("/api/sites");
	allSites = data.sites;
	sitesLoaded = true;
	if (data.update_check) monitorUpdateCheck(data.update_check);
	$("#settings-admin-site").hidden = !allSites.some((s) => s.admin);
	$("#settings-admin-group").hidden = $("#settings-admin-site").hidden;
	defaultUmbracoVersion = (data.defaults || {}).umbraco_version || "";
	renderSites();
	if (currentView === "site") renderSite(decodeURIComponent(location.hash.slice(6)));
	if (currentView === "sql") await refreshSql();
}

// A site is running when any of its services is; sites without a service are static
function siteState(site) {
	const units = site.environments.filter((e) => e.unit);
	if (!units.length) return "";
	return units.some((e) => e.state === "active") ? "active" : "inactive";
}
const hasProd = (site) => site.environments.some((e) => e.key === "prod");

function renderStats() {
	const searched = overviewSites().filter(matchesQuery);
	const count = (test) => searched.filter(test).length;
	const chips = [
		["all", "All", searched.length],
		["running", "Running", count((s) => siteState(s) === "active")],
		["stopped", "Stopped", count((s) => siteState(s) === "inactive")],
		["prod", "With prod", count(hasProd)],
	];
	for (const kind of Object.keys(kindLabel)) {
		const n = count((s) => s.kind === kind);
		if (n) chips.push([kind, kindLabel[kind], n]);
	}
	if (!chips.some(([key, , n]) => key === filter && n > 0)) filter = "all";
	const box = $("#stats");
	box.replaceChildren();
	for (const [key, label, n] of chips) {
		if (!n && key !== "all") continue;
		box.append(el("div", { className: "chip" + (filter === key ? " active" : ""), onclick: () => { filter = key; renderSites(); } },
			el("b", { textContent: n }), el("span", { textContent: label })));
	}
}

function matchesQuery(site) {
	const text = query.trim().toLowerCase();
	const haystack = site.name + " " + site.environments.map((e) => e.binding + " " + (e.host || "")).join(" ");
	return !text || haystack.toLowerCase().includes(text);
}

function matches(site) {
	const state = siteState(site);
	if (filter === "running" && state !== "active") return false;
	if (filter === "stopped" && state !== "inactive") return false;
	if (filter === "prod" && !hasProd(site)) return false;
	if (kindLabel[filter] && site.kind !== filter) return false;
	return matchesQuery(site);
}


// ------------------------------------------------------------------ button icons
// Inline SVG (Lucide-style, 24x24 stroke icons); the CSP does not allow external images
const iconPaths = {
	lock: "M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4",
	unlock: "M5 11h14v10H5zM8 11V7a4 4 0 0 1 7.5-2",
	terminal: "M4 17l6-6-6-6M12 19h8",
	folder: "M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z",
	upload: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12",
	download: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3",
	sync: "M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8M21 3v5h-5M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16M8 16H3v5",
	restart: "M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8M21 3v5h-5",
	trash: "M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2",
	play: "M6 3l14 9-14 9z",
	stop: "M7 5h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z",
	file: "M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7ZM14 2v4a2 2 0 0 0 2 2h4M8 13h8M8 17h8",
	globe: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20ZM2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z",
	shield: "M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1zM9 12l2 2 4-4",
	sliders: "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M2 14h4M10 8h4M18 16h4",
	activity: "M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2",
	pencil: "M21.17 6.81a2.12 2.12 0 0 0-3-3L3.84 17.17a2 2 0 0 0-.5.83l-1.32 4.35a.5.5 0 0 0 .62.62l4.35-1.32a2 2 0 0 0 .83-.5zM15 5l4 4",
	eraser: "M7 21l-4.3-4.3c-1-1-1-2.5 0-3.4l9.6-9.6c1-1 2.5-1 3.4 0l5.6 5.6c1 1 1 2.5 0 3.4L13 21M22 21H7M5 11l9 9",
	manage: "M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z",
	external: "M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6",
	key: "M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.78 7.78 5.5 5.5 0 0 1 7.78-7.78zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4",
	server: "M4 4h16v6H4zM4 14h16v6H4zM8 7h.01M8 17h.01M12 7h5M12 17h5",
	package: "m12 3 9 5v8l-9 5-9-5V8zM3 8l9 5 9-5M12 13v8M7.5 5.5l9 5",
};

// label (exact, or a prefix ending in "*") -> [icon, tone]
const buttonStyles = {
	"Shell": ["terminal", "blue"], "Server shell": ["terminal", "blue"], "New file": ["file", "blue"], "New folder": ["folder", "blue"], "Upload": ["upload", "blue"], "Browse /": ["folder", "blue"], "Files": ["folder", "blue"], "Manage": ["manage", "blue"],
	"Publish": ["upload", "green"], "Publish both": ["upload", "green"], "Sync to*": ["sync", "blue"], "Start": ["play", "green"],
	"Check for updates": ["download", "amber"],
	"Add package": ["download", "green"], "Remove package": ["trash", "red"],
	"Add": ["download", "green"], "Remove": ["trash", "red"], "Check": ["download", "amber"],
	"Fix permissions": ["shield", "teal"], "Update Python packages": ["sync", "blue"],
	"Create production site": ["server", "purple"],
	"Check outdated": ["download", "amber"], "Restart": ["restart", "amber"], "Restart Apache": ["restart", "amber"],
	"Stop": ["stop", "red"], "Clear logs": ["eraser", "gray"], "Logs": ["file", "gray"],
	"Issue certificate": ["shield", "teal"], "Change environment": ["sliders", "purple"], "Change bindings": ["globe", "blue"],
	"Check service": ["activity", "teal"], "Test Apache config": ["activity", "teal"], "Admin site": ["manage", "gray"],
	"Edit service": ["pencil", "amber"], "Edit config": ["pencil", "amber"], "Edit appsettings.json": ["pencil", "amber"], "Edit web.config": ["pencil", "amber"], "Delete*": ["trash", "red"],
};

function svgIcon(name) {
	const ns = "http://www.w3.org/2000/svg";
	const svg = document.createElementNS(ns, "svg");
	for (const [key, value] of Object.entries({ viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round", class: "ico", "aria-hidden": "true" })) svg.setAttribute(key, value);
	const path = document.createElementNS(ns, "path");
	path.setAttribute("d", iconPaths[name]);
	svg.append(path);
	return svg;
}

function decorate(root) {
	for (const button of root.querySelectorAll("button:not([data-tone])")) {
		const label = button.textContent.trim();
		const style = buttonStyles[label] || (label.startsWith("Delete") ? buttonStyles["Delete*"] : label.startsWith("Sync to ") ? buttonStyles["Sync to*"] : null);
		if (!style) continue;
		button.dataset.tone = style[1];
		button.prepend(svgIcon(style[0]));
	}
}

let multiSelecting = false;
const multiSites = new Set();
const canMultiSelect = (site) => !site.admin && site.has_project && (site.kind === "csharp" || site.kind === "umbraco");
const overviewSites = () => listedSites().filter((site) => !multiSelecting || canMultiSelect(site));

function renderMultiSelection() {
	$("#btn-check-updates").hidden = !listedSites().some(canMultiSelect);
	$("#btn-multi").hidden = multiSelecting || !listedSites().some(canMultiSelect);
	$("#btn-multi-action").hidden = !multiSelecting;
	$("#btn-multi-action").disabled = multiSites.size === 0;
	$("#btn-multi-action").textContent = `Choose action (${multiSites.size})`;
	$("#btn-multi-cancel").hidden = !multiSelecting;
}

function renderSites() {
	renderStats();
	for (const name of multiSites) {
		if (!listedSites().some((site) => site.name === name && canMultiSelect(site))) multiSites.delete(name);
	}
	renderMultiSelection();
	const box = $("#sites");
	box.replaceChildren();
	const visible = overviewSites().filter(matches);
	$("#no-sites").hidden = visible.length > 0;
	$("#btn-new-site").hidden = visible.length === 0;
	const firstSite = listedSites().length === 0;
	$("#search").hidden = firstSite;
	$("#empty-sites-title").textContent = firstSite ? "Your first site starts here" : "Make room for your next idea";
	$("#empty-sites-description").textContent = firstSite
		? "Create a home for your next idea. Set up your first site in just a few steps."
		: "Try a different search or filter, or bring something new to life by creating a site.";
	for (const site of visible) box.append(siteCard(site));
	updateSiteActivity();
	decorate(box);
}

function updateSiteActivity() {
	for (const badge of document.querySelectorAll(".site-activity")) {
		const tasks = Object.values(terminals).filter((task) => !task.exited && task.sites.includes(badge.dataset.site)
			&& (!badge.dataset.scope || task.scope === "all" || task.scope === badge.dataset.scope));
		badge.hidden = tasks.length === 0;
		badge.title = tasks.map((task) => task.taskTitle).join("\n");
	}
}

function siteActivity(site, scope = "") {
	const activity = el("span", { className: "site-activity", hidden: true, role: "status" },
		el("span", { className: "activity-spinner", ariaHidden: "true" }), el("span", { textContent: "Working" }));
	activity.dataset.site = site.name;
	activity.dataset.scope = scope;
	return activity;
}

// Opens a URL in a new tab: a plain link, or (asButton) a small button with an "open" icon
function openLink(text, url, title, asButton) {
	const a = el("a", { className: asButton ? "btn-link" : "link", href: url, target: "_blank", rel: "noopener noreferrer", title: title || url, onclick: (event) => event.stopPropagation() });
	if (!asButton) { a.textContent = text; return a; }
	a.append(el("span", { textContent: text }), svgIcon("external"));
	return a;
}
const bindingLink = (binding, asButton) => binding
	? openLink(binding, "https://" + binding + "/", "", asButton)
	: el("span", { className: "muted", textContent: "no binding" });
const backofficeLink = (binding, asButton) => openLink(binding + "/umbraco", "https://" + binding + "/umbraco", "Umbraco backoffice", asButton);

function environmentAddress(env) {
	const hostname = env.binding || (env.mode === "remote" ? env.host : $("meta[name=server-hostname]")?.content) || "";
	const authority = hostname.includes(":") && !hostname.startsWith("[") ? `[${hostname}]` : hostname;
	return {
		hostname,
		url: hostname ? `${env.scheme || "https"}://${authority}/` : "",
		title: env.binding ? hostname : `Default / wildcard: ${hostname}. Apache chooses the site for this address.`,
	};
}

const versionColors = 8; // majors 18, 19, 20 … get different colours (see .badge.version[data-major] in style.css)

function kindBadges(site) {
	const badges = el("div", { className: "badges" }, el("span", { className: "badge " + site.kind, textContent: kindLabel[site.kind] || site.kind }));
	if (site.umbraco_version) {
		const major = parseInt(site.umbraco_version, 10);
		const badge = el("span", { className: "badge version", textContent: "v" + site.umbraco_version, title: "Umbraco " + site.umbraco_version });
		if (Number.isFinite(major)) badge.dataset.major = String(major % versionColors);
		badges.append(badge);
	}
	if (site.admin) badges.append(el("span", { className: "badge admin", textContent: "this admin site" }));
	if (site.updates === "available") badges.append(el("span", { className: "badge updates-available", textContent: "Updates available", title: "NuGet updates were found. Install package updates, then publish the site." }));
	if (site.updates === "error") badges.append(el("span", { className: "badge updates-error", textContent: "Update check failed", title: "Run Check for updates again. See server logs for details." }));
	return badges;
}

function httpsLock(secure) {
	const label = secure == null ? "HTTPS status unavailable" : secure ? "HTTPS enabled" : "HTTPS not enabled";
	return el("span", { className: "https-lock " + (secure == null ? "unknown" : secure ? "secure" : "insecure"), title: label, ariaLabel: label }, svgIcon(secure ? "lock" : "unlock"));
}

function remoteIcon(env) {
	return el("span", { className: "remote-icon", title: "Remote server: " + env.host, ariaLabel: "Remote server: " + env.host }, svgIcon("server"));
}

function environmentLabel(site, env) {
	return site.kind === "python" ? "Live" : env.environment || (site.kind === "csharp" || site.kind === "umbraco" ? "Unknown" : env.label);
}

function publishBadge(env) {
	const labels = { pending: "Publish pending", unknown: "Publish status unknown" };
	const label = labels[env.publish_status];
	return label ? el("span", { className: "badge publish-" + env.publish_status, textContent: label,
		title: env.publish_status === "pending" ? "Project or package configuration has changed since this environment's last successful publish." : "Publish this environment to establish its package baseline." }) : null;
}

function siteCard(site) {
	const state = siteState(site);
	const actions = el("div", { className: "site-links" });
	for (const env of site.environments) {
		const production = env.key === "prod";
		const environment = environmentLabel(site, env);
		const group = el("div", { className: "site-link-group" + (production ? " production" : ""), role: "group", ariaLabel: environment + " · " + (env.binding || site.name) });
		const heading = el("div", { className: "site-environment-head" },
			httpsLock(env.https),
			el("span", { className: "badge env-" + environment.toLowerCase(), textContent: environment }));
		if (env.mode === "remote") heading.append(remoteIcon(env));
		const pending = publishBadge(env);
		if (pending) heading.append(el("div", { className: "publish-status" }, pending));
		group.append(heading);
		const links = el("div", { className: "site-environment-links" });
		const address = environmentAddress(env);
		if (!address.url) {
			group.append(el("span", { className: "muted", textContent: "No binding configured" }));
			actions.append(group);
			continue;
		}
		const frontend = openLink("Frontend", address.url, environment + ": " + address.title, true);
		frontend.classList.add("frontend");
		links.append(frontend);
		if (site.kind === "umbraco") {
			const backend = openLink("Backend", address.url + "umbraco", environment + " backoffice: " + address.title, true);
			backend.classList.add("backend");
			links.append(backend);
		}
		group.append(links);
		actions.append(group);
	}

	const badges = kindBadges(site);
	badges.append(siteActivity(site));
	const selection = multiSelecting && canMultiSelect(site)
		? el("label", { className: "multi-select", onclick: (event) => event.stopPropagation() },
			el("input", { type: "checkbox", checked: multiSites.has(site.name), ariaLabel: "Select " + site.name,
				onchange: (event) => {
					if (event.target.checked) multiSites.add(site.name); else multiSites.delete(site.name);
					renderMultiSelection();
				} }), el("span", { textContent: "Select site" }))
		: null;
	return el("article", { className: "site " + state + (hasProd(site) ? " with-production" : "") + (site.admin ? " is-admin" : ""), onclick: () => go(siteLink(site.name)) },
		...(selection ? [selection] : []),
		el("div", { className: "site-head" },
			el("span", { className: "site-emblem" }, svgIcon("globe")),
			el("div", { className: "site-identity" }, el("h3", { className: "site-title", textContent: site.name }), badges),
			el("span", { className: "dot " + state, title: state || "Static site" })),
		actions);
}

const scriptNames = {
	"publish.sh": "Publish", "publish_local.sh": "Publish (test)", "publish_prod.sh": "Publish to production",
	"sync_prod.sh": "Sync content to production", "clear_logs.sh": "Clear logs", "clear_logs_local.sh": "Clear logs (test)",
	"clear_logs_prod.sh": "Clear logs (production)", "create_prod.sh": "Create production site", "change_env.sh": "Change environment (Test / Production)",
	"outdated.sh": "Check outdated packages", "edit_conf.sh": "Edit Apache config", "issue_certificat.sh": "Issue certificate",
	"restart_apache.sh": "Restart Apache", "set_password.sh": "Change admin password", "fix_permissions.sh": "Fix permissions",
	"update_packages.sh": "Update Python packages",
};
const scriptLabel = (file) => scriptNames[file] || file.replace(/\.sh$/, "").replace(/[_-]+/g, " ").replace(/^./, (c) => c.toUpperCase());

function scriptSelect(site, className) {
	const select = el("select", { className: className || "", title: "Run a script from the site's scripts folder" }, el("option", { textContent: "Run script…", value: "" }));
	const groups = [["Production", (f) => f.includes("_prod")], ["Site", (f) => !f.includes("_prod")]];
	for (const [label, test] of groups) {
		const files = site.scripts.filter((f) => test(f) && !buttonScripts.has(f));
		if (!files.length) continue;
		const group = el("optgroup", { label });
		for (const file of files) group.append(el("option", { textContent: scriptLabel(file), value: file, title: file }));
		select.append(group);
	}
	select.onchange = () => {
		const script = select.value;
		select.value = "";
		if (script) run(() => openTask({ kind: "script", site: site.name, script }));
	};
	return select;
}

function renderSite(name) {
	const site = allSites.find((s) => s.name === name);
	const head = $("#site-head");
	const envs = $("#envs");
	const packagesPanel = $("#site-packages");
	head.replaceChildren();
	envs.replaceChildren();
	packagesPanel.replaceChildren();
	packagesPanel.hidden = true;
	if (!site && sitesLoaded) { go("#sites"); return; }
	if (!site) {
		head.append(el("p", { className: "empty", textContent: allSites.length ? "Site not found." : "Loading…" }));
		return;
	}
	const actions = el("div", { className: "actions" });
	const add = (label, handler, className) => actions.append(el("button", { textContent: label, className: className || "", onclick: () => run(handler) }));
	if (site.scripts.some((f) => !buttonScripts.has(f))) actions.append(scriptSelect(site));
	if (hasProd(site) && site.scripts.includes("publish_prod.sh") && ["publish_local.sh", "publish.sh"].some((file) => site.scripts.includes(file))) {
		add("Publish both", async () => {
			if (await ask({ title: "Publish both?", message: "Publish the test site first, then production (local or remote). Both services are restarted. If test publishing fails, production is not published.", input: false, ok: "Publish both" })) {
				await openTask({ kind: "publish_both", site: site.name });
			}
		});
	}
	if (site.kind !== "csharp" && site.kind !== "umbraco" && site.scripts.includes("outdated.sh")) {
		actions.append(el("button", { textContent: "Check outdated", title: "outdated.sh", onclick: () => run(() => openTask({ kind: "script", site: site.name, script: "outdated.sh" })) }));
	}
	if (site.kind === "python") {
		for (const [script, label] of [["update_packages.sh", "Update Python packages"]]) {
			if (site.scripts.includes(script)) {
				add(label, () => openTask({ kind: "script", site: site.name, script }));
			}
		}
	}
	if (hasProd(site) && site.scripts.includes("sync_prod.sh")) {
		const target = site.environments.find((env) => env.key === "prod");
		const label = "Sync to " + (target.environment || "target");
		actions.append(el("button", { textContent: label, title: "sync_prod.sh", onclick: () => run(async () => {
			if (await ask({ title: label + "?", message: `Sync content to ${target.binding || target.host || site.name}.`, input: false, ok: label })) await openTask({ kind: "script", site: site.name, script: "sync_prod.sh" });
		}) }));
	}
	if (!site.admin && hasProd(site)) add("Delete everything", () => deleteSite(site, "all"), "danger");
	head.append(el("div", { className: "detail-title" }, el("h2", { textContent: site.name }), kindBadges(site)), actions);
	for (const env of site.environments) envs.append(envPanel(site, env));
	if (site.has_project && (site.kind === "csharp" || site.kind === "umbraco")) {
		packagesPanel.hidden = false;
		const updates = new Map((site.package_updates || []).map((item) => [item.name.toLowerCase(), item]));
		const packages = (site.packages || []).filter((item) => item.name.toLowerCase() !== "microsoft.icu.icu4c.runtime" || updates.has(item.name.toLowerCase()));
		const packageActions = el("div", { className: "actions" },
			el("button", { textContent: "Add", title: "Add package", onclick: () => run(() => openPackageDialog(site, "add")) }),
			el("button", { textContent: "Remove", title: "Remove package", onclick: () => run(() => openPackageDialog(site, "remove")) }));
		packageActions.append(el("button", { textContent: "Check", title: "Check outdated packages", onclick: () => run(() => openTask({ kind: "bulk", sites: [site.name], action: "check" })) }));
		if (updates.size > 1) {
			packageActions.append(el("button", { textContent: "Update all", className: "primary", onclick: () => run(() => updatePackages(site)) }));
		}
		const packageList = el("div", { className: "package-list" });
		for (const item of packages) {
			const update = updates.get(item.name.toLowerCase());
			const nameButton = el("button", {
				type: "button", className: "package-copy", title: `Copy ${item.name}`,
				onclick: () => run(async () => {
					if (!navigator.clipboard) throw new Error("Clipboard access requires HTTPS or localhost.");
					await navigator.clipboard.writeText(item.name);
					toast(`Copied ${item.name}`, "ok");
				}),
			}, el("span", { className: "package-name" }, svgIcon("package"), el("span", { textContent: item.name })));
			const versions = el("div", { className: "package-versions" },
				el("span", { className: "package-version", textContent: update ? update.current : item.version || "Managed separately" }));
			if (update) versions.append(
				el("span", { className: "muted", textContent: "→", ariaLabel: "Update available to" }),
				el("span", { className: "package-version package-latest", textContent: update.latest }),
				el("button", { className: "small primary", textContent: "Update", title: `Update ${item.name} to ${update.latest}`,
					onclick: () => run(() => updatePackages(site, update)) }));
			packageList.append(el("div", { className: "package-row" + (update ? " has-update" : "") }, nameButton, versions));
		}
		if (!packageList.children.length && !(site.package_errors || []).length) {
			packageList.append(el("p", { className: "muted", textContent: "No packages to display." }));
		}
		packagesPanel.append(
			el("div", { className: "package-head" },
				el("div", { className: "package-heading" }, el("h3", { textContent: "NuGet packages" }), el("span", { className: "package-count", textContent: String(packages.length), title: "Visible package references" })),
				packageActions),
			...(site.package_errors || []).map((message) => el("p", { className: "package-error", textContent: `Could not read package references: ${message}` })),
			...(site.updates === "error" ? [el("p", { className: "package-error", textContent: "Package update check failed. Run Check to retry." })] : []),
			...(site.updates === "available" && !updates.size ? [el("p", { className: "muted", textContent: "Run Check to load available package versions." })] : []),
			packageList);
	}
	decorate(head);
	decorate(envs);
	updateSiteActivity();
	decorate(packagesPanel);
}

async function updatePackages(site, update) {
	const items = update ? [update] : site.package_updates;
	if (!(await ask({
		title: update ? "Update package" : "Update all packages",
		message: items.map((item) => `${item.name}: ${item.current} → ${item.latest}`).join("\n")
			+ "\nMajor upgrades may require code changes. This updates the project; publish separately.",
		input: false, ok: update ? "Update" : "Update all",
	}))) return;
	await openTask({ kind: "update_package", site: site.name, ...(update ? { package: update.name } : {}) });
}

// Scripts that have their own button and are therefore left out of the "Run script…" list
const buttonScripts = new Set(["publish.sh", "publish_local.sh", "publish_prod.sh", "create_prod.sh", "sync_prod.sh", "clear_logs.sh", "clear_logs_local.sh", "clear_logs_prod.sh", "issue_certificat.sh", "change_env.sh", "restart_service.sh", "check_service.sh", "edit_service.sh", "edit_conf.sh", "restart_apache.sh", "outdated.sh", "set_password.sh", "fix_permissions.sh", "update_packages.sh"]);

// [script candidates or null, button label, confirmation text (null = none; the script asks itself), prod_action name]
function quickActions(env) {
	if (env.key === "prod") {
		return [
			[["publish_prod.sh"], "Publish", "Build and publish this site to production. The service is restarted."],
			[["clear_logs_prod.sh"], "Clear logs", "Delete the production logs."],
			[null, "Issue certificate", "Issue a new certificate for the production binding.", "issue_certificate"],
			[null, "Change environment", null, "change_env"],
		];
	}
	return [
		[["publish_local.sh", "publish.sh"], "Publish", "Build and publish the test site. The service is restarted."],
		[["clear_logs_local.sh", "clear_logs.sh"], "Clear logs", "Delete the test site logs."],
		[null, "Issue certificate", "Issue a new certificate for the test site binding.", "issue_certificate"],
		[null, "Change environment", null, "change_env"],
	];
}

function envPanel(site, env) {
	const dot = env.unit ? env.state || "unknown" : "";
	const heading = el("div", { className: "env-head" },
		siteActivity(site, env.key === "prod" ? "prod" : "main"),
		el("span", { className: "dot " + dot }),
		el("span", { className: "badge " + (env.unit ? "state-" + (env.state || "unknown") : ""), textContent: env.unit ? env.state || "unknown" : env.mode === "remote" ? "remote" : "static" }));
	const environment = site.kind === "python" ? "Live" : env.environment;
	if (environment) heading.append(el("span", { className: "badge env-" + environment.toLowerCase(), textContent: environment }));
	heading.append(httpsLock(env.https));
	if (env.mode === "remote") heading.append(remoteIcon(env));
	if (env.key !== "prod" && !hasProd(site) && site.scripts.includes("create_prod.sh")) {
		heading.append(el("button", { textContent: "Create production site", className: "small create-production",
			onclick: () => run(() => openCreateProdDialog(site)) }));
	}
	const pending = publishBadge(env);
	if (pending) heading.append(el("div", { className: "publish-status" }, pending));

	const scheme = env.scheme || "https";
	const address = environmentAddress(env);
	const binding = !address.url ? bindingLink("") : site.admin && env.key !== "prod"
		? el("span", { textContent: env.binding })
		: openLink(env.binding || address.hostname, address.url, address.title);
	const meta = el("dl", { className: "meta" }, el("dt", { textContent: "Binding" }), el("dd", {}, binding));
	if (env.aliases && env.aliases.length) {
		meta.append(el("dt", { textContent: "Aliases" }), el("dd", { className: "database-name aliases" },
			...env.aliases.flatMap((alias, index) => [
				...(index ? [", "] : []),
				openLink(alias, scheme + "://" + alias + "/"),
			])));
	}
	if (site.kind === "umbraco" && address.url) meta.append(el("dt", { textContent: "Backoffice" }), el("dd", {}, openLink(address.hostname + "/umbraco", address.url + "umbraco", address.title)));
	if (site.kind === "csharp" || site.kind === "umbraco") {
		meta.append(el("dt", { textContent: "Datasource" }), el("dd", {
			className: "database-name",
			textContent: (env.database || "Unknown") + (env.database_server ? ` (${env.database_server})` : ""),
			title: "Inferred from database configuration; connection and server edition are not tested.",
		}));
		if (env.database_name) meta.append(el("dt", { textContent: "Database" }), el("dd", {
			className: "database-name", textContent: env.database_name,
		}));
	}
	if (env.port) meta.append(el("dt", { textContent: "Port" }), el("dd", { textContent: env.port }));
	if (env.mode === "remote") {
		meta.append(el("dt", { textContent: "Server" }), el("dd", { textContent: `${env.user || "?"}@${env.host || "?"}:${env.ssh_port || 22}` }));
		meta.append(el("dt", { textContent: "Folder" }), el("dd", { textContent: env.folder ? "/var/www/" + env.folder : "—" }));
	} else {
		meta.append(el("dt", { textContent: "Folder" }), el("dd", { textContent: "/var/www/" + env.path }));
		if (env.unit) meta.append(el("dt", { textContent: "Service" }), el("dd", { textContent: env.unit }));
	}

	const actions = el("div", { className: "actions" });
	const add = (label, handler, className) => actions.append(el("button", { textContent: label, className: "small " + (className || ""), onclick: () => run(handler) }));
	if (env.unit || env.mode === "remote") {
		if (env.unit !== site.name || !site.admin) add(env.state === "active" ? "Stop" : "Start", () => serviceAction(site, env, env.state === "active" ? "stop" : "start"));
		add("Restart", () => serviceAction(site, env, "restart"));
		add("Logs", () => showLogs(site, env));
	}
	if (env.mode === "remote") {
		add("Shell", () => openTask({ kind: "remote_tool", site: site.name, tool: "shell" }));
		add("Files", () => openFiles("", site.name));
	} else {
		add("Shell", () => openTask({ kind: "shell", site: site.name, path: env.path, scope: env.key === "prod" ? "prod" : "main" }));
		add("Files", () => openFiles(env.path));
	}

	const quick = el("div", { className: "actions quick" });
	const settings = el("div", { className: "actions quick" });
	settings.setAttribute("role", "group");
	settings.setAttribute("aria-label", "Environment settings");
	if (!site.admin) {
		settings.append(el("button", { textContent: "Fix permissions", className: "small", onclick: () => run(async () => {
			if (await ask({ title: "Fix permissions?", message: "Restore www-data ownership and content access for this environment. Scripts and symbolic links are skipped. No service restart is needed.", input: false, ok: "Fix permissions" })) {
				await openTask({ kind: "fix_permissions", site: site.name, scope: env.key === "prod" ? "prod" : "test" });
			}
		}) }));
	}
	if (!hasProd(site) || !site.admin) {
		settings.append(el("button", { textContent: "Change bindings", className: "small", onclick: () => run(() => changeBinding(site, env.key === "prod" ? "prod" : "test", env)) }));
	}
	let extraTools = null;
	for (const [files, label, confirm, action] of quickActions(env)) {
		let file = files && files.find((f) => site.scripts.includes(f));
		if (!file && label === "Publish" && env.key !== "prod") file = site.scripts.find((f) => /^publish.*\.sh$/.test(f) && f !== "publish_prod.sh");
		if (!action && !file) continue;
		if (action && env.mode === "remote" && !env.host) continue;
		if (action && env.key !== "prod" && (!env.unit || site.admin) && action === "change_env") continue;
		(action ? settings : quick).append(el("button", { textContent: label, className: "small", title: file || action, onclick: () => run(async () => {
			if (action === "change_env") { openEnvironmentDialog(site, env); return; }
			if (confirm && !(await ask({ title: `${label}?`, message: `${confirm} (${site.name})`, input: false, ok: label }))) return;
			await openTask(action ? { kind: "prod_action", site: site.name, action, scope: env.key === "prod" ? "prod" : "test" } : { kind: "script", site: site.name, script: file });
		}) }));
	}
	if (env.unit || env.mode === "remote" || env.apache) {
		const tools = el("div", { className: "actions quick" });
		for (const [tool, label] of [["check_service", "Check service"], ["edit_service", "Edit service"], ["edit_conf", "Edit config"]]) {
			if (tool !== "edit_conf" && !env.unit && env.mode !== "remote") continue;
			tools.append(el("button", { textContent: label, className: "small", onclick: () => run(() => tool === "check_service"
				? openTask({ kind: env.mode === "remote" ? "remote_tool" : "unit_tool", site: site.name, unit: env.unit, tool, scope: env.key === "prod" ? "prod" : "main" })
				: editConfiguration(site, env, tool === "edit_service" ? "service" : "conf")) }));
		}
		if (site.kind === "csharp" || site.kind === "umbraco") {
			for (const filename of ["appsettings.json", "web.config"]) {
				if (env.mode !== "remote" && !(env.editable_configs || []).includes(filename)) continue;
				tools.append(el("button", { textContent: "Edit " + filename, className: "small", onclick: () => run(() =>
					editFile(env.mode === "remote" ? filename : join(env.publish_path, filename), env.mode === "remote" ? site.name : "")) }));
			}
		}
		extraTools = tools;
	}
	if (hasProd(site) && !site.admin) add(`Delete ${environmentLabel(site, env)} site`, () => deleteSite(site, env.key === "prod" ? "prod" : "test"), "danger");
	if (!hasProd(site) && !site.admin) add("Delete site", () => deleteSite(site, "all"), "danger");

	const panel = el("section", { className: "env " + (env.key === "prod" ? "prod" : "") }, heading, meta);
	if (env.exists === false) panel.append(el("p", { className: "muted", textContent: "Nothing published here yet." }));
	if (env.remote_error) panel.append(el("p", { className: "package-error", textContent: "Remote status unavailable: " + env.remote_error }));
	if (quick.children.length) panel.append(quick);
	if (settings.children.length) panel.append(settings);
	if (extraTools) panel.append(extraTools);
	panel.append(actions);
	return panel;
}

async function serviceAction(site, env, action) {
	const unit = env.unit || env.folder;
	if (action !== "start" && !(await ask({ title: `${action === "stop" ? "Stop" : "Restart"} ${unit}?`, message: env.binding || site.name, input: false, ok: action === "stop" ? "Stop" : "Restart", danger: action === "stop" }))) return;
	await api(`/api/sites/${encodeURIComponent(site.name)}/service`, { json: { action, unit: env.unit, scope: env.mode === "remote" ? "prod" : "local" } });
	toast(`${unit}: ${action} done`, "ok");
	await loadSites();
}

const logsDialog = $("#dlg-logs");
let logsTarget = null;
async function showLogs(site, env) {
	logsTarget = { site, env };
	$("#logs-title").textContent = `Logs · ${env.unit || env.folder}`;
	if (!logsDialog.open) logsDialog.showModal();
	await refreshLogs();
}
async function refreshLogs() {
	$("#logs-text").textContent = "Loading…";
	const data = await api(`/api/sites/${encodeURIComponent(logsTarget.site.name)}/logs?unit=${encodeURIComponent(logsTarget.env.unit)}&scope=${logsTarget.env.mode === "remote" ? "prod" : "local"}`);
	const box = $("#logs-text");
	box.textContent = data.text || "(no log lines)";
	box.scrollTop = box.scrollHeight;
}
$("#logs-refresh").onclick = () => run(refreshLogs);
$("#logs-close").onclick = () => logsDialog.close();

async function changeBinding(site, scope, env) {
	const current = env ? env.binding : site.binding;
	const label = scope === "prod" ? "production site" : "test site";
	const bindings = await ask({
		title: `Change ${label} bindings`,
		message: `Set the primary domain and aliases for the ${label} of ${site.name}. Aliases must resolve to the same server. HTTP-only sites remain HTTP-only; certificates are updated only for HTTPS sites.`,
		value: current,
		extra: { label: "Aliases (one per line)", value: (env?.aliases || []).join("\n"), placeholder: "www.example.com\napi.example.com" },
		ok: "Change",
	});
	if (!bindings) return;
	const binding = bindings.value.trim();
	const aliases = bindings.extra.trim();
	const currentAliases = (env?.aliases || []).join(" ").toLowerCase();
	if (binding !== current || aliases.replace(/,/g, " ").trim().replace(/\s+/g, " ").toLowerCase() !== currentAliases) {
		await openTask({ kind: "change_binding", site: site.name, binding, aliases, scope });
	}
}

async function deleteSite(site, scope) {
	const prod = site.environments.find((e) => e.key === "prod");
	const main = site.environments.find((e) => e.key === "main");
	const mainEnvironment = environmentLabel(site, main);
	const prodEnvironment = prod ? environmentLabel(site, prod) : "";
	const remote = prod && prod.mode === "remote";
	const target = scope === "prod" ? (prod.binding || prod.unit || prod.folder || site.name) : site.name;
	const what = {
		all: remote
			? `This deletes the site and its remote ${prodEnvironment} site on ${prod.host}: files, services, Apache configs and logs on both servers.`
			: prod
				? `This deletes the site and its ${prodEnvironment} site on this server: files, services, Apache configs and logs.`
				: `This deletes the site: its files, service, Apache config and logs.`,
		test: `This deletes the ${mainEnvironment} site's files, service and Apache config. The ${prodEnvironment} site is kept.`,
		prod: remote
			? `This deletes the ${prodEnvironment} site on ${prod.host}: its files, service, Apache config and logs. The ${mainEnvironment} site is kept.`
			: `This deletes the ${prodEnvironment} site: its files, service, Apache config and logs. The ${mainEnvironment} site is kept.`,
	}[scope];
	const typed = await ask({
		title: scope === "all" ? "Delete everything" : `Delete ${scope === "prod" ? prodEnvironment : mainEnvironment} site`, danger: true, ok: "Delete", match: target,
		message: `${what}\nType "${target}" to confirm.`,
	});
	if (typed === null) return;
	let certificates = false;
	const certificateMessage = remote && scope !== "test"
		? "Also revoke and remove the certificate(s) for the site being deleted? The remote certificate is kept if another enabled site uses the domain."
		: "Also revoke and remove the certificate(s) for the site being deleted?";
	const deletedEnvironments = site.environments.filter((env) => scope === "all" || (scope === "prod" ? env.key === "prod" : env.key !== "prod"));
	if (deletedEnvironments.some((env) => env.https !== false)) {
		certificates = (await ask({ title: "Certificate", message: certificateMessage, input: false, ok: "Remove certificate(s)" })) === true;
	}
	await openTask({ kind: "delete_site", site: site.name, certificates, scope });
}

$("#search").oninput = (event) => {
	query = event.target.value;
	if (currentView !== "sites") go("sites");
	renderSites();
};
$("#btn-server-shell").onclick = () => run(() => openTask({ kind: "shell" }));
$("#btn-browse").onclick = () => run(() => openFiles("", "", true));
$("#btn-check-updates").onclick = () => run(async () => {
	$("#btn-check-updates").disabled = true;
	try {
		monitorUpdateCheck(await api("/api/package-updates", { method: "POST" }));
	} catch (error) {
		$("#btn-check-updates").disabled = false;
		throw error;
	}
});
document.addEventListener("keydown", (event) => {
	const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
	if (event.key === "/" && !typing && !$("#search").hidden) { event.preventDefault(); if (currentView !== "sites") go("sites"); $("#search").focus(); }
	if (event.key === "`" && event.ctrlKey) { event.preventDefault(); toggleDock(); }
	if (event.key === "Escape") $("#user-menu").hidden = true;
});
setInterval(() => {
	const busy = document.activeElement && $("#view-sites").contains(document.activeElement) && document.activeElement.tagName !== "BODY";
	if (!document.hidden && !busy && !document.querySelector("dialog[open]")) loadSites().catch(() => {});
}, 15000);

// ------------------------------------------------------------------ account

$("#btn-user").onclick = (event) => { event.stopPropagation(); $("#user-menu").hidden = !$("#user-menu").hidden; };
document.addEventListener("click", (event) => { if (!$("#user-menu").contains(event.target)) $("#user-menu").hidden = true; });
const profileDialog = $("#dlg-profile");
const profileForm = $("#form-profile");
$("#menu-profile").onclick = () => {
	$("#user-menu").hidden = true;
	profileForm.reset();
	profileForm.username.value = $("#menu-user").textContent;
	profileDialog.showModal();
	profileForm.current_password.focus();
};
$("#profile-cancel").onclick = () => profileDialog.close();
profileForm.onsubmit = (event) => {
	event.preventDefault();
	const data = Object.fromEntries(new FormData(profileForm));
	run(async () => {
		if (data.new_password !== data.repeat) throw new Error("The new passwords do not match.");
		delete data.repeat;
		const result = await api("/api/profile", { json: data });
		$("#menu-user").textContent = result.user;
		$("#btn-user").textContent = result.user.slice(0, 1).toUpperCase();
		profileDialog.close();
		toast("Profile saved", "ok");
	});
};

const dialog = $("#dlg-site");
const siteForm = $("#form-site");
function syncSiteForm() {
	const csharp = siteForm.type.value === "csharp";
	const umbraco = siteForm.csharp.value === "umbraco";
	$("#csharp-options").hidden = !csharp;
	$("#umbraco-version-option").hidden = !umbraco;
	$("#template-name-option").hidden = umbraco;
	siteForm.version.disabled = !csharp || !umbraco;
	siteForm.elements.template.disabled = !csharp || umbraco;
}
siteForm.addEventListener("change", (event) => { if (["type", "csharp"].includes(event.target.name)) syncSiteForm(); });
siteForm.site.oninput = () => { if (!siteForm.binding.dataset.edited) siteForm.binding.value = siteForm.site.value; };
siteForm.binding.oninput = () => { siteForm.binding.dataset.edited = "1"; };
$("#btn-new-site").onclick = () => { siteForm.reset(); siteForm.version.value = defaultUmbracoVersion; delete siteForm.binding.dataset.edited; syncSiteForm(); dialog.showModal(); siteForm.site.focus(); };
$("#btn-empty-new-site").onclick = () => $("#btn-new-site").click();
$("#site-cancel").onclick = (e) => { e.preventDefault(); dialog.close(); };
siteForm.onsubmit = () => {
	const data = Object.fromEntries(new FormData(siteForm));
	data.kind = "create_site";
	run(() => openTask(data));
};

const prodDialog = $("#dlg-prod");
const prodForm = $("#form-prod");
function syncProdForm() {
	const target = prodForm.elements.target.value;
	const local = target === "local";
	$("#prod-local-options").hidden = !local;
	$("#prod-remote-options").hidden = local;
	prodForm.local_path.required = local;
	prodForm.host.required = !local;
	prodForm.user.required = !local;
	prodForm.ssh_port.required = !local;
	const previous = prodForm.dataset.target;
	if (previous && previous !== target) {
		const previousDefaults = previous === "local"
			? { folder: prodForm.dataset.localFolder, binding: prodForm.dataset.localBinding }
			: { folder: prodForm.dataset.remoteFolder, binding: prodForm.dataset.remoteBinding };
		const nextDefaults = local
			? { folder: prodForm.dataset.localFolder, binding: prodForm.dataset.localBinding }
			: { folder: prodForm.dataset.remoteFolder, binding: prodForm.dataset.remoteBinding };
		if (prodForm.folder.value === previousDefaults.folder) prodForm.folder.value = nextDefaults.folder;
		if (!prodForm.binding.dataset.edited && prodForm.binding.value === previousDefaults.binding) prodForm.binding.value = nextDefaults.binding;
	}
	prodForm.dataset.target = target;
}
function openCreateProdDialog(site) {
	prodForm.reset();
	delete prodForm.binding.dataset.edited;
	const binding = site.environments.find((env) => env.key === "test")?.binding || site.name;
	prodForm.folder.value = site.name;
	prodForm.binding.value = binding;
	prodForm.local_path.value = `/var/www/${site.name}/_prod`;
	prodForm.dataset.remoteFolder = site.name;
	prodForm.dataset.remoteBinding = binding;
	prodForm.dataset.localFolder = `prod-${site.name}`;
	prodForm.dataset.localBinding = `prod-${site.name}`;
	prodForm.elements.target.value = "remote";
	delete prodForm.dataset.target;
	syncProdForm();
	prodForm.dataset.site = site.name;
	prodDialog.showModal();
	prodForm.elements.target.focus();
}
prodForm.elements.target.onchange = syncProdForm;
prodForm.folder.oninput = () => {
	if (!prodForm.binding.dataset.edited) prodForm.binding.value = prodForm.folder.value;
};
prodForm.binding.oninput = () => { prodForm.binding.dataset.edited = "1"; };
$("#prod-cancel").onclick = () => prodDialog.close();
prodForm.onsubmit = (event) => {
	event.preventDefault();
	const data = Object.fromEntries(new FormData(prodForm));
	data.kind = "create_prod";
	data.site = prodForm.dataset.site;
	run(async () => {
		await openTask(data);
		prodForm.reset();
		prodDialog.close();
	});
};

const environmentDialog = $("#dlg-environment");
const environmentForm = $("#form-environment");
function openEnvironmentDialog(site, env) {
	environmentForm.reset();
	environmentForm.dataset.site = site.name;
	environmentForm.dataset.scope = env.key === "prod" ? "prod" : "test";
	const choices = [...environmentForm.querySelectorAll("input[name=environment]")];
	const selected = choices.find((input) => input.value.toLowerCase() === (env.environment || "").toLowerCase());
	if (selected) selected.checked = true;
	$("#environment-site").textContent = env.binding || site.name;
	$("#environment-current").textContent = env.environment || "Unknown";
	$("#environment-current").className = "badge env-" + (env.environment || "unknown").toLowerCase();
	environmentDialog.showModal();
	(selected || choices[0]).focus();
}
$("#environment-cancel").onclick = () => environmentDialog.close();
environmentForm.onsubmit = (event) => {
	event.preventDefault();
	const data = {
		kind: "prod_action", action: "change_env",
		site: environmentForm.dataset.site, scope: environmentForm.dataset.scope,
		environment: environmentForm.elements.environment.value,
	};
	run(async () => {
		await openTask(data);
		environmentDialog.close();
	});
};

const multiDialog = $("#dlg-multi");
const multiForm = $("#form-multi");
$("#btn-multi").onclick = () => { multiSelecting = true; renderSites(); };
$("#btn-multi-cancel").onclick = () => { multiSelecting = false; multiSites.clear(); renderSites(); };
function syncMultiAction() {
	const action = multiForm.elements.namedItem("action").value;
	$("#multi-action-description").textContent = {
		check: "Run dotnet list package --outdated for every selected project without changing packages.",
		add: "Add a NuGet package to every selected project.",
		remove: "Remove a NuGet package from every selected project.",
		local: "Build and restart each selected site's local environment.",
		prod: "Build and deploy each selected site's production environment.",
		both: "Publish local, then production for each site. Sites run in parallel.",
		outdated: "Run dotnet outdated --upgrade for every selected project. Review changes and publish afterward.",
	}[action] || "";
	const packageAction = action === "add" || action === "remove";
	$("#multi-package-fields").hidden = !packageAction;
	multiForm.elements.namedItem("package").disabled = !packageAction;
	$("#multi-version-row").hidden = action !== "add";
	multiForm.elements.namedItem("version").disabled = action !== "add";
	const selected = listedSites().filter((site) => multiSites.has(site.name));
	const skipped = action === "prod" || action === "both"
		? selected.filter((site) => !site.scripts.includes("publish_prod.sh")) : [];
	$("#multi-skipped").textContent = skipped.length
		? "Will be skipped (no production): " + skipped.map((site) => site.name).join(", ") : "";
	$("#multi-submit").disabled = !action || submittingMulti || !selected.length || skipped.length === selected.length;
}
$("#btn-multi-action").onclick = () => {
	multiForm.reset();
	$("#multi-selection").replaceChildren(...Array.from(multiSites, (name) =>
		el("span", { className: "multi-site-tag", role: "listitem" }, svgIcon("globe"), el("span", { textContent: name }))));
	syncMultiAction();
	multiDialog.showModal();
};
multiForm.elements.namedItem("action").onchange = syncMultiAction;
$("#multi-close").onclick = () => multiDialog.close();
let submittingMulti = false;
multiForm.onsubmit = (event) => {
	event.preventDefault();
	if (submittingMulti) return;
	const payload = Object.fromEntries(new FormData(multiForm));
	payload.kind = "bulk";
	payload.sites = Array.from(multiSites);
	submittingMulti = true;
	$("#multi-submit").disabled = true;
	run(async () => {
		try {
			await openTask(payload);
			multiDialog.close();
			multiSelecting = false;
			multiSites.clear();
			renderSites();
		} finally {
			submittingMulti = false;
			syncMultiAction();
		}
	});
};

const packageDialog = $("#dlg-package");
const packageForm = $("#form-package");
function openPackageDialog(site, action) {
	packageForm.reset();
	packageForm.dataset.site = site.name;
	packageForm.dataset.action = action;
	const adding = action === "add";
	$("#package-title").textContent = adding ? "Add package" : "Remove package";
	$("#package-description").textContent = adding
		? "Add a NuGet package to this site's project."
		: "Remove a NuGet package from this site's project.";
	$("#package-version-row").hidden = !adding;
	$("#package-submit").textContent = adding ? "Add package" : "Remove package";
	packageDialog.showModal();
	packageForm.elements.namedItem("package").focus();
}
$("#package-cancel").onclick = () => packageDialog.close();
packageForm.onsubmit = (event) => {
	event.preventDefault();
	const data = Object.fromEntries(new FormData(packageForm));
	data.kind = "dotnet_package";
	data.site = packageForm.dataset.site;
	data.action = packageForm.dataset.action;
	run(async () => {
		await openTask(data);
		packageDialog.close();
	});
};

// ------------------------------------------------------------------ files

var currentPath = "";
const editorDialog = $("#dlg-editor");
const filesDialog = $("#dlg-files");
$("#files-close").onclick = () => filesDialog.close();
filesDialog.addEventListener("cancel", (event) => {
	if (event.target !== filesDialog) return;
	event.preventDefault();
	filesDialog.close();
});
let openFile = null;
let configurationEditor = null;
let savingFile = false;
let dirty = false;

const sizeText = (n) => (n < 1024 ? n + " B" : n < 1048576 ? Math.round(n / 1024) + " KB" : (n / 1048576).toFixed(1) + " MB");
const join = (a, b) => (a ? a + "/" + b : b);
const parentOf = (path) => path.split("/").slice(0, -1).join("/");

function setDirty(value) {
	dirty = value;
	$("#editor-dirty").hidden = !value;
}
window.addEventListener("beforeunload", (event) => { if (dirty) event.preventDefault(); });

async function discardOk() {
	return !dirty || (await ask({ title: "Unsaved changes", message: `${openFile} has unsaved changes.`, input: false, ok: "Discard", danger: true })) === true;
}

let hideNonEditableFiles = true;
let listedFiles = [];
$("#files-filter").onclick = () => {
	hideNonEditableFiles = !hideNonEditableFiles;
	$("#files-filter").textContent = hideNonEditableFiles ? "Show all files" : "Hide non-editable files";
	$("#files-filter").setAttribute("aria-pressed", String(hideNonEditableFiles));
	renderFiles();
};

async function loadFiles(path) {
	const data = await api(fsUrl("list") + "?path=" + encodeURIComponent(path));
	currentPath = data.path;
	const crumbs = $("#crumbs");
	crumbs.replaceChildren(el("a", { className: "link", textContent: remoteFilesSite ? "Remote production · " + remoteFilesSite : serverFiles ? "/" : "/var/www", onclick: () => run(() => loadFiles("")) }));
	let accumulated = "";
	for (const part of currentPath ? currentPath.split("/") : []) {
		accumulated = join(accumulated, part);
		const target = accumulated;
		crumbs.append(" / ", el("a", { className: "link", textContent: part, onclick: () => run(() => loadFiles(target)) }));
	}
	listedFiles = data.items;
	renderFiles();
}

function renderFiles() {
	const items = listedFiles.filter((item) => !hideNonEditableFiles || (item.dir ? item.has_editable_files : item.editable));
	const body = $("#files tbody");
	body.replaceChildren();
	if (currentPath) {
		body.append(el("tr", { onclick: () => run(() => loadFiles(parentOf(currentPath))), style: "cursor:pointer" }, el("td", { textContent: "↰ .." }), el("td"), el("td"), el("td")));
	}
	for (const item of items) {
		const full = join(currentPath, item.name);
		const canOpen = item.dir || item.editable;
		const link = el(canOpen ? "a" : "span", {
			className: canOpen ? "link" : "file-uneditable",
			textContent: (item.dir ? "📁 " : "📄 ") + item.name,
			title: canOpen ? item.name : "Not editable. Download this file instead.",
			...(canOpen ? { onclick: () => run(() => (item.dir ? loadFiles(full) : editFile(full))) } : {}),
		});
		const tools = el("td", { className: "tools" });
		if (!item.dir) tools.append(el("a", { className: "link", textContent: "Download", href: fsUrl("download") + "?path=" + encodeURIComponent(full) }));
		tools.append(el("a", { className: "link", textContent: "Rename", onclick: () => run(() => renameItem(full, item.name)) }));
		tools.append(el("a", { className: "link", textContent: "Delete", onclick: () => run(() => deleteItem(full, item.name)) }));
		body.append(el("tr", {}, el("td", {}, link), el("td", { className: "num muted", textContent: item.dir ? "" : sizeText(item.size) }), el("td", { className: "muted", textContent: new Date(item.modified * 1000).toLocaleString() }), tools));
	}
	if (!items.length) body.append(el("tr", {}, el("td", { colSpan: 4, className: "empty", textContent: listedFiles.length ? "No editable files in this folder." : "This folder is empty." })));
}

async function openFiles(path, remoteSite = "", server = false) {
	remoteFilesSite = remoteSite;
	serverFiles = server && !remoteSite;
	await loadFiles(path);
	$("#files-title").textContent = remoteSite ? "Remote files · " + remoteSite : serverFiles ? "Server files" : "Files";
	if (!filesDialog.open) filesDialog.showModal();
}

// Monaco is downloaded into static/vendor by install/update_admin_site.sh; without it a plain textarea is used
let monacoPromise = null;
let monacoEditor = null;
function loadMonaco() {
	if (monacoPromise) return monacoPromise;
	monacoPromise = new Promise((resolve, reject) => {
		const base = location.origin + "/static/vendor/monaco/";
		const workerUrl = URL.createObjectURL(new Blob([`self.MonacoEnvironment={baseUrl:'${base}'};importScripts('${base}vs/base/worker/workerMain.js');`], { type: "text/javascript" }));
		window.MonacoEnvironment = { getWorkerUrl: () => workerUrl };
		document.head.append(el("script", {
			src: "/static/vendor/monaco/vs/loader.js",
			onload: () => {
				window.require.config({ paths: { vs: "/static/vendor/monaco/vs" } });
				window.require(["vs/editor/editor.main"], () => resolve(window.monaco), reject);
			},
			onerror: () => reject(new Error("Monaco is not installed")),
		}));
	});
	return monacoPromise;
}

const languageByExtension = { conf: "ini", service: "ini", cshtml: "razor", csproj: "xml", props: "xml", config: "xml", sh: "shell", env: "ini", cs: "csharp", yml: "yaml" };
function languageFor(path) {
	const name = path.split("/").pop().toLowerCase();
	return languageByExtension[name.includes(".") ? name.split(".").pop() : ""];
}

let loadingContent = false;
let openFileSaveUrl = "";
async function showInEditor(path, content) {
	let monaco = null;
	try { monaco = await loadMonaco(); } catch (_) { monaco = null; }
	const box = $("#editor-box");
	const area = $("#editor-text");
	if (!monaco) {
		box.hidden = true;
		area.hidden = false;
		area.value = content;
		return;
	}
	area.hidden = true;
	box.hidden = false;
	if (!monacoEditor) {
		monacoEditor = monaco.editor.create(box, { theme: "vs-dark", automaticLayout: true, fontSize: 13, minimap: { enabled: true }, scrollBeyondLastLine: false, renderWhitespace: "selection", tabSize: 4, detectIndentation: true });
		monacoEditor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => run(saveFile));
	}
	const previous = monacoEditor.getModel();
	loadingContent = true;
	try {
		monacoEditor.setModel(null);
		if (previous) previous.dispose();
		const model = monaco.editor.createModel(content, languageFor(path), monaco.Uri.file("/" + path));
		monacoEditor.setModel(model);
		model.onDidChangeContent(() => { if (!loadingContent) setDirty(true); });
	} finally {
		loadingContent = false;
	}
	monacoEditor.focus();
}

const editorValue = () => (monacoEditor && !$("#editor-box").hidden ? monacoEditor.getValue() : $("#editor-text").value);

async function editFile(path, remoteSite = remoteFilesSite) {
	if (!(await discardOk())) return;
	const base = remoteSite ? "/api/remote-fs/" + encodeURIComponent(remoteSite) + "/" : serverFiles ? "/api/server-fs/" : "/api/fs/";
	const saveUrl = base + "save";
	const data = await api(base + "read?path=" + encodeURIComponent(path));
	openFile = path;
	configurationEditor = null;
	openFileSaveUrl = saveUrl;
	if (!editorDialog.open) editorDialog.showModal();
	$("#editor-path").textContent = path;
	$("#editor-status").textContent = "";
	await showInEditor(path, data.content);
	setDirty(false);
}

async function editConfiguration(site, env, kind) {
	if (!(await discardOk())) return;
	const url = `/api/sites/${encodeURIComponent(site.name)}/config/${kind}?scope=${env.mode === "remote" ? "remote" : "local"}&unit=${encodeURIComponent(env.unit)}`;
	let data = await api(url);
	if (data.encoding_required) {
		const dialog = $("#dlg-encoding");
		$("#encoding-message").textContent = data.message;
		dialog.returnValue = "";
		const encoding = await new Promise((resolve) => {
			dialog.onclose = () => resolve(dialog.returnValue === "open" ? $("#config-encoding").value : null);
			dialog.showModal();
		});
		if (!encoding) return;
		data = await api(url + "&encoding=" + encodeURIComponent(encoding));
	}
	configurationEditor = { url, kind, encoding: data.encoding || "utf-8" };
	openFile = data.path;
	if (!editorDialog.open) editorDialog.showModal();
	$("#editor-path").textContent = data.path + (env.mode === "remote" ? " · " + env.host : "");
	$("#editor-status").textContent = configurationEditor.encoding === "utf-8" ? ""
		: `Opened as ${configurationEditor.encoding}. Check the characters before saving; Save converts this file to UTF-8.`;
	await showInEditor(data.path, data.content);
	setDirty(false);
}

async function saveFile() {
	if (openFile === null || savingFile) return;
	savingFile = true;
	$("#editor-save").disabled = true;
	try {
		const configuration = configurationEditor;
		const content = editorValue();
		if (configuration && configuration.encoding !== "utf-8" && !(await ask({
			title: "Convert configuration to UTF-8?",
			message: `This file was opened as ${configuration.encoding}. Verify that its characters look correct. Saving converts the file to UTF-8.`,
			input: false, ok: "Save as UTF-8",
		}))) return;
		await api(configuration ? configuration.url : openFileSaveUrl, {
			json: { action: "save", path: openFile, content },
		});
		if (configuration) configuration.encoding = "utf-8";
		setDirty(editorValue() !== content);
		$("#editor-status").textContent = "Saved " + new Date().toLocaleTimeString();
		if (configuration) {
			const service = configuration.kind === "service";
			const apply = await ask({
				title: service ? "Reload daemon and restart service?" : "Test config and reload Apache?",
				message: service ? "The file has been saved. Restart the service to apply the changes."
					: "The file has been saved. Apache will only reload if the configuration test succeeds.",
				input: false, ok: service ? "Reload and restart" : "Test and reload",
			});
			if (apply) {
				$("#editor-status").textContent = "Applying changes…";
				await api(configuration.url, { json: { action: "apply" } });
				$("#editor-status").textContent = service ? "Saved. Service restarted." : "Saved. Config test passed; Apache reloaded.";
				await loadSites();
			}
		}
	} catch (error) {
		$("#editor-status").textContent = error.message;
		toast(error.message, "error");
	} finally {
		savingFile = false;
		$("#editor-save").disabled = false;
	}
}

async function createItem(kind, title) {
	const name = await ask({ title, placeholder: "Name", ok: "Create" });
	if (!name) return;
	await api(fsUrl(kind), { json: { path: currentPath, name: name.trim() } });
	await loadFiles(currentPath);
}

async function uploadFiles(files) {
	if (!files.length) return;
	const form = new FormData();
	form.append("path", currentPath);
	for (const file of files) form.append("files", file);
	const result = await api(fsUrl("upload"), { method: "POST", body: form });
	toast("Uploaded " + result.count + " file(s)");
	await loadFiles(currentPath);
}

async function renameItem(path, name) {
	const next = await ask({ title: "Rename", value: name, ok: "Rename" });
	if (!next || next === name) return;
	await api(fsUrl("rename"), { json: { path, name: next } });
	await loadFiles(currentPath);
}

async function deleteItem(path, name) {
	if (!(await ask({ title: "Delete " + name + "?", message: "This permanently deletes the file or folder.", input: false, ok: "Delete", danger: true }))) return;
	await api(fsUrl("delete"), { json: { path } });
	await loadFiles(currentPath);
}

$("#btn-new-file").onclick = () => run(() => createItem("newfile", "New file"));
$("#btn-new-folder").onclick = () => run(() => createItem("mkdir", "New folder"));
$("#btn-upload").onclick = () => $("#upload-input").click();
$("#upload-input").onchange = (event) => {
	const files = [...event.target.files];
	event.target.value = "";
	run(() => uploadFiles(files));
};

for (const type of ["dragenter", "dragover"]) drop.addEventListener(type, (event) => { event.preventDefault(); drop.classList.add("dragging"); });
drop.addEventListener("dragleave", () => drop.classList.remove("dragging"));
drop.addEventListener("drop", (event) => {
	event.preventDefault();
	drop.classList.remove("dragging");
	run(() => uploadFiles([...event.dataTransfer.files]));
});
$("#editor-save").onclick = () => run(saveFile);
async function closeEditor() {
	if (savingFile) return;
	if (!(await discardOk())) return;
	openFile = null;
	configurationEditor = null;
	setDirty(false);
	editorDialog.close();
}
$("#editor-close").onclick = closeEditor;
editorDialog.addEventListener("keydown", (event) => {
	if (event.key !== "Escape" || savingFile) return;
	event.preventDefault();
	event.stopImmediatePropagation();
	if (!event.repeat) run(closeEditor);
}, true);
editorDialog.addEventListener("cancel", (event) => {
	event.preventDefault();
	if (event.target === editorDialog) run(closeEditor);
});
$("#editor-text").oninput = () => setDirty(true);
$("#editor-text").onkeydown = (event) => {
	if ((event.ctrlKey || event.metaKey) && event.key === "s") { event.preventDefault(); run(saveFile); }
	if (event.key === "Tab" && !event.shiftKey) {
		event.preventDefault();
		const area = event.target;
		area.setRangeText("\t", area.selectionStart, area.selectionEnd, "end");
		setDirty(true);
	}
};

// -------------------------------------------------------------- terminals

const terminals = {};
let activeTerminal = null;

function updateTerminalState() {
	const count = Object.keys(terminals).length;
	$("#term-count").textContent = count;
	$("#term-count").hidden = !count;
	$("#btn-dock").hidden = !count;
	if (!count) $("#dock").hidden = true;
}

function showDock() {
	$("#dock").hidden = false;
	fitActive();
}

function toggleDock() {
	if (!$("#dock").hidden) { $("#dock").hidden = true; return; }
	if (!Object.keys(terminals).length) { toast("No terminals open. Open a shell or run a script from a site."); return; }
	showDock();
}
$("#btn-dock").onclick = toggleDock;
// Closing the window also removes the terminals whose process has finished; running ones keep going
async function closeDock() {
	for (const t of Object.values(terminals).filter((x) => x.exited)) await t.remove();
	$("#dock").hidden = true;
}
$("#dock-close").onclick = closeDock;
document.addEventListener("keydown", (event) => {
	if (event.key !== "Escape" || $("#dock").hidden || document.querySelector("dialog[open]")) return;
	event.preventDefault();
	event.stopImmediatePropagation();
	if (!event.repeat) run(closeDock);
}, true);
$("#dock-size").onclick = () => { $("#dock").classList.toggle("max"); fitActive(); };

function fitActive() {
	const t = terminals[activeTerminal];
	if (!t) return;
	const fitNow = () => { try { t.fit.fit(); } catch (_) { /* hidden while measuring */ } };
	fitNow();
	// Layout may not be final yet when the dock was just shown
	requestAnimationFrame(() => { fitNow(); requestAnimationFrame(fitNow); });
	t.term.focus();
}
window.addEventListener("resize", () => { if (!$("#dock").hidden) fitActive(); });

function activate(id) {
	activeTerminal = id;
	for (const [key, t] of Object.entries(terminals)) {
		t.pane.hidden = key !== id;
		t.tab.classList.toggle("active", key === id);
	}
	fitActive();
}

function connectTerminal(t) {
	const scheme = location.protocol === "https:" ? "wss://" : "ws://";
	const ws = new WebSocket(`${scheme}${location.host}/ws/term/${t.id}`);
	t.ws = ws;
	ws.onopen = () => {
		t.term.reset();
		ws.send(JSON.stringify({ t: "r", rows: t.term.rows, cols: t.term.cols }));
	};
	ws.onmessage = (event) => {
		const message = JSON.parse(event.data);
		if (message.o !== undefined) t.term.write(message.o);
		if (message.x !== undefined) {
			t.exited = true;
			updateSiteActivity();
			t.exitCode = message.x;
			if (t.autoClose && message.x === 0 && message.output_problem === false && t.remove) {
				t.remove();
				run(loadSites);
				return;
			}
			t.term.write(`\r\n\x1b[33m[Process exited with code ${message.x}]\x1b[0m\r\n`);
			t.title.textContent += message.x === 0 && !message.output_problem ? " ✓" : " !";
			run(loadSites).finally(() => {
				if (activeTerminal === t.id && !$("#dock").hidden && !document.querySelector("dialog[open]")) {
					requestAnimationFrame(fitActive);
				}
			});
		}
	};
	ws.onclose = () => {
		if (!t.exited && terminals[t.id]) setTimeout(() => terminals[t.id] && connectTerminal(t), 2000);
	};
}

function openTerminal(id, title, autoClose, sites = [], running = true, scope = "main") {
	if (terminals[id]) return activate(id);
	const pane = el("div", { className: "pane", hidden: true });
	const titleNode = el("span", { textContent: title });
	const close = el("span", { className: "x", textContent: "✕", title: "Close (stops the process)" });
	const tab = el("div", { className: "tab", onclick: () => activate(id) }, titleNode, close);
	$("#tabs").append(tab);
	$("#panes").append(pane);

	const term = new Terminal({ cursorBlink: true, fontSize: 14, scrollback: 5000, fontFamily: "ui-monospace, Menlo, Consolas, monospace", theme: { background: "#000000" } });
	const fit = new FitAddon.FitAddon();
	term.loadAddon(fit);
	term.open(pane);
	const t = { id, term, fit, pane, tab, title: titleNode, taskTitle: title, sites, scope, exited: !running, ws: null, autoClose: !!autoClose, remove: null };
	terminals[id] = t;
	updateSiteActivity();

	// Enter closes finished terminals, including failed processes.
	term.attachCustomKeyEventHandler((event) => {
		if (t.exited && event.key === "Enter") {
			if (event.type === "keydown") closeDock();
			return false;
		}
		return true;
	});
	term.onData((data) => t.ws && t.ws.readyState === 1 && t.ws.send(JSON.stringify({ t: "i", d: data })));
	term.onResize(({ rows, cols }) => t.ws && t.ws.readyState === 1 && t.ws.send(JSON.stringify({ t: "r", rows, cols })));
	new ResizeObserver(() => { if (!pane.hidden && pane.clientHeight > 0) { try { fit.fit(); } catch (_) { /* ignore */ } } }).observe($("#panes"));
	setInterval(() => t.ws && t.ws.readyState === 1 && t.ws.send(JSON.stringify({ t: "p" })), 30000);

	t.remove = async () => {
		await run(() => api(`/api/term/${id}/close`, { json: {} }));
		t.exited = true;
		if (t.ws) t.ws.close();
		term.dispose(); tab.remove(); pane.remove();
		delete terminals[id];
		updateSiteActivity();
		activeTerminal = Object.keys(terminals)[0] || null;
		if (activeTerminal) activate(activeTerminal);
		updateTerminalState();
	};
	close.onclick = async (event) => {
		event.stopPropagation();
		if (!t.exited && !(await ask({ title: "Close terminal", message: "The process is still running. Close and stop it?", input: false, ok: "Stop and close", danger: true }))) return;
		await t.remove();
	};
	connectTerminal(t);
	updateTerminalState();
	activate(id);
}

async function openTask(payload) {
	const data = await api("/api/term", { json: payload });
	openTerminal(data.id, data.title, data.auto_close, data.sites, data.running, data.scope);
	if (terminals[data.id]) {
		showDock();
		for (const dialog of document.querySelectorAll("dialog[open]")) {
			dialog.addEventListener("close", () => {
				if (activeTerminal === data.id && !document.querySelector("dialog[open]")) {
					requestAnimationFrame(fitActive);
				}
			}, { once: true });
		}
	}
}

async function restoreTerminals() {
	const { sessions } = await api("/api/term");
	for (const s of sessions) openTerminal(s.id, s.title, s.auto_close, s.sites, s.running, s.scope);
	if (sessions.length) { for (const t of Object.values(terminals)) t.pane.hidden = true; activate(sessions[0].id); }
}

run(async () => {
	syncSiteForm();
	await loadSites();
	await route();
	await restoreTerminals();
	updateTerminalState();
});


// ------------------------------------------------------------------ settings menu
let sqlConnections = [];
let sqlSnapshot = { databases: [], backups: [] };
let sqlLoadGeneration = 0;

function sqlSelected() {
	const id = $("#sql-connection").value;
	if (!id) throw new Error("Choose a SQL connection.");
	return id;
}

async function loadSqlConnections() {
	const selected = $("#sql-connection").value;
	const data = await api("/api/sql/connections");
	sqlConnections = data.connections;
	const select = $("#sql-connection");
	select.replaceChildren(el("option", { value: "", textContent: "Choose a connection..." }),
		...sqlConnections.map((item) => el("option", { value: item.id, textContent: `${item.name} · ${item.host}:${item.port}` })));
	select.value = sqlConnections.some((item) => item.id === selected) ? selected : sqlConnections[0]?.id || "";
	await refreshSql();
}

async function refreshSql() {
	const id = $("#sql-connection").value;
	const generation = ++sqlLoadGeneration;
	for (const key of ["sql-refresh", "sql-remove-connection", "sql-create", "sql-backup-all"]) $("#" + key).disabled = !id;
	$("#sql-databases").replaceChildren();
	$("#sql-backups").replaceChildren();
	sqlSnapshot = { databases: [], backups: [] };
	if (!id) {
		$("#sql-status").textContent = "Add a local or remote SQL connection to get started.";
		return;
	}
	$("#sql-status").textContent = "Loading databases and backup history...";
	try {
		const data = await api("/api/sql/" + encodeURIComponent(id));
		if (generation !== sqlLoadGeneration) return;
		sqlSnapshot = data;
		$("#sql-status").textContent = `${data.databases.length} databases · ${data.backups.length} recorded full backups`;
		for (const database of data.databases) {
			const actions = el("div", { className: "actions" });
			if (database.id > 4) {
				for (const [label, action] of [["Backup", "backup"], ["Copy", "copy"], ["Delete", "drop"]]) {
					actions.append(el("button", { textContent: label, onclick: () => run(() => sqlDatabaseAction(action, database)) }));
				}
			}
			$("#sql-databases").append(el("tr", {}, el("td", { textContent: database.name }),
				el("td", { textContent: database.size_mb + " MB" }), el("td", { textContent: database.state }),
				el("td", { textContent: database.recovery }), el("td", {}, actions)));
		}
		for (const backup of data.backups) {
			$("#sql-backups").append(el("tr", {}, el("td", { textContent: backup.database_name }),
				el("td", { textContent: backup.finished }), el("td", { textContent: backup.size_mb + " MB" }),
				el("td", { textContent: backup.path }), el("td", {}, el("button", {
					textContent: "Restore", disabled: backup.media_count !== 1,
					title: backup.media_count !== 1 ? "Striped backups are not supported" : "Restore this full backup",
					onclick: () => run(() => sqlRestore(backup)),
				}))));
		}
		decorateSql();
	} catch (error) {
		if (generation !== sqlLoadGeneration) return;
		$("#sql-status").textContent = error.message;
		for (const key of ["sql-create", "sql-backup-all"]) $("#" + key).disabled = true;
		throw error;
	}
}

async function sqlDatabaseAction(action, database) {
	const connection = sqlSelected();
	const payload = { kind: "sql_action", connection, action, database: database.name };
	if (action === "copy") {
		const target = await ask({ title: "Copy database", message: `Back up ${database.name} and restore to a new database. Logins are not copied; existing database users remain in the backup.`, placeholder: "New database name", ok: "Copy" });
		if (!target) return;
		payload.target = target.trim();
	} else if (action === "drop") {
		const confirmation = await ask({ title: "Delete database?", message: `This permanently deletes ${database.name} and disconnects all sessions. Backups and server logins are kept. Type ${database.name} to confirm.`, match: database.name, danger: true, ok: "Delete" });
		if (!confirmation) return;
		payload.confirmation = confirmation;
	} else if (!(await ask({ title: "Backup database?", message: `Create a full copy-only backup of ${database.name} in the connection's server-side backup directory.`, input: false, ok: "Backup" }))) return;
	await openTask(payload);
}

async function sqlRestore(backup) {
	const connection = sqlSelected();
	const target = await ask({ title: "Restore database", message: `Restore ${backup.database_name} from ${backup.path}. SQL Server must still be able to read this file. Logins are not created automatically.`, value: backup.database_name, ok: "Continue" });
	if (!target) return;
	const name = target.trim();
	const existing = sqlSnapshot.databases.some((item) => item.name.toLowerCase() === name.toLowerCase());
	let confirmation = "";
	if (existing) {
		confirmation = await ask({ title: "Overwrite existing database?", message: `All data in ${name} will be replaced and active sessions disconnected. Take a backup first. Type ${name} to confirm.`, match: name, danger: true, ok: "Overwrite and restore" });
		if (!confirmation) return;
	} else if (!(await ask({ title: "Restore to new database?", message: `Create ${name} from the selected backup.`, input: false, ok: "Restore" }))) return;
	await openTask({ kind: "sql_action", connection, action: "restore", database: backup.database_name,
		target: name, backup_id: backup.id, overwrite: existing, confirmation });
}

function decorateSql() {
	const styles = {
		"Add connection": ["server", "blue"], "Remove connection": ["trash", "red"],
		"Create database": ["server", "green"], "Backup": ["download", "blue"],
		"Backup all": ["download", "blue"], "Copy": ["sync", "purple"],
		"Restore": ["upload", "amber"], "Refresh": ["sync", "blue"],
	};
	for (const button of $("#view-sql").querySelectorAll("button:not([data-tone])")) {
		const style = styles[button.textContent.trim()];
		if (!style) continue;
		button.dataset.tone = style[1];
		button.prepend(svgIcon(style[0]));
	}
	decorate($("#view-sql"));
}

$("#settings-sql").onclick = () => go("#sql");
$("#sql-back").onclick = () => go("#sites");
$("#sql-refresh").onclick = () => run(refreshSql);
$("#sql-connection").onchange = () => run(refreshSql);
$("#sql-create").onclick = () => run(async () => {
	const connection = sqlSelected();
	const name = await ask({ title: "Create database", message: "Creates a database only, not a new SQL login.", placeholder: "Database name", ok: "Create" });
	if (name) await openTask({ kind: "sql_action", connection, action: "create", database: name.trim() });
});
$("#sql-backup-all").onclick = () => run(async () => {
	const connection = sqlSelected();
	if (await ask({ title: "Backup all databases?", message: "Create full copy-only backups of all online user databases on this connection. System databases are excluded.", input: false, ok: "Backup all" })) {
		await openTask({ kind: "sql_action", connection, action: "backup_all" });
	}
});
$("#sql-remove-connection").onclick = () => run(async () => {
	const id = sqlSelected();
	if (!(await ask({ title: "Remove connection?", message: "Remove the saved connection and credentials from this admin server. Databases and backups are not deleted.", input: false, ok: "Remove", danger: true }))) return;
	await api("/api/sql/connections", { method: "DELETE", json: { id } });
	await loadSqlConnections();
});
$("#sql-add-connection").onclick = () => {
	$("#form-sql-connection").reset();
	$("#dlg-sql-connection").showModal();
};
$("#sql-connection-cancel").onclick = () => $("#dlg-sql-connection").close();
$("#form-sql-connection").onsubmit = (event) => {
	event.preventDefault();
	const form = event.target;
	const data = Object.fromEntries(new FormData(form));
	data.port = Number(data.port);
	data.trust_certificate = form.elements.trust_certificate.checked;
	run(async () => {
		await api("/api/sql/connections", { json: data });
		form.reset();
		$("#dlg-sql-connection").close();
		await loadSqlConnections();
	});
};
decorateSql();
$("#settings-sql").dataset.tone = "blue";
$("#settings-sql").prepend(svgIcon("server"));

const settingsMenu = $("#settings-menu");
async function checkSystemUpdate(refresh = false) {
	const result = await api("/api/system-update" + (refresh ? "?refresh=1" : ""));
	const badge = $("#system-update-badge");
	const available = result.status === "available";
	const unknown = result.status === "unknown";
	badge.hidden = !available && !unknown;
	badge.textContent = unknown ? "Version unknown" : "Update available";
	badge.classList.toggle("system-update-available", available);
	$("#settings-update").hidden = !available && !unknown;
	$("#settings-update").replaceChildren(svgIcon("sync"), unknown ? "Initialize / update" : "Update");
	$("#btn-settings").title = unknown ? "Installed version unknown. Use Initialize / update to install the latest version and record it."
		: result.status === "error" ? result.error
		: available ? `Update available · Installed: ${result.installed.slice(0, 7)} · GitHub: ${result.latest.slice(0, 7)}` : "Settings";
	if (result.status === "error") throw new Error(result.error);
}
$("#btn-settings").onclick = (event) => {
	event.stopPropagation();
	settingsMenu.hidden = !settingsMenu.hidden;
	if (!settingsMenu.hidden) run(() => checkSystemUpdate(true));
};
document.addEventListener("click", () => { settingsMenu.hidden = true; });
$("#settings-restart-apache").onclick = () => run(async () => {
	if (await ask({ title: "Restart Apache?", message: "All sites on this server are briefly unavailable.", input: false, ok: "Restart", danger: true })) await openTask({ kind: "apache", tool: "restart" });
});
$("#settings-admin-site").onclick = () => { const admin = allSites.find((s) => s.admin); if (admin) go(siteLink(admin.name)); };
$("#settings-apache-test").onclick = () => run(() => openTask({ kind: "apache", tool: "configtest" }));
$("#settings-update").onclick = () => run(async () => {
	if (await ask({
		title: "Update VNS and the admin site?",
		message: "Download and install the latest VNS version, then update this admin site. The admin service restarts near the end, disconnecting this terminal. Reload the page once it is available again.",
		input: false, ok: "Update",
	})) await openTask({ kind: "update_system" });
});
$("#settings-update").dataset.tone = "purple";
$("#settings-update").prepend(svgIcon("sync"));

decorate($("#settings-menu"));
decorate($("#view-sites .toolbar"));
decorate($("#view-files"));
run(checkSystemUpdate);
setInterval(() => { if (!document.hidden) run(checkSystemUpdate); }, 300000);
