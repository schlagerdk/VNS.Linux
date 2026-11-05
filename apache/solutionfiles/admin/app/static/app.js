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

function toast(message, type) {
	const node = el("div", { className: "toast " + (type || ""), textContent: message });
	$("#toasts").append(node);
	setTimeout(() => node.remove(), type === "error" ? 8000 : 3500);
}

async function run(action) {
	try {
		return await action();
	} catch (err) {
		toast(err.message, "error");
	}
}

// Replaces prompt()/confirm(). Resolves to the entered text (or true without an input), or null when cancelled.
function ask({ title, message = "", value = "", placeholder = "", ok = "OK", danger = false, input = true, match = null }) {
	const dlg = $("#dlg-prompt");
	const field = $("#prompt-input");
	const okButton = $("#prompt-ok");
	$("#prompt-title").textContent = title;
	$("#prompt-message").textContent = message;
	$("#prompt-message").hidden = !message;
	field.hidden = !input;
	field.value = value;
	field.placeholder = placeholder;
	okButton.value = "ok";
	okButton.textContent = ok;
	okButton.disabled = match !== null;
	dlg.classList.toggle("danger", danger);
	field.oninput = () => { if (match !== null) okButton.disabled = field.value !== match; };
	$("#prompt-cancel").onclick = () => dlg.close("cancel");
	dlg.returnValue = "";
	return new Promise((resolve) => {
		dlg.onclose = () => resolve(dlg.returnValue === "ok" ? (input ? field.value : true) : null);
		dlg.showModal();
		if (input) { field.focus(); field.select(); } else okButton.focus();
	});
}

// ------------------------------------------------------------------ views

let filesLoaded = false;
let currentView = "sites";
const views = ["sites", "site", "files"];

function showView(name) {
	currentView = name;
	for (const view of views) $("#view-" + view).hidden = view !== name;
	if (name === "sites") $("#search").focus({ preventScroll: true });
}

// Hash routes: #sites, #site/<name>, #files/<path>
async function route() {
	const hash = decodeURIComponent(location.hash.slice(1));
	const slash = hash.indexOf("/");
	const view = slash < 0 ? hash : hash.slice(0, slash);
	const arg = slash < 0 ? "" : hash.slice(slash + 1);
	if (currentView === "files" && view !== "files" && !(await discardOk())) {
		history.replaceState(null, "", "#files/" + encodeURI(currentPath));
		return;
	}
	if (view === "files") {
		showView("files");
		await run(() => loadFiles(arg));
	} else if (view === "site" && arg) {
		showView("site");
		renderSite(arg);
	} else {
		showView("sites");
	}
}
window.addEventListener("hashchange", route);
const go = (hash) => { location.hash = hash; };
const siteLink = (name) => "#site/" + encodeURIComponent(name);
const filesLink = (path) => "#files/" + path.split("/").map(encodeURIComponent).join("/");

// ------------------------------------------------------------------ sites

let allSites = [];
let filter = "all";
let query = "";

const kindLabel = { umbraco: "Umbraco", csharp: "C# / .NET", python: "Python", html: "HTML" };

async function loadSites() {
	allSites = (await api("/api/sites")).sites;
	renderSites();
	if (currentView === "site") renderSite(decodeURIComponent(location.hash.slice(6)));
}

// A site is running when any of its services is; sites without a service are static
function siteState(site) {
	const units = site.environments.filter((e) => e.unit);
	if (!units.length) return "";
	return units.some((e) => e.state === "active") ? "active" : "inactive";
}
const hasProd = (site) => site.environments.some((e) => e.key === "prod");

function renderStats() {
	const count = (test) => allSites.filter(test).length;
	const chips = [
		["all", "All", allSites.length],
		["running", "Running", count((s) => siteState(s) === "active")],
		["stopped", "Stopped", count((s) => siteState(s) === "inactive")],
		["prod", "With prod", count(hasProd)],
	];
	for (const kind of Object.keys(kindLabel)) {
		const n = count((s) => s.kind === kind);
		if (n) chips.push([kind, kindLabel[kind], n]);
	}
	const box = $("#stats");
	box.replaceChildren();
	for (const [key, label, n] of chips) {
		box.append(el("div", { className: "chip" + (filter === key ? " active" : ""), onclick: () => { filter = key; renderSites(); } },
			el("b", { textContent: n }), el("span", { textContent: label })));
	}
}

