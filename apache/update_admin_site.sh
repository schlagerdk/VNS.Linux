#!/bin/bash
# Update an existing admin site to the code in /vns (run update.sh first to fetch the latest version).
# The login (config.json), the virtual environment and the downloaded terminal library are kept.
# Usage: update_admin_site.sh [folder]
set -euo pipefail
cd "$(dirname "$0")"
source lib/common.sh
source lib/admin_vendor.sh

if [[ ${1:-} == "--update-csharp-publish-scripts" ]]; then
	sudo python3 - "${_rootPath}" "${_apacheDir}" <<'PY'
import re
import shlex
import sys
from pathlib import Path

root, apache = map(Path, sys.argv[1:])
helper = shlex.quote(str(apache / "lib/dotnet.sh"))
local_source = f'source {helper}\n'
production_source = f'source {helper}\n'
production_runtime = (
    'if ((_local)); then\n'
    '\t_runtimeIdentifier=$(linux_runtime_identifier)\n'
    'else\n'
    '\t_runtimeIdentifier=$(linux_runtime_identifier "$(remote uname -m)")\n'
    'fi\n'
)
planned = []
unchanged = []


def update_publish_script(path, production):
    if path.is_symlink():
        raise RuntimeError(f"Refusing to modify symlink: {path}")
    original = path.read_text()
    text = original
    publish_lines = list(re.finditer(r"(?m)^[ \t]*dotnet publish\b[^\n]*$", text))
    if len(publish_lines) != 1:
        raise RuntimeError(f"Expected one dotnet publish command in {path}")

    match = publish_lines[0]
    command = match.group()
    if "--runtime" not in command:
        if "--self-contained false" not in command:
            raise RuntimeError(f"Unrecognized framework-dependent publish command in {path}")
        command = command.replace(
            "--self-contained false",
            ('--runtime "${_runtimeIdentifier}"' if production
             else '--runtime "$(linux_runtime_identifier)"') + " --self-contained false",
        )
    if "CustomAfterMicrosoftCommonTargets=" not in command:
        command += " -p:CustomAfterMicrosoftCommonTargets=" + shlex.quote(str(apache / "lib/publish.targets"))
    text = text[:match.start()] + command + text[match.end():]

    if production:
        remote_definition = re.search(r"(?m)^remote\(\) \{[^\n]*\}$", text)
        if not remote_definition:
            raise RuntimeError(f"Could not locate remote command in {path}")
        if "_runtimeIdentifier=$(linux_runtime_identifier)" not in text:
            text = text[:remote_definition.end()] + "\n" + production_runtime + text[remote_definition.end():]
            remote_definition = re.search(r"(?m)^remote\(\) \{[^\n]*\}$", text)
        if not re.search(r"""(?m)^source .*/lib/dotnet\.sh["']?$""", text):
            text = text[:remote_definition.start()] + production_source + text[remote_definition.start():]
    else:
        publish = re.search(r"(?m)^([ \t]*)dotnet publish\b[^\n]*$", text)
        prefix = publish.group(1)
        additions = ""
        if not re.search(r"""(?m)^source .*/lib/dotnet\.sh["']?$""", text):
            additions += prefix + local_source
        if not re.search(r'(?m)^rm -rf "\$\{_publishFolder\}/runtimes"$', text):
            additions += prefix + 'rm -rf "${_publishFolder}/runtimes"\n'
        if additions:
            text = text[:publish.start()] + additions + text[publish.start():]

    (unchanged if text == original else planned).append((path, text))


for site in sorted(root.iterdir()):
    if not site.is_dir() or not any(site.glob("*.csproj")):
        continue
    scripts = site / "scripts"
    for name in ("publish.sh", "publish_local.sh"):
        path = scripts / name
        if path.is_file():
            update_publish_script(path, False)
    path = scripts / "publish_prod.sh"
    if path.is_file():
        update_publish_script(path, True)

if not planned and not unchanged:
    print(f"No C# publish scripts found in {root}.")
else:
    for path, text in planned:
        path.write_text(text)
        print(f"Updated {path}")
    for path, _ in unchanged:
        print(f"Already current: {path}")
    print(f"Updated {len(planned)} script(s); {len(unchanged)} already current.")
PY
	exit
fi

_site=${1:-}

_found=()
for _dir in "${_rootPath}"/*/; do
	_name=$(basename "${_dir}")
	if sudo test -f "${_dir}app/manage.py" && sudo test -f "${_dir}app/config.json"; then
		_found+=("${_name}")
	fi
done

if [[ -z ${_site} ]]; then
	if ((${#_found[@]} == 0)); then
		echo "No admin site found in ${_rootPath}. Use install_admin_site.sh." >&2
		exit 1
	elif ((${#_found[@]} == 1)); then
		_site=${_found[0]}
	else
		echo "Admin sites: ${_found[*]}"
		ask _site "Enter admin site folder name"
	fi
fi

_isAdmin=0
for _name in "${_found[@]}"; do [[ ${_name} == "${_site}" ]] && _isAdmin=1; done
if ((_isAdmin == 0)); then
	echo "${_site} is not an admin site created by install_admin_site.sh." >&2
	exit 1
fi

set_site_vars
echo "Update the app files"
sudo rsync -a --delete \
	--exclude /config.json --exclude /static/vendor/ --exclude /__pycache__/ \
	"${_solutionFolder}/admin/app/" "${_siteRoot}/app/"
sudo chown -R root:root "${_siteRoot}/app"
sudo chmod 700 "${_siteRoot}/app"

download_admin_vendor "${_siteRoot}/app"

echo "Update the Python packages"
sudo "${_siteRoot}/venv/bin/pip" install --quiet -r "${_siteRoot}/app/requirements.txt"

echo "Update the scripts"
install_scripts "${_solutionFolder}/admin/scripts"

echo "Restart the admin site"
# The service uses KillMode=process, so terminals opened in the admin site keep running
sudo systemctl restart "${_site}"
echo "Admin site '${_site}' updated."
