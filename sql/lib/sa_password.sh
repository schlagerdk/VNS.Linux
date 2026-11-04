#!/bin/bash
# Hjælpefunktioner til SQL Server sa-adgangskoden.
# Scripts i sql/ bruger en pladsholder for adgangskoden, som erstattes ved installation.

# Erstatter pladsholderen i alle .sh-filer under <sql-mappe>. $2 er adgangskoden i shell-quoted form.
sa_apply_quoted() {
	local _dir=$1
	SA_QUOTED=$2 find "${_dir}" -name '*.sh' -type f -exec \
		perl -pi -e 's/\x27\@SA_PASSWORD\@\x27/$ENV{SA_QUOTED}/g' {} +
}

# Spørger efter sa-adgangskoden, sætter SA_PASSWORD og opdaterer scripts i <sql-mappe>.
sa_prompt_and_apply() {
	local _dir=$1 _confirm
	while true; do
		read -r -s -p "Enter SQL Server sa password: " SA_PASSWORD; echo
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
	# Single-quote og escape ' så adgangskoden kan indeholde alle tegn
	local _escaped="'\\''"
	sa_apply_quoted "${_dir}" "'${SA_PASSWORD//\'/${_escaped}}'"
}