function matches(site) {
	const state = siteState(site);
	if (filter === "running" && state !== "active") return false;
	if (filter === "stopped" && state !== "inactive") return false;
	if (filter === "prod" && !hasProd(site)) return false;
	if (kindLabel[filter] && site.kind !== filter) return false;
	const text = query.trim().toLowerCase();
	const haystack = site.name + " " + site.environments.map((e) => e.binding + " " + (e.host || "")).join(" ");
	return !text || haystack.toLowerCase().includes(text);
}

function renderSites() {
	renderStats();
	const box = $("#sites");
	box.replaceChildren();
	const visible = allSites.filter(matches);
	$("#no-sites").hidden = visible.length > 0;
	for (const site of visible) box.append(siteCard(site));
}

const bindingLink = (binding) => binding
	? el("a", { className: "link", textContent: binding, href: "https://" + binding + "/", target: "_blank", rel: "noopener noreferrer", onclick: (event) => event.stopPropagation() })
	: el("span", { className: "muted", textContent: "no binding" });

function kindBadges(site) {
	const badges = el("div", { className: "badges" }, el("span", { className: "badge " + site.kind, textContent: kindLabel[site.kind] || site.kind }));
	if (site.admin) badges.append(el("span", { className: "badge admin", textContent: "this admin site" }));
	return badges;
}

function siteCard(site) {
	const state = siteState(site);
	const rows = el("div", { className: "env-rows" });
	for (const env of site.environments) {
		const dot = env.unit ? env.state || "unknown" : env.mode === "remote" ? "remote" : "";
		rows.append(el("div", { className: "env-row" },
			el("span", { className: "dot " + dot }),
			el("span", { className: "env-label", textContent: env.label }),
			bindingLink(env.binding),
			env.mode === "remote" ? el("span", { className: "muted", textContent: "on " + (env.host || "remote") }) : el("span")));
	}
	const actions = el("div", { className: "actions" });
	const add = (label, handler, className) => actions.append(el("button", { textContent: label, className: "small " + (className || ""), onclick: (event) => { event.stopPropagation(); run(handler); } }));
	add("Manage", () => go(siteLink(site.name)), "primary");
	add("Shell", () => openTask({ kind: "shell", site: site.name }));
	add("Files", () => go(filesLink(site.name)));

	return el("article", { className: "site " + state + (site.admin ? " is-admin" : ""), onclick: () => go(siteLink(site.name)) },
		el("div", { className: "site-head" }, el("span", { className: "dot " + state }), el("h3", { className: "site-title", textContent: site.name })),
		kindBadges(site), rows, actions);
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
		const files = site.scripts.filter(test);
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
	head.replaceChildren();
	envs.replaceChildren();
	if (!site) {
		head.append(el("p", { className: "empty", textContent: allSites.length ? "Site not found." : "Loading…" }));
		return;
	}
	const actions = el("div", { className: "actions" });
	const add = (label, handler, className) => actions.append(el("button", { textContent: label, className: className || "", onclick: () => run(handler) }));
	add("Shell", () => openTask({ kind: "shell", site: site.name }), "primary");
	add("Files", () => go(filesLink(site.name)));
	if (site.scripts.length) actions.append(scriptSelect(site));
	if (!hasProd(site)) add("Change binding", () => changeBinding(site, "test"));
	if (!site.admin) add(hasProd(site) ? "Delete everything" : "Delete site", () => deleteSite(site, "all"), "danger");
	head.append(el("div", { className: "detail-title" }, el("h2", { textContent: site.name }), kindBadges(site)), actions);
	for (const env of site.environments) envs.append(envPanel(site, env));
}

