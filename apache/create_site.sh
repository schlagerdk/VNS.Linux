#!/bin/bash
# Usage: create_site.sh [folder] [html|python|csharp] [umbraco|empty] [umbraco-version] [project-name] [binding] [publish-folder] [Debug|Release] [y|n certificate] [template-name]
set -euo pipefail
cd "$(dirname "$0")"
source lib/common.sh
source lib/dotnet.sh

trap 'echo "FAILED at ${BASH_SOURCE[0]}:${LINENO} (exit $?): ${BASH_COMMAND}" >&2; echo "Site is incomplete - run ./delete_site.sh ${_site:-<site>} n to clean up" >&2' ERR
trap 'release_port_reservation' EXIT
set -o errtrace

_defaultUmbracoVersion=18.3.1
_defaultUmbraco13Version=13.16.4
_dotNetVersion=net10.0

_site=${1:-}
_type=${2:-}
_csharpKind=${3:-}
_umbracoVersion=${4:-}
_projectName=${5:-}
_binding=${6:-}
_publishArg=${7:-}
_buildConfig=${8:-}
_createCertificate=${9:-}
_templateName=${10:-}
_templateProvided=n
[[ $# -lt 10 ]] || _templateProvided=y
ask _createCertificate "Create an HTTPS certificate? (y/n)" y
case "${_createCertificate,,}" in
	y | yes) _createCertificate=y ;;
	n | no) _createCertificate=n ;;
	*) echo "Certificate option must be y or n." >&2; exit 1 ;;
esac

ask _site "Enter folder name"
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

if [[ ${_type} == csharp ]]; then
	ask _projectName "Enter C# project name" "${_site}"
	validate_project_name "${_projectName}"
fi

if [[ -z ${_binding} && ${_site} == *.* ]]; then
	ask _binding "Enter site URL binding" "${_site}"
else
	ask _binding "Enter site URL binding"
fi
validate_binding "${_binding}"
_binding=${_binding,,}

set_site_vars

create_html() {
	_docRoot="${_siteRoot}"
	echo "Create html"
	sudo mkdir -p "${_docRoot}"
	render_to "${_solutionFolder}/html/site/index.html" "${_docRoot}/index.html"
	[[ ${_createCertificate} == n ]] || issue_certificate
	install_apache_config "${_solutionFolder}/html/apache.conf.tpl"
	install_scripts "${_solutionFolder}/common/scripts"
}

