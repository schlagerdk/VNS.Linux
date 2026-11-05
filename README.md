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

Fetches the latest version from GitHub and synchronizes it to `/vns`. Local files are not deleted. Apache site ports are assigned automatically to the first available port starting at 4000, and a port is reusable after its site is deleted.

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
- `create_site.sh [folder] [html|python|csharp] [umbraco|empty] [umbraco-version] [project-name] [binding] [publish-folder] [Debug|Release]` asks for the site folder and type. For C# sites, it asks for the project name (default: folder name), then asks for the DNS binding (default: folder name when it is a fully qualified domain name). For C# sites it also asks for the local publish folder (default `_publish`, e.g. `_test`) and the build configuration (Debug or Release; Release also sets the service environment to `Production`, Debug to `Test`). The DNS name must already resolve to the server for Let's Encrypt certificate issuance.
- `change_binding.sh [folder] [new-domain]` changes an existing site's Apache binding and issues a certificate for the new domain. It revokes and removes the old certificate after the new binding is active, unless another site still uses the old domain. DNS for the new domain must point to the server and HTTP port 80 must be reachable.
- `prod_action.sh <folder> <issue_certificate|change_env>` issues a new certificate or changes the environment (Development/Test/Staging/Production) of the production site.
- `test_action.sh <folder> <issue_certificate|change_env>` does the same for the test (main) site, using its Apache config and service.
- `change_binding_prod.sh [folder] [new-domain]` does the same for a site's production environment (read from `scripts/publish_prod.sh`), on this server or over SSH on the remote one, and updates `PROD_BINDING` in `publish_prod.sh`.
- C# sites get `publish.sh` (publish into the local publish folder and restart the site; `publish.sh Release` overrides the build configuration; the service is always started again, also if the publish fails), for Umbraco `clear_logs.sh`, and `create_prod.sh`.
- `scripts/create_prod.sh` adds a production target (remote over SSH, or a local folder). It asks for the settings, renames `publish.sh` and `clear_logs.sh` to `publish_local.sh` and `clear_logs_local.sh`, creates `publish_prod.sh`, `clear_logs_prod.sh` and `sync_prod.sh` (Umbraco) with the answers filled in (mode 700, as they may contain the SSH password), and deletes itself. Production is always a Release build. Password login needs `sshpass`; the login must be root or have passwordless sudo, and `apache/init.sh` must have been run on the prod server. The settings block at the top of each prod script can be edited later (`PROD_IP`, `PROD_USER`, `PROD_PASSWORD` or an SSH key, `PROD_FOLDER`, `PROD_LOCAL_PATH`).
  - `publish_prod.sh` runs `dotnet publish -c Release` into a temporary folder (the local publish folder is untouched) and uploads it directly into `/var/www/<PROD_FOLDER>` on prod. It creates the systemd service, Apache config and certificate there if missing (set `PROD_BINDING`, and make DNS point to prod first). On the first publish `appsettings*.json` (with `ConnectionStrings` removed) and `web.config` are copied; later publishes never overwrite them. Media, `umbraco/Data` and logs on prod are kept too; `UmbracoApplicationUrl` is set to the prod binding.
  - `sync_prod.sh` (Umbraco) mirrors `uSync`, `Views`, `wwwroot/css`, `wwwroot/scripts` and `wwwroot/media` to prod.
  - With `PROD_LOCAL_PATH` set (e.g. `/var/www/_prod`; choose "local" in `create_prod.sh`) the scripts work against a local folder instead of SSH. `publish_prod.sh` then creates the service, Apache config and certificate on this server for that folder, exactly as on a remote prod (use a `PROD_FOLDER` and `PROD_BINDING` that differ from the test site; the script refuses to reuse an existing service that runs from another folder). If `PROD_LOCAL_PATH` is inside the project folder (e.g. `_prod`), `publish_prod.sh` adds it to the `.csproj` build excludes automatically. `sync_prod.sh` mirrors into that folder and `clear_logs_prod.sh` only deletes the log folder there.
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

### Admin site

