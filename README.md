# VNS.Linux

Scripts for setting up an Ubuntu/Debian server for web hosting and maintaining it afterward: Apache with Let's Encrypt certificates, .NET 10, Python, optional Node.js, and SQL Server with backup tools.

Everything is deployed to `/vns` on the server.

## Installation

Run as a user with `sudo` privileges:

```bash
apt install git && git clone https://github.com/schlagerdk/VNS.Linux.git
```

Derefter:

```bash
cd VNS.Linux
./init.sh                  # base setup and deployment to /vns
/vns/apache/init.sh        # Apache, acme.sh, .NET 10, Python
/vns/install_nodejs.sh     # optional: Node.js via nvm
```

`init.sh` does the following:

1. Sets the timezone to `Europe/Copenhagen` and enables NTP
2. Updates all packages
3. Installs `curl git net-tools nano unzip ca-certificates rsync`
4. Copies the repository to `/vns` via `update.sh` and removes the cloned directory

## Updating

```bash
/vns/update.sh
```

Fetches the latest version from GitHub and synchronizes it to `/vns`. Local files are not deleted, and `apache/_portnumber.txt` is never overwritten, preserving existing port numbers.

## Contents

| Directory / file | Description |
|---|---|
| `init.sh` | Initial server setup |
| `update.sh` | Updates `/vns` from GitHub |
| `install_nodejs.sh` | Installs Node.js 22 via nvm |
| `apache/` | Apache setup and site creation/deletion |
| `sql/` | SQL Server installation, database, and backup scripts |

### Apache (`apache/`)

- `init.sh` installs Apache (with `ssl rewrite headers proxy proxy_http proxy_wstunnel`), acme.sh (Let's Encrypt, automatic renewal via cron), the .NET 10 SDK and ASP.NET Core runtime, Python 3, and `dotnet-outdated`.
- `create_site.sh [folder] [html|python|csharp] [umbraco|empty] [umbraco-version] [project-name] [binding]` asks for the site folder and type. For C# sites, it asks for the project name (default: folder name), then asks for the DNS binding (default: folder name when it is a fully qualified domain name). The DNS name must already resolve to the server for Let's Encrypt certificate issuance.
- `change_binding.sh [folder] [new-domain]` changes an existing site's Apache binding and issues a certificate for the new domain. It revokes and removes the old certificate after the new binding is active, unless another site still uses the old domain. DNS for the new domain must point to the server and HTTP port 80 must be reachable.
- C# sites get production deploy scripts in `<site>/scripts`. Each has a settings block at the top (`PROD_IP`, `PROD_USER`, `PROD_PASSWORD` or an SSH key, `PROD_FOLDER`). Password login needs `sshpass`; the login must be root or have passwordless sudo, and `apache/init.sh` must have been run on the prod server.
  - `publish_prod.sh` uploads the existing `_Publish` output (run `publish.sh` first; nothing is published locally) to `/var/www/<PROD_FOLDER>/_Publish` on prod. It creates the systemd service, Apache config and certificate there if missing (set `PROD_BINDING`, and make DNS point to prod first). `appsettings*.json`, media, `umbraco/Data` and logs on prod are kept; `UmbracoApplicationUrl` is set to the prod binding.
  - `sync_prod.sh` (Umbraco) mirrors `uSync`, `Views`, `wwwroot/css`, `wwwroot/scripts` and `wwwroot/media` to prod.
  - `clear_logs_prod.sh` (Umbraco) stops the prod service, deletes `umbraco/Logs` and starts it again.
- Example: create a site with folder `narhat.dk` and temporary binding `narhat.linux-01.vns.dk`; later run `/vns/apache/change_binding.sh narhat.dk narhat.dk` once `narhat.dk` resolves to the server.
- `delete_site.sh [site] [y|n]` removes a site, service, and optionally its certificates.
- `solutionfiles/` contains templates for `html`, `python`, and `csharp` (including an Umbraco template).

### SQL Server (`sql/`)

- `install_server.sh` installs the latest SQL Server available for the server's Ubuntu version (currently 2025 on Ubuntu 22.04/24.04), along with the `sqlcmd` tools. Already-installed components are skipped, so the script can also be used to add only what's missing.
- After installing SQL Server and its tools, `install_server.sh` prompts for the `sa` password entered in `mssql-conf setup` and inserts it into the scripts in `sql/` (replacing the `@SA_PASSWORD@` placeholder). The password is stored only locally on the server, and `update.sh` preserves it during updates.
- `update_server.sh` updates SQL Server (and `sqlcmd`) to the latest version in the configured repository and restarts the service. Back up first. Switching to a new major version (e.g. 2022 → 2025) requires a new installation with `install_server.sh`.
- `uninstall_server.sh` removes SQL Server, the `sqlcmd` tools, and Microsoft's apt repositories. Databases in `/var/opt/mssql` are preserved unless you explicitly type `DELETE` when prompted.
- `create_db.sh`, `drop_db.sh`, `copy_db.sh`, `restore_db.sh`, `list_dbs.sh`, and `list_backups.sh` are for day-to-day administration. The create, copy, and restore scripts prompt for and confirm a password for the new SQL login; the password is not stored in the SQL template files.
- `cron/` contains scheduled jobs (backups, log shrinking, etc.), enabled with `sql/cron/add_to_crontab.sh`.

## Requirements

- Ubuntu/Debian with `apt`
- A user with `sudo` privileges
- Internet access
