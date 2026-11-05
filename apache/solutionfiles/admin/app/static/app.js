"use strict";

const csrf = document.querySelector("meta[name=csrf]").content;
const adminSite = document.querySelector("meta[name=admin-site]").content;
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

async function run(action) {
	try {
		return await action();
	} catch (err) {
		alert(err.message);
	}
}

// ------------------------------------------------------------------ views

function showView(name) {
	for (const view of ["sites", "files", "terminals"]) $("#view-" + view).hidden = view !== name;
	for (const button of document.querySelectorAll("nav button")) button.classList.toggle("active", button.dataset.view === name);
	if (name === "terminals") fitActive();
}
for (const button of document.querySelectorAll("nav button")) button.onclick = () => showView(button.dataset.view);

// ------------------------------------------------------------------ sites

async function loadSites() {
	const { sites } = await api("/api/sites");
	const body = $("#sites tbody");
	body.replaceChildren();
	for (const site of sites) {
		const actions = el("td", { className: "actions" });
		const add = (label, handler, className) => actions.append(el("button", { textContent: label, onclick: () => run(handler), className: className || "" }));
		add("Shell", () => openTask({ kind: "shell", site: site.name }));
		add("Files", () => openFiles(site.name));
		if (site.scripts.length) {
			const select = el("select", {}, el("option", { textContent: "Scripts…", value: "" }));
			for (const script of site.scripts) select.append(el("option", { textContent: script, value: script }));
			select.onchange = () => {
				const script = select.value;
				select.value = "";
				if (script) run(() => openTask({ kind: "script", site: site.name, script }));
			};
			actions.append(select);
		}
		if (site.service) {
			if (!site.admin) add(site.state === "active" ? "Stop" : "Start", () => serviceAction(site.name, site.state === "active" ? "stop" : "start"));
			add("Restart", () => serviceAction(site.name, "restart"));
		}
		add("Binding", () => changeBinding(site));
		if (!site.admin) add("Delete", () => deleteSite(site), "danger");

		const name = el("td", {}, el("strong", { textContent: site.name }));
		if (site.admin) name.append(el("span", { className: "muted", textContent: " (this admin site)" }));
		body.append(el("tr", {}, name,
			el("td", { textContent: site.binding }),
			el("td", { textContent: site.kind }),
			el("td", { textContent: site.port }),
			el("td", { className: site.state ? "state-" + site.state : "muted", textContent: site.service ? site.state : "no service" }),
			actions));
	}
}

async function serviceAction(name, action) {
	await api(`/api/sites/${encodeURIComponent(name)}/service`, { json: { action } });
	await loadSites();
}

function changeBinding(site) {
	const binding = prompt(`New binding for ${site.name}:`, site.binding);
	if (binding && binding !== site.binding) return openTask({ kind: "change_binding", site: site.name, binding });
}

function deleteSite(site) {
	if (prompt(`This deletes the site, its files, service and Apache config.\nType the site name to confirm: ${site.name}`) !== site.name) return;
	const certificates = confirm("Also revoke and remove the certificate?");
	return openTask({ kind: "delete_site", site: site.name, certificates });
}

$("#btn-refresh").onclick = () => run(loadSites);
$("#btn-server-shell").onclick = () => run(() => openTask({ kind: "shell" }));

const dialog = $("#dlg-site");
const siteForm = $("#form-site");
function syncSiteForm() {
	$("#csharp-options").hidden = siteForm.type.value !== "csharp";
}
siteForm.type.onchange = syncSiteForm;
siteForm.site.oninput = () => { if (!siteForm.binding.dataset.edited) siteForm.binding.value = siteForm.site.value; };
siteForm.binding.oninput = () => { siteForm.binding.dataset.edited = "1"; };
$("#btn-new-site").onclick = () => { siteForm.reset(); delete siteForm.binding.dataset.edited; syncSiteForm(); dialog.showModal(); };
$("#site-cancel").onclick = () => dialog.close();
siteForm.onsubmit = () => {
	const data = Object.fromEntries(new FormData(siteForm));
	data.kind = "create_site";
	run(() => openTask(data));
};