function envPanel(site, env) {
	const dot = env.unit ? env.state || "unknown" : "";
	const heading = el("div", { className: "env-head" },
		el("span", { className: "dot " + dot }), el("h3", { textContent: env.label }),
		el("span", { className: "badge " + (env.unit ? "state-" + (env.state || "unknown") : ""), textContent: env.unit ? env.state || "unknown" : env.mode === "remote" ? "remote" : "static" }));
	if (env.environment) heading.append(el("span", { className: "muted", textContent: env.environment }));

	const meta = el("dl", { className: "meta" }, el("dt", { textContent: "Binding" }), el("dd", {}, bindingLink(env.binding)));
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
	if (env.unit) {
		if (env.unit !== site.name || !site.admin) add(env.state === "active" ? "Stop" : "Start", () => serviceAction(site, env, env.state === "active" ? "stop" : "start"));
		add("Restart", () => serviceAction(site, env, "restart"));
		add("Logs", () => showLogs(site, env));
	}
	if (env.mode !== "remote" && env.path && env.exists !== false) {
		add("Shell", () => openTask({ kind: "shell", site: site.name, path: env.path, label: site.name + " · " + env.label }));
		add("Files", () => go(filesLink(env.path)));
	}

	if (hasProd(site) && !site.admin) add("Change binding", () => changeBinding(site, env.key === "prod" ? "prod" : "test", env));
	if (hasProd(site) && !site.admin) add(env.key === "prod" ? "Delete production site" : "Delete test site", () => deleteSite(site, env.key === "prod" ? "prod" : "test"), "danger");

	const panel = el("section", { className: "env " + (env.key === "prod" ? "prod" : "") }, heading, meta);
	if (env.exists === false) panel.append(el("p", { className: "muted", textContent: "Nothing published here yet." }));
	if (env.mode === "remote") panel.append(el("p", { className: "muted", textContent: "Runs on another server. Use publish_prod.sh and sync_prod.sh from “Run script…” to deploy." }));
	panel.append(actions);
	return panel;
}

async function serviceAction(site, env, action) {
	await api(`/api/sites/${encodeURIComponent(site.name)}/service`, { json: { action, unit: env.unit } });
	toast(`${env.unit}: ${action} done`, "ok");
	await loadSites();
}

const logsDialog = $("#dlg-logs");
let logsTarget = null;
async function showLogs(site, env) {
	logsTarget = { site, env };
	$("#logs-title").textContent = `Logs · ${env.unit}`;
	if (!logsDialog.open) logsDialog.showModal();
	await refreshLogs();
}
async function refreshLogs() {
	$("#logs-text").textContent = "Loading…";
	const data = await api(`/api/sites/${encodeURIComponent(logsTarget.site.name)}/logs?unit=${encodeURIComponent(logsTarget.env.unit)}`);
	const box = $("#logs-text");
	box.textContent = data.text || "(no log lines)";
	box.scrollTop = box.scrollHeight;
}
$("#logs-refresh").onclick = () => run(refreshLogs);
$("#logs-close").onclick = () => logsDialog.close();

async function changeBinding(site, scope, env) {
	const current = env ? env.binding : site.binding;
	const label = scope === "prod" ? "production site" : "test site";
	const binding = await ask({ title: `Change ${label} binding`, message: `New domain for the ${label} of ${site.name}`, value: current, ok: "Change" });
	if (binding && binding.trim() !== current) await openTask({ kind: "change_binding", site: site.name, binding: binding.trim(), scope });
}

async function deleteSite(site, scope) {
	const prod = site.environments.find((e) => e.key === "prod");
	const remote = prod && prod.mode === "remote";
	const target = scope === "prod" ? (prod.unit || prod.folder || site.name) : site.name;
	const what = {
		all: `This deletes the site and its production site on this server: files, services, Apache configs and logs.`,
		test: `This deletes the site's own files, service and Apache config. The production site is kept.`,
		prod: remote
			? `The production site runs on ${prod.host}, which is not touched. This only removes the production scripts from this site.`
			: `This deletes the production site: its files, service, Apache config and logs. The test site is kept.`,
	}[scope];
	const typed = await ask({
		title: { all: "Delete everything", test: "Delete test site", prod: "Delete production site" }[scope], danger: true, ok: "Delete", match: target, placeholder: target,
		message: `${what}\nType "${target}" to confirm.`,
	});
	if (typed === null) return;
	let certificates = false;
	if (!(scope === "prod" && remote)) {
		certificates = (await ask({ title: "Certificate", message: "Also revoke and remove the certificate?", input: false, ok: "Remove certificate" })) === true;
	}
	await openTask({ kind: "delete_site", site: site.name, certificates, scope });
}

