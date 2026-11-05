#!/bin/bash
# Front-end libraries of the admin site, downloaded into <app>/static/vendor (not stored in the repository).
# Missing files are downloaded; existing ones are kept.
_xtermVersion=5.3.0
_xtermFitVersion=0.8.0
_monacoVersion=0.52.2
_cdn=https://cdn.jsdelivr.net/npm

# download_admin_vendor <app-folder>
download_admin_vendor() {
	local _vendor="$1/static/vendor"
	sudo mkdir -p "${_vendor}"
	if [[ ! -f ${_vendor}/xterm.js || ! -f ${_vendor}/xterm.css || ! -f ${_vendor}/xterm-addon-fit.js ]]; then
		echo "Download the terminal library (xterm.js ${_xtermVersion})"
		sudo curl -fsSL -o "${_vendor}/xterm.js" "${_cdn}/xterm@${_xtermVersion}/lib/xterm.js"
		sudo curl -fsSL -o "${_vendor}/xterm.css" "${_cdn}/xterm@${_xtermVersion}/css/xterm.css"
		sudo curl -fsSL -o "${_vendor}/xterm-addon-fit.js" "${_cdn}/xterm-addon-fit@${_xtermFitVersion}/lib/xterm-addon-fit.js"
	fi
	if [[ ! -f ${_vendor}/monaco/vs/editor/editor.main.js ]]; then
		echo "Download the code editor (Monaco ${_monacoVersion})"
		local _tmp
		_tmp=$(mktemp -d)
		if curl -fsSL "https://registry.npmjs.org/monaco-editor/-/monaco-editor-${_monacoVersion}.tgz" | tar xz -C "${_tmp}" --strip-components=2 package/min/vs; then
			sudo rm -rf "${_vendor}/monaco"
			sudo mkdir -p "${_vendor}/monaco"
			sudo mv "${_tmp}/vs" "${_vendor}/monaco/vs"
		else
			echo "Could not download Monaco; the admin site falls back to a plain text editor." >&2
		fi
		rm -rf "${_tmp}"
	fi
}