// ------------------------------------------------------------------ files

let currentPath = "";
let openFile = null;

const sizeText = (n) => (n < 1024 ? n + " B" : n < 1048576 ? Math.round(n / 1024) + " KB" : (n / 1048576).toFixed(1) + " MB");
const join = (a, b) => (a ? a + "/" + b : b);

async function openFiles(path) {
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
	if (currentPath) body.append(el("tr", {}, el("td", {}, el("a", { className: "link", textContent: "..", onclick: () => run(() => loadFiles(currentPath.split("/").slice(0, -1).join("/"))) })), el("td"), el("td"), el("td")));
	for (const item of data.items) {
		const full = join(currentPath, item.name);
		const link = el("a", { className: "link", textContent: (item.dir ? "📁 " : "") + item.name, onclick: () => run(() => (item.dir ? loadFiles(full) : editFile(full))) });
		const tools = el("td", { className: "actions" });
		if (!item.dir) tools.append(el("a", { className: "link", textContent: "Download", href: "/api/fs/download?path=" + encodeURIComponent(full) }));
		tools.append(el("a", { className: "link", textContent: "Rename", onclick: () => run(() => renameItem(full, item.name)) }));
		tools.append(el("a", { className: "link", textContent: "Delete", onclick: () => run(() => deleteItem(full, item.name)) }));
		body.append(el("tr", {}, el("td", {}, link), el("td", { textContent: item.dir ? "" : sizeText(item.size) }), el("td", { textContent: new Date(item.modified * 1000).toLocaleString() }), tools));
	}
}

async function editFile(path) {
	const data = await api("/api/fs/read?path=" + encodeURIComponent(path));
	openFile = path;
	$("#editor").hidden = false;
	$("#editor-path").textContent = path;
	$("#editor-text").value = data.content;
	$("#editor-status").textContent = "";
}

async function saveFile() {
	if (openFile === null) return;
	await api("/api/fs/save", { json: { path: openFile, content: $("#editor-text").value } });
	$("#editor-status").textContent = "Saved " + new Date().toLocaleTimeString();
}

async function renameItem(path, name) {
	const next = prompt("New name:", name);
	if (next && next !== name) {
		await api("/api/fs/rename", { json: { path, name: next } });
		await loadFiles(currentPath);
	}
}

async function deleteItem(path, name) {
	if (!confirm(`Delete ${name}? This cannot be undone.`)) return;
	await api("/api/fs/delete", { json: { path } });
	if (openFile === path) { openFile = null; $("#editor").hidden = true; }
	await loadFiles(currentPath);
}

async function createItem(kind, label) {
	const name = prompt(label);
	if (name) {
		await api("/api/fs/" + kind, { json: { path: currentPath, name } });
		await loadFiles(currentPath);
	}
}

$("#btn-new-file").onclick = () => run(() => createItem("newfile", "New file name:"));
$("#btn-new-folder").onclick = () => run(() => createItem("mkdir", "New folder name:"));
$("#btn-upload").onclick = () => $("#upload-input").click();
$("#upload-input").onchange = (event) => run(async () => {
	const form = new FormData();
	form.append("path", currentPath);
	for (const file of event.target.files) form.append("files", file);
	event.target.value = "";
	await api("/api/fs/upload", { method: "POST", body: form });
	await loadFiles(currentPath);
});
$("#editor-save").onclick = () => run(saveFile);
$("#editor-close").onclick = () => { openFile = null; $("#editor").hidden = true; };
$("#editor-text").onkeydown = (event) => {
	if ((event.ctrlKey || event.metaKey) && event.key === "s") { event.preventDefault(); run(saveFile); }
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

	const term = new Terminal({ cursorBlink: true, fontSize: 14, scrollback: 5000, theme: { background: "#000000" } });
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
		if (!t.exited && !confirm("The process is still running. Close and stop it?")) return;
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
	await loadSites();
	await restoreTerminals();
	updateTerminalState();
});
