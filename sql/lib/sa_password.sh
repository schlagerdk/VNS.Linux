#!/bin/bash
# Helpers for the SQL Server sa password.
# Scripts in sql/ use a password placeholder that is replaced during installation.

# Replaces the placeholder in all .sh files under <sql-directory>. $2 is the password in shell-quoted form.
sa_apply_quoted() {
	local _dir=$1
	SA_QUOTED=$2 find "${_dir}" -name '*.sh' -type f -exec \
		perl -pi -e 's/\x27\@SA_PASSWORD\@\x27/$ENV{SA_QUOTED}/g' {} +
}

# Prompts for the sa password, sets SA_PASSWORD, and updates scripts in <sql-directory>.
sa_prompt_and_apply() {
	local _dir=$1 _confirm
	while true; do
		read -r -s -p "Enter the sa password you set during SQL Server setup so the SQL scripts can be updated: " SA_PASSWORD; echo
		read -r -s -p "Repeat password: " _confirm; echo
		if [[ -z ${SA_PASSWORD} ]]; then
			echo "Password cannot be empty" >&2
		elif [[ ${SA_PASSWORD} != "${_confirm}" ]]; then
			echo "Passwords do not match" >&2
		elif ((${#SA_PASSWORD} < 8)); then
			echo "SQL Server requires at least 8 characters" >&2
		else
			break
		fi
	done
	# Single-quote and escape ' so the password can contain any character
	local _escaped="'\\''"
	sa_apply_quoted "${_dir}" "'${SA_PASSWORD//\'/${_escaped}}'"
}
