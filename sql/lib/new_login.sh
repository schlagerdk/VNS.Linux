#!/bin/bash

prompt_new_login_password() {
	local _confirm
	while true; do
		read -r -s -p "Enter password for the new SQL login: " NEW_LOGIN_PASSWORD || return 1
		echo
		read -r -s -p "Repeat password: " _confirm || return 1
		echo
		if [[ -z ${NEW_LOGIN_PASSWORD} ]]; then
			echo "Password cannot be empty" >&2
		elif [[ ${NEW_LOGIN_PASSWORD} != "${_confirm}" ]]; then
			echo "Passwords do not match" >&2
		elif ((${#NEW_LOGIN_PASSWORD} < 8)); then
			echo "Password must be at least 8 characters" >&2
		elif [[ ${NEW_LOGIN_PASSWORD} == *$'\r'* ]]; then
			echo "Password cannot contain carriage returns" >&2
		else
			return 0
		fi
	done
}

render_sql_template() {
	local _template=$1 _output=$2
	shift 2

	local _escapedPassword
	_escapedPassword=$(printf '%s' "${NEW_LOGIN_PASSWORD}" | sed "s/'/''/g")
	local _sqlPassword="'${_escapedPassword}'"
	sed "$@" "${_template}" |
		SQL_LOGIN_PASSWORD="${_sqlPassword}" perl -pe \
			's/\@NEW_LOGIN_PASSWORD\@/$ENV{SQL_LOGIN_PASSWORD}/g' >"${_output}"
}