`apache/install_admin_site.sh` creates a Flask website (served by gunicorn behind Apache with a certificate) for managing every site in `/var/www`. It asks for folder name, binding, username, password (min. 12 characters) and an optional IP/CIDR allowlist (empty = everyone).

- Sites overview with one card per site. Open a site to see its environments side by side: the site itself (test) and, if `scripts/` contains `publish_prod.sh`, its production site (read from the `PROD_*` settings; the password is never read). A production site on the same server gets its own state, start/stop/restart, logs, shell and files; a remote one shows host and binding.
- Each environment card has buttons for its scripts: Publish, Clear logs, Issue certificate and Change environment (Publish and Clear logs on test need the script in `scripts/`; Issue certificate and Change environment always work, via `apache/test_action.sh` for test and `apache/prod_action.sh` for production, on this server or over SSH). Cards with a local service also have Check service, Edit service and Edit config. *Check outdated* and *Sync to prod* sit at the top of the site next to *Delete everything*. Scripts with a button are left out of the "Run script…" list. Publish, Sync, Clear logs, Issue certificate, Stop, Restart and Restart Apache ask for confirmation.
- Buttons have icons and colour tones; Delete buttons are always alone at the far right.
- Files open in a Monaco editor (syntax colouring, Ctrl+S to save).
- Typing `exit` in a shell closes its terminal tab, and the modal closes when no tabs remain (script output tabs stay open).
- The ⚙ Settings menu in the header has server-wide actions: Restart Apache, Test Apache config, and a link to the admin site itself. The overview hides the admin site and the Apache default site (`/var/www/html`).
- Umbraco sites show the installed Umbraco version (read from the `.csproj`) as a badge; the colour depends on the major version. Each environment card also has a *Backoffice* link to `<binding>/umbraco`.
- Terminals open in a modal window (button or Ctrl+`), and files open in an editor modal (Esc asks before discarding changes). The user icon opens the profile, where the username and password can be changed, and log out.
- Sites overview with start/stop/restart, binding and port; run `create_site.sh`, `change_binding.sh` (per environment) and `delete_site.sh`; run the scripts in a site's `scripts/` folder; open a shell in a site. C# site details list the NuGet package references from their `.csproj` files and provide *Add package* and *Remove package* dialogs. Click a package to copy its name (requires HTTPS or localhost); `Microsoft.ICU.ICU4C.Runtime` is hidden from the list without changing the project. Everything runs in interactive terminals that survive a browser reload.
- Delete is scoped: *Delete test site*, *Delete production site* or *Delete everything* (`delete_site.sh <site> <y|n certs> [all|test|prod]`). Local production removes its service, Apache config and files. Remote production also removes its service, Apache config, logs, files and isolation root over SSH; deleting only the test site leaves production intact. Certificate removal is optional; on the remote server, a certificate still used by another enabled site is kept. After deleting production, `create_prod.sh` can be run again. Scripts in the dropdown are shown with friendly names.
- File browser confined to `/var/www`: edit, upload, download, create, rename, delete.
- Security: **the site runs as root.** Login is rate limited per IP, sessions are HttpOnly/Secure/SameSite=Strict with a 2 hour idle timeout, and all POSTs require a CSRF token. Use a long password and preferably the IP allowlist.
- `apache/init.sh` offers to run the installer at the end. To remove the admin site again (service, Apache config, optional certificate, files and login) run `apache/uninstall_admin_site.sh`; other sites are not touched.
- To update an installed admin site after `update.sh`, run `apache/update_admin_site.sh`. It replaces the app code, updates the Python packages and restarts the site, and keeps the login, the virtual environment and the terminal library.
- Change the password with `scripts/set_password.sh` in the admin site (this also signs out all sessions). The xterm.js assets (jsdelivr) and the Monaco editor (npm registry, ~13 MB) are downloaded into `static/vendor` when the site is installed or updated; without Monaco the file editor falls back to a plain textarea.

## Requirements

- Ubuntu/Debian with `apt`
- A user with `sudo` privileges
- Internet access