$("#search").oninput = (event) => {
	query = event.target.value;
	if (currentView !== "sites") go("sites");
	renderSites();
};
$("#btn-refresh").onclick = () => run(loadSites);
$("#btn-server-shell").onclick = () => run(() => openTask({ kind: "shell" }));
$("#btn-browse").onclick = () => go("files/");
document.addEventListener("keydown", (event) => {
	const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
	if (event.key === "/" && !typing) { event.preventDefault(); if (currentView !== "sites") go("sites"); $("#search").focus(); }
	if (event.key === "`" && event.ctrlKey) { event.preventDefault(); toggleDock(); }
	if (event.key === "Escape") $("#user-menu").hidden = true;
});
setInterval(() => {
	const busy = document.activeElement && $("#view-sites").contains(document.activeElement) && document.activeElement.tagName !== "BODY";
	if (!document.hidden && currentView !== "files" && !busy && !document.querySelector("dialog[open]")) loadSites().catch(() => {});
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
	$("#csharp-options").hidden = siteForm.type.value !== "csharp";
}
siteForm.type.onchange = syncSiteForm;
siteForm.site.oninput = () => { if (!siteForm.binding.dataset.edited) siteForm.binding.value = siteForm.site.value; };
siteForm.binding.oninput = () => { siteForm.binding.dataset.edited = "1"; };
$("#btn-new-site").onclick = () => { siteForm.reset(); delete siteForm.binding.dataset.edited; syncSiteForm(); dialog.showModal(); siteForm.site.focus(); };
$("#site-cancel").onclick = () => dialog.close();
siteForm.onsubmit = () => {
	const data = Object.fromEntries(new FormData(siteForm));
	data.kind = "create_site";
	run(() => openTask(data));
};

// ------------------------------------------------------------------ files

var currentPath = "";
const editorDialog = $("#dlg-editor");
let openFile = null;
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

async function loadFiles(path) {
	const data = await api("/api/fs/list?path=" + encodeURIComponent(path));
	currentPath = data.path;
	filesLoaded = true;
	history.replaceState(null, "", filesLink(currentPath));
	const site = currentPath.split("/")[0];
	$("#files-back").href = site ? siteLink(site) : "#sites";
	$("#files-back").textContent = site ? "← " + site : "← All sites";
	const crumbs = $("#crumbs");
	crumbs.replaceChildren(el("a", { className: "link", textContent: "/var/www", onclick: () => run(() => loadFiles("")) }));
	let accumulated = "";
	for (const part of currentPath ? currentPath.split("/") : []) {
		accumulated = join(accumulated, part);
		const target = accumulated;
		crumbs.append(" / ", el("a", { className: "link", textContent: part, onclick: () => run(() => loadFiles(target)) }));
	}
	const body = $("#files tbody");
	body.replaceChildren();
	if (currentPath) {
		body.append(el("tr", { onclick: () => run(() => loadFiles(parentOf(currentPath))), style: "cursor:pointer" }, el("td", { textContent: "↰ .." }), el("td"), el("td"), el("td")));
	}
	for (const item of data.items) {
		const full = join(currentPath, item.name);
		const link = el("a", { className: "link", textContent: (item.dir ? "📁 " : "📄 ") + item.name, onclick: () => run(() => (item.dir ? loadFiles(full) : editFile(full))) });
		const tools = el("td", { className: "tools" });
		if (!item.dir) tools.append(el("a", { className: "link", textContent: "Download", href: "/api/fs/download?path=" + encodeURIComponent(full) }));
		tools.append(el("a", { className: "link", textContent: "Rename", onclick: () => run(() => renameItem(full, item.name)) }));
		tools.append(el("a", { className: "link", textContent: "Delete", onclick: () => run(() => deleteItem(full, item.name)) }));
		body.append(el("tr", {}, el("td", {}, link), el("td", { className: "num muted", textContent: item.dir ? "" : sizeText(item.size) }), el("td", { className: "muted", textContent: new Date(item.modified * 1000).toLocaleString() }), tools));
	}
	if (!data.items.length) body.append(el("tr", {}, el("td", { colSpan: 4, className: "empty", textContent: "This folder is empty." })));
}

async function editFile(path) {
	if (!(await discardOk())) return;
	const data = await api("/api/fs/read?path=" + encodeURIComponent(path));
	openFile = path;
	if (!editorDialog.open) editorDialog.showModal();
	$("#editor-path").textContent = path;
	$("#editor-text").value = data.content;
	$("#editor-status").textContent = "";
	setDirty(false);
}

async function saveFile() {
	if (openFile === null) return;
	await api("/api/fs/save", { json: { path: openFile, content: $("#editor-text").value } });
	setDirty(false);
	$("#editor-status").textContent = "Saved " + new Date().toLocaleTimeString();
}

async function renameItem(path, name) {
	const next = await ask({ title: "Rename", value: name, ok: "Rename" });
	if (next && next !== name) {
		await api("/api/fs/rename", { json: { path, name: next } });
		await loadFiles(currentPath);
	}
}

async function deleteItem(path, name) {
	if ((await ask({ title: "Delete", message: `Delete ${name}? This cannot be undone.`, input: false, ok: "Delete", danger: true })) !== true) return;
	await api("/api/fs/delete", { json: { path } });
	if (openFile === path) { openFile = null; setDirty(false); }
	await loadFiles(currentPath);
}

async function createItem(kind, title) {
	const name = await ask({ title, ok: "Create" });
	if (name) {
		await api("/api/fs/" + kind, { json: { path: currentPath, name } });
		await loadFiles(currentPath);
	}
}

async function uploadFiles(files) {
	if (!files.length) return;
	const form = new FormData();
	form.append("path", currentPath);
	for (const file of files) form.append("files", file);
	const result = await api("/api/fs/upload", { method: "POST", body: form });
	toast(`Uploaded ${result.count} file(s)`, "ok");
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
const drop = $("#drop");
drop.addEventListener("dragover", (event) => { event.preventDefault(); drop.classList.add("dragging"); });
drop.addEventListener("dragleave", (event) => { if (event.target === drop || !drop.contains(event.relatedTarget)) drop.classList.remove("dragging"); });
drop.addEventListener("drop", (event) => {
	event.preventDefault();
	drop.classList.remove("dragging");
	run(() => uploadFiles([...event.dataTransfer.files]));
});
$("#editor-save").onclick = () => run(saveFile);
async function closeEditor() {
	if (!(await discardOk())) return;
	openFile = null;
	setDirty(false);
	editorDialog.close();
}
$("#editor-close").onclick = closeEditor;
editorDialog.addEventListener("cancel", (event) => { event.preventDefault(); closeEditor(); });
editorDialog.addEventListener("keydown", (event) => {
	if (event.key === "Escape" && !$("#dlg-prompt").open) { event.preventDefault(); closeEditor(); }
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
$("#dock-close").onclick = () => { $("#dock").hidden = true; };
$("#dock-size").onclick = () => { $("#dock").classList.toggle("max"); fitActive(); };

function fitActive() {
	const t = terminals[activeTerminal];
	if (t) { try { t.fit.fit(); } catch (_) { /* hidden while measuring */ } t.term.focus(); }
}

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
			t.term.write(`\r\n\x1b[33m[Process exited with code ${message.x}]\x1b[0m\r\n`);
			t.title.textContent += " ✓";
			loadSites().catch(() => {});
		}
	};
	ws.onclose = () => {
		if (!t.exited && terminals[t.id]) setTimeout(() => terminals[t.id] && connectTerminal(t), 2000);
	};
}

function openTerminal(id, title) {
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
	const t = { id, term, fit, pane, tab, title: titleNode, exited: false, ws: null };
	terminals[id] = t;

	term.onData((data) => t.ws && t.ws.readyState === 1 && t.ws.send(JSON.stringify({ t: "i", d: data })));
	term.onResize(({ rows, cols }) => t.ws && t.ws.readyState === 1 && t.ws.send(JSON.stringify({ t: "r", rows, cols })));
	new ResizeObserver(() => { if (!pane.hidden) { try { fit.fit(); } catch (_) { /* ignore */ } } }).observe(pane);
	setInterval(() => t.ws && t.ws.readyState === 1 && t.ws.send(JSON.stringify({ t: "p" })), 30000);

	close.onclick = async (event) => {
		event.stopPropagation();
		if (!t.exited && !(await ask({ title: "Close terminal", message: "The process is still running. Close and stop it?", input: false, ok: "Stop and close", danger: true }))) return;
		await run(() => api(`/api/term/${id}/close`, { json: {} }));
		t.ws && (t.exited = true, t.ws.close());
		term.dispose(); tab.remove(); pane.remove();
		delete terminals[id];
		activeTerminal = Object.keys(terminals)[0] || null;
		if (activeTerminal) activate(activeTerminal);
		updateTerminalState();
	};
	connectTerminal(t);
	updateTerminalState();
	activate(id);
}

async function openTask(payload) {
	const data = await api("/api/term", { json: payload });
	openTerminal(data.id, data.title);
	showDock();
}

async function restoreTerminals() {
	const { sessions } = await api("/api/term");
	for (const s of sessions) openTerminal(s.id, s.title + (s.running ? "" : " ✓"));
	if (sessions.length) { for (const t of Object.values(terminals)) t.pane.hidden = true; activate(sessions[0].id); }
}

run(async () => {
	syncSiteForm();
	await loadSites();
	await route();
	await restoreTerminals();
	updateTerminalState();
});