create_python() {
	_docRoot="${_siteRoot}/wwwroot"
	allocate_ports
	sudo mkdir -p "${_siteRoot}/app" "${_docRoot}" /var/lib/vns-roots/${_site}
	[[ ${_createCertificate} == n ]] || issue_certificate

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
	local _kindFolder="${_solutionFolder}/csharp"
	local _project="${_siteRoot}/${_projectName}.csproj"
	ask _csharpKind "C# site kind (umbraco/empty)" umbraco
	case "${_csharpKind}" in
		umbraco | empty) ;;
		*) echo "C# site kind must be umbraco or empty." >&2; exit 1 ;;
	esac
	ask _publishArg "Enter local publish folder" "${_publishFolder}"
	validate_publish_folder "${_publishArg}"
	_publishFolder=${_publishArg}
	ask _buildConfig "Build configuration (Debug/Release)" Debug
	case "${_buildConfig}" in
		Debug) _environment=Test ;;
		Release) _environment=Production ;;
		*) echo "Build configuration must be Debug or Release" >&2; exit 1 ;;
	esac
	_docRoot="${_siteRoot}/${_publishFolder}/wwwroot"

	if [[ ${_csharpKind} == umbraco ]]; then
		ask _umbracoVersion "Set umbraco version" "${_defaultUmbracoVersion}"
		_kindFolder="${_solutionFolder}/csharp/umbraco"
		if [[ ${_umbracoVersion} == 13 || ${_umbracoVersion} == 13.* ]]; then
			[[ ${_umbracoVersion} != 13 ]] || _umbracoVersion=${_defaultUmbraco13Version}
			if [[ ! ${_umbracoVersion} =~ ^13\.([0-9]+)\.([0-9]+)$ ]] ||
				((10#${BASH_REMATCH[1]} < 3 || (10#${BASH_REMATCH[1]} == 3 && 10#${BASH_REMATCH[2]} < 2))); then
				echo "Umbraco 13 requires version 13.3.2 or newer for VNS.Umbraco." >&2
				exit 1
			fi
			_dotNetVersion=net8.0
			ensure_dotnet_channel 8.0
		fi
	else
		[[ ${_templateProvided} == y ]] || ask _templateName "Template name (optional)" web
		_templateName=${_templateName:-web}
		if [[ ! ${_templateName} =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
			echo "Invalid template name. Use letters, numbers, dots, hyphens or underscores." >&2
			exit 1
		fi
	fi

	allocate_ports
	sudo mkdir -p "${_siteRoot}" "${_docRoot}"
	[[ ${_createCertificate} == n ]] || issue_certificate
	sudo mkdir -p /var/lib/vns-roots/${_site}

	install_scripts "${_solutionFolder}/common/scripts"
	install_scripts "${_solutionFolder}/common/service-scripts"
	install_scripts "${_solutionFolder}/csharp/scripts"
	install_scripts "${_kindFolder}/scripts"

	cd "${_siteRoot}"
	if [[ ${_csharpKind} == umbraco ]]; then
		if [[ ${_dotNetVersion} == net8.0 ]]; then
			printf '%s\n' '{"sdk":{"version":"8.0.100","rollForward":"latestFeature","allowPrerelease":false}}' | sudo tee global.json >/dev/null
		fi
		create_umbraco_project
	else
		echo "Create .NET project from template: ${_templateName}"
		sudo dotnet new "${_templateName}" --name "${_projectName}" --output . --force
		if [[ ! -f ${_project} ]]; then
			echo "Template '${_templateName}' did not create ${_projectName}.csproj; a runnable C# site project is required." >&2
			exit 1
		fi
		exclude_from_build "${_project}" "${_publishFolder}"
		configure_project_dotnet "${_project}"
		sudo dotnet publish "${_project}" -c "${_buildConfig}" -p:PublishDir="${_publishFolder}" --self-contained false --nologo
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
	sudo find "${_siteRoot}" -maxdepth 1 -name '*.csproj' -delete

	echo "Remove central package management from template (our csproj sets versions itself)"
	sudo rm -f Directory.Packages.props Directory.Build.props Directory.Build.targets

	echo "Add Program.cs and project file (replace umbraco and .NET version)"
	sudo cp -f "${_files}/Program.cs" "${_siteRoot}/Program.cs"
	sudo cp -f "${_files}/UmbracoCoreProjectName.csproj" "${_project}"
	sudo sed -i "s|UmbracoCoreInstallVersion|${_umbracoVersion}|g; s|DotNetVersion|${_dotNetVersion}|g" "${_project}"
	exclude_from_build "${_project}" "${_publishFolder}"

	if [[ ${_umbracoVersion%%.*} -ge 17 ]]; then
			sudo dotnet add package Umbraco.Cms.DevelopmentMode.Backoffice -v "${_umbracoVersion}"
	fi

	echo "Install VNS Umbraco and uSync package"
	if [[ ${_dotNetVersion} == net8.0 ]]; then
		sudo dotnet add package VNS.Umbraco -v 13.0.0
		sudo dotnet add package uSync -v 13.0.0
	else
		sudo dotnet add package VNS.Umbraco
		sudo dotnet add package uSync
	fi

	echo "Delete default wwwroot, views and default settings"
	sudo rm -rf wwwroot Views Properties .gitignore appsettings.json appsettings.Development.json \
		appsettings-schema.json appsettings-schema.Umbraco.Cms.json umbraco-package-schema.json

	echo "Publish site"
	sudo dotnet publish "${_project}" -c "${_buildConfig}" -p:PublishDir="${_publishFolder}" --self-contained false --nologo

	echo "Delete bin and schema files"
	sudo rm -rf bin
	sudo rm -f appsettings-schema.json appsettings-schema.Umbraco.Cms.json appsettings-schema.usync.json umbraco-package-schema.json
	sudo rm -f "${_publish}"/{appsettings-schema.json,appsettings-schema.Umbraco.Cms.json,appsettings-schema.usync.json,umbraco-package-schema.json}

	echo "Add appsettings.json and web.config"
	sudo cp -f "${_files}/appsettings.json" "${_publish}/appsettings.json"
	sudo sed -i "s|FolderName|${_binding}|g; s|UUID|$(uuidgen)|g" "${_publish}/appsettings.json"
	if [[ ${_createCertificate} == n ]]; then
		configure_umbraco_https "${_publish}/appsettings.json" "${_binding}" n >/dev/null
	fi
	sudo cp -f "${_files}/web.config" "${_publish}/web.config"
	sudo sed -i "s|UmbracoCoreProjectName|${_projectName}|g" "${_publish}/web.config"

	echo "Add default wwwroot, views and uSync"
	sudo cp -rf "${_files}/wwwroot" "${_files}/Views" "${_files}/uSync" "${_publish}/"
}

case "${_type}" in
	html) create_html ;;
	python) create_python ;;
	csharp) create_csharp ;;
	*) echo "Unknown site type: ${_type}" >&2; exit 1 ;;
esac

release_port_reservation
_scheme=http
[[ ${_createCertificate} == n ]] || _scheme=https
echo "Site created: ${_scheme}://${_binding}/ (folder: ${_site})"
if [[ ${_type} == csharp ]]; then
	echo "Run ${_siteRoot}/scripts/create_prod.sh when you want a production target for this site."
fi
