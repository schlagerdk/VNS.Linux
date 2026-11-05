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
const views = ["sites", "files", "terminals"];
function showView(name) {
	if (!views.includes(name)) name = "sites";
	for (const view of views) $("#view-" + view).hidden = view !== name;
	for (const button of document.querySelectorAll("nav button")) button.classList.toggle("active", button.dataset.view === name);
	history.replaceState(null, "", "#" + name);
	if (name === "terminals") fitActive();
	if (name === "files" && !filesLoaded) { filesLoaded = true; run(() => loadFiles(currentPath)); }
}
for (const button of document.querySelectorAll("nav button")) button.onclick = () => showView(button.dataset.view);

// ------------------------------------------------------------------ sites

let allSites = [];
let filter = "all";
let query = "";

const kindLabel = { umbraco: "Umbraco", csharp: "C# / .NET", python: "Python", html: "HTML" };

async function loadSites() {
	allSites = (await api("/api/sites")).sites;
	renderSites();
}

function renderStats() {
	const count = (test) => allSites.filter(test).length;
	const chips = [
		["all", "All", allSites.length],
		["running", "Running", count((s) => s.state === "active")],
		["stopped", "Stopped", count((s) => s.service && s.state !== "active")],
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
	if (filter === "running" && site.state !== "active") return false;
	if (filter === "stopped" && !(site.service && site.state !== "active")) return false;
	if (kindLabel[filter] && site.kind !== filter) return false;
	const text = query.trim().toLowerCase();
	return !text || (site.name + " " + site.binding).toLowerCase().includes(text);
}

function renderSites() {
	renderStats();
	const box = $("#sites");
	box.replaceChildren();
	const visible = allSites.filter(matches);
	$("#no-sites").hidden = visible.length > 0;
	for (const site of visible) box.append(siteCard(site));
}

function siteCard(site) {
	const state = site.service ? site.state || "unknown" : "";
	const badges = el("div", { className: "badges" }, el("span", { className: "badge " + site.kind, textContent: kindLabel[site.kind] || site.kind }));
	if (site.admin) badges.append(el("span", { className: "badge admin", textContent: "this admin site" }));
	badges.append(el("span", { className: "badge " + (state ? "state-" + state : ""), textContent: state || "static" }));

	const binding = site.binding
		? el("a", { className: "link", textContent: site.binding, href: "https://" + site.binding + "/", target: "_blank", rel: "noopener noreferrer" })
		: el("span", { className: "muted", textContent: "—" });
	const meta = el("dl", { className: "meta" },
		el("dt", { textContent: "Binding" }), el("dd", {}, binding),
		el("dt", { textContent: "Folder" }), el("dd", { textContent: "/var/www/" + site.name }));
	if (site.port) meta.append(el("dt", { textContent: "Port" }), el("dd", { textContent: site.port }));

	const actions = el("div", { className: "actions" });
	const add = (label, handler, className, title) => actions.append(el("button", { textContent: label, title: title || "", className: "small " + (className || ""), onclick: () => run(handler) }));
	add("Shell", () => openTask({ kind: "shell", site: site.name }), "", "Open a terminal in the site folder");
	add("Files", () => openFiles(site.name));
	if (site.scripts.length) {
		const select = el("select", { className: "small", title: "Run a script from the site's scripts folder" }, el("option", { textContent: "Scripts…", value: "" }));
		for (const script of site.scripts) select.append(el("option", { textContent: script, value: script }));
		select.onchange = () => {
			const script = select.value;
			select.value = "";
			if (script) run(() => openTask({ kind: "script", site: site.name, script }));
		};
		actions.append(select);
	}
	if (site.service) {
		if (!site.admin) add(state === "active" ? "Stop" : "Start", () => serviceAction(site.name, state === "active" ? "stop" : "start"));
		add("Restart", () => serviceAction(site.name, "restart"));
	}
	add("Binding", () => changeBinding(site), "", "Change the domain");
	if (!site.admin) add("Delete", () => deleteSite(site), "danger");

	return el("article", { className: "site " + state + (site.admin ? " is-admin" : "") },
		el("div", { className: "site-head" }, el("span", { className: "dot " + state }), el("h3", { className: "site-title", textContent: site.name })),
		badges, meta, actions);
}

async function serviceAction(name, action) {
	await api(`/api/sites/${encodeURIComponent(name)}/service`, { json: { action } });
	toast(`${name}: ${action} done`, "ok");
	await loadSites();
}

async function changeBinding(site) {
	const binding = await ask({ title: "Change binding", message: `New domain for ${site.name}`, value: site.binding, ok: "Change" });
	if (binding && binding !== site.binding) await openTask({ kind: "change_binding", site: site.name, binding: binding.trim() });
}

async function deleteSite(site) {
	const typed = await ask({
		title: "Delete site", danger: true, ok: "Delete", match: site.name, placeholder: site.name,
		message: `This deletes the site, its files, service and Apache config.\nType "${site.name}" to confirm.`,
	});
	if (typed === null) return;
	const certificates = (await ask({ title: "Certificate", message: "Also revoke and remove the certificate?", input: false, ok: "Remove certificate" })) === true;
	await openTask({ kind: "delete_site", site: site.name, certificates });
}

$("#search").oninput = (event) => { query = event.target.value; renderSites(); };
$("#btn-refresh").onclick = () => run(loadSites);
$("#btn-server-shell").onclick = () => run(() => openTask({ kind: "shell" }));
document.addEventListener("keydown", (event) => {
	const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
	if (event.key === "/" && !typing && !$("#view-sites").hidden) { event.preventDefault(); $("#search").focus(); }
});
setInterval(() => {
	const busy = document.activeElement && $("#sites").contains(document.activeElement);
	if (!document.hidden && !$("#view-sites").hidden && !busy && !document.querySelector("dialog[open]")) loadSites().catch(() => {});
}, 15000);

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

async function openFiles(path) {
	filesLoaded = true;
	showView("files");
	await run(() => loadFiles(path));
}

async function loadFiles(path) {
	const data = await api("/api/fs/list?path=" + encodeURIComponent(path));
	currentPath = data.path;
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
	$("#editor").hidden = false;
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
	if (openFile === path) { openFile = null; setDirty(false); $("#editor").hidden = true; }
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
$("#editor-close").onclick = async () => {
	if (!(await discardOk())) return;
	openFile = null;
	setDirty(false);
	$("#editor").hidden = true;
};
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
	$("#term-count").textContent = count ? `(${count})` : "";
	$("#no-terminals").hidden = count > 0;
}

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
	showView("terminals");
	openTerminal(data.id, data.title);
}

async function restoreTerminals() {
	const { sessions } = await api("/api/term");
	for (const s of sessions) openTerminal(s.id, s.title + (s.running ? "" : " ✓"));
	if (sessions.length) { for (const t of Object.values(terminals)) t.pane.hidden = true; activate(sessions[0].id); }
}

run(async () => {
	syncSiteForm();
	showView(location.hash.slice(1));
	await loadSites();
	await restoreTerminals();
	updateTerminalState();
});
