#!/bin/bash
# Usage: create_site.sh [site] [html|python|csharp] [umbraco|empty] [umbraco-version]
set -euo pipefail
cd "$(dirname "$0")"
source lib/common.sh

trap 'echo "FAILED at ${BASH_SOURCE[0]}:${LINENO} (exit $?): ${BASH_COMMAND}" >&2; echo "Site is incomplete - run ./delete_site.sh ${_site:-<site>} n to clean up" >&2' ERR
set -o errtrace

_defaultUmbracoVersion=18.3.0
_dotNetVersion=net10.0

_site=${1:-}
_type=${2:-}
_csharpKind=${3:-}
_umbracoVersion=${4:-}

ask _site "Enter site name"
validate_site_name "${_site}"

if [[ -z ${_type} ]]; then
	echo "Site type:"
	echo "  1) html"
	echo "  2) python"
	echo "  3) csharp"
	read -r -p "Choose [1]: " _choice
	case "${_choice:-1}" in
		1 | html) _type=html ;;
		2 | python) _type=python ;;
		3 | csharp | c#) _type=csharp ;;
		*) echo "Unknown choice: ${_choice}" >&2; exit 1 ;;
	esac
fi

set_site_vars

create_html() {
	_docRoot="${_siteRoot}"
	echo "Create html"
	sudo mkdir -p "${_docRoot}"
	render_to "${_solutionFolder}/html/site/index.html" "${_docRoot}/index.html"
	issue_certificate
	install_apache_config "${_solutionFolder}/html/apache.conf.tpl"
	install_scripts "${_solutionFolder}/common/scripts"
}

create_python() {
	_docRoot="${_siteRoot}/wwwroot"
	allocate_ports
	sudo mkdir -p "${_siteRoot}/app" "${_docRoot}" /var/lib/vns-roots/${_site}
	issue_certificate

	echo "Create python app and virtual environment"
	for _file in "${_solutionFolder}"/python/app/*; do
		render_to "${_file}" "${_siteRoot}/app/$(basename "${_file}")"
	done
	sudo python3 -m venv "${_siteRoot}/venv"
	sudo "${_siteRoot}/venv/bin/pip" install -r "${_siteRoot}/app/requirements.txt"
	sudo chown -R www-data:www-data "${_siteRoot}/app" "${_siteRoot}/venv"

	install_scripts "${_solutionFolder}/common/scripts"
	install_scripts "${_solutionFolder}/common/service-scripts"
	install_scripts "${_solutionFolder}/python/scripts"

	install_apache_config "${_solutionFolder}/python/apache.conf.tpl"
	install_service "${_solutionFolder}/python/service.tpl"
}

create_csharp() {
	local _kindFolder="${_solutionFolder}/csharp" _project="${_siteRoot}/${_site}.csproj"
	ask _csharpKind "C# site kind (umbraco/empty)" umbraco
	_docRoot="${_siteRoot}/${_publishFolder}/wwwroot"

	if [[ ${_csharpKind} == umbraco ]]; then
		ask _umbracoVersion "Set umbraco version" "${_defaultUmbracoVersion}"
		_kindFolder="${_solutionFolder}/csharp/umbraco"
	fi

	allocate_ports
	sudo mkdir -p "${_siteRoot}" "${_docRoot}"
	issue_certificate
	sudo mkdir -p /var/lib/vns-roots/${_site}

	install_scripts "${_solutionFolder}/common/scripts"
	install_scripts "${_solutionFolder}/common/service-scripts"
	install_scripts "${_solutionFolder}/csharp/scripts"
	install_scripts "${_kindFolder}/scripts"

	cd "${_siteRoot}"
	if [[ ${_csharpKind} == umbraco ]]; then
		create_umbraco_project
	else
		echo "Create empty ASP.NET Core project"
		sudo dotnet new web --name "${_site}" --output . --force
		sudo dotnet publish -c Debug -p:PublishDir="${_publishFolder}" --self-contained false --nologo
		sudo rm -rf bin obj
	fi
	cd "${_apacheDir}"

	# The service runs as www-data and needs to write inside the publish folder only
	sudo chown -R www-data:www-data "${_siteRoot}/${_publishFolder}"

	local _conf="${_kindFolder}/apache.conf.tpl" _service="${_kindFolder}/service.tpl"
	[[ -f ${_conf} ]] || _conf="${_solutionFolder}/csharp/apache.conf.tpl"
	[[ -f ${_service} ]] || _service="${_solutionFolder}/csharp/service.tpl"
	install_apache_config "${_conf}"
	install_service "${_service}"
}

create_umbraco_project() {
	local _files="${_solutionFolder}/csharp/umbraco/project" _publish="${_siteRoot}/${_publishFolder}"

	echo "Create new umbraco instance in folder"
	sudo dotnet new install "Umbraco.Templates@${_umbracoVersion}" --force
	sudo dotnet new umbraco --force

	echo "Remove central package management from template (our csproj sets versions itself)"
	sudo rm -f Directory.Packages.props Directory.Build.props Directory.Build.targets

	echo "Add Program.cs and project file (replace umbraco and .NET version)"
	sudo cp -f "${_files}/Program.cs" "${_siteRoot}/Program.cs"
	sudo cp -f "${_files}/UmbracoCoreProjectName.csproj" "${_project}"
	sudo sed -i "s|UmbracoCoreInstallVersion|${_umbracoVersion}|g; s|DotNetVersion|${_dotNetVersion}|g" "${_project}"

	# Needed for umbraco version 17+
	sudo dotnet add package Umbraco.Cms.DevelopmentMode.Backoffice -v "${_umbracoVersion}"

	echo "Install VNS Umbraco and uSync package"
	sudo dotnet add package VNS.Umbraco
	sudo dotnet add package uSync

	echo "Delete default wwwroot, views and default settings"
	sudo rm -rf wwwroot Views Properties .gitignore appsettings.json appsettings.Development.json \
		appsettings-schema.json appsettings-schema.Umbraco.Cms.json umbraco-package-schema.json

	echo "Publish site"
	sudo dotnet publish -c Debug -p:PublishDir="${_publishFolder}" --self-contained false --nologo

	echo "Delete bin and schema files"
	sudo rm -rf bin
	sudo rm -f appsettings-schema.json appsettings-schema.Umbraco.Cms.json appsettings-schema.usync.json umbraco-package-schema.json
	sudo rm -f "${_publish}"/{appsettings-schema.json,appsettings-schema.Umbraco.Cms.json,appsettings-schema.usync.json,umbraco-package-schema.json}

	echo "Add appsettings.json and web.config"
	sudo cp -f "${_files}/appsettings.json" "${_publish}/appsettings.json"
	sudo sed -i "s|FolderName|${_site}|g; s|UUID|$(uuidgen)|g" "${_publish}/appsettings.json"
	sudo cp -f "${_files}/web.config" "${_publish}/web.config"
	sudo sed -i "s|UmbracoCoreProjectName|${_site}|g" "${_publish}/web.config"

	echo "Add default wwwroot, views and uSync"
	sudo cp -rf "${_files}/wwwroot" "${_files}/Views" "${_files}/uSync" "${_publish}/"
}

case "${_type}" in
	html) create_html ;;
	python) create_python ;;
	csharp) create_csharp ;;
	*) echo "Unknown site type: ${_type}" >&2; exit 1 ;;
esac

echo "Site created: https://${_site}/"
