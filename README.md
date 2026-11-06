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

- New site offers an optional HTTPS certificate (enabled by default). Choose HTTP-only to skip certificate issuance; later, Issue certificate enables HTTPS and redirects HTTP to port 443, including updating Umbraco's HTTPS settings. The CLI accepts `y` or `n` as the ninth argument to `create_site.sh`.
- `init.sh` installs Apache (with `ssl rewrite headers proxy proxy_http proxy_wstunnel`), acme.sh (Let's Encrypt, automatic renewal via cron), the .NET 8 and 10 SDKs and ASP.NET Core runtimes, Python 3, and `dotnet-outdated`.
- `create_site.sh [folder] [html|python|csharp] [umbraco|empty] [umbraco-version] [project-name] [binding] [publish-folder] [Debug|Release]` asks for the site folder and type. For C# sites, it asks for the project name (default: folder name), then asks for the DNS binding (default: folder name when it is a fully qualified domain name). For C# sites it also asks for the local publish folder (default `_publish`, e.g. `_test`) and the build configuration (Debug or Release; Release also sets the service environment to `Production`, Debug to `Test`). The DNS name must already resolve to the server for Let's Encrypt certificate issuance.
- `change_binding.sh [folder] [new-domain]` changes an existing site's Apache binding and issues a certificate for the new domain. It revokes and removes the old certificate after the new binding is active, unless another site still uses the old domain. DNS for the new domain must point to the server and HTTP port 80 must be reachable.
- For C# kind `empty`, the New site dialog replaces Umbraco version with an optional Template name. An empty value keeps the `web` template; otherwise creation runs `dotnet new <template-name> --name <project-name> --output .` in the site folder. The template must already be installed and create a runnable C# site project. CLI users can pass the template name as the tenth argument (after the certificate option).
- New C# sites offer an Environment selector (Development, Test, Staging or Production; default Test), independent of Debug/Release. The selected value sets both `ASPNETCORE_ENVIRONMENT` and `DOTNET_ENVIRONMENT` in the site service. CLI users can pass it as the eleventh argument; omitting it preserves the Debug-to-Test / Release-to-Production defaults.
- Create production site also offers Development, Test, Staging or Production (default Production), for both local and remote targets. CLI setup prompts for the same choice. The value is saved as `PROD_ENVIRONMENT` and applied when creating the target service; the build remains Release regardless of environment.
- Templates targeting `net8.0` (including `vnswebapi`) automatically install/select .NET 8 using a site-local `global.json`. Creation and generated local/production publish scripts explicitly publish `<project-name>.csproj`, avoiding template solution references that normalize hyphens to underscores. Existing sites keep their installed publish scripts; update those scripts separately.
- Umbraco 13 uses `net8.0` and a site-local `global.json` selecting the .NET 8 SDK, including on later publishes. Creation installs the .NET 8 SDK/runtime if missing; production servers also need .NET 8 (run the updated `init.sh`). VNS.Umbraco and uSync are pinned to their compatible `13.0.0` releases; Umbraco must be at least `13.3.2`. Entering `13` selects `13.16.4`; prefer a recent patch over old releases with known vulnerabilities. Other Umbraco versions retain .NET 10.
- The bundled `Program.cs`, `appsettings.json`, `wwwroot`, `Views` and `uSync` are copied into new Umbraco sites only for version 18 or newer. Earlier versions keep the files generated by their own Umbraco template, including application settings during publish. HTTP-only setup still adjusts the published Umbraco HTTPS settings. Package installation and production content sync are unchanged.
- `prod_action.sh <folder> <issue_certificate|change_env>` issues a new certificate or changes the environment (Development/Test/Staging/Production) of the production site.
- `test_action.sh <folder> <issue_certificate|change_env>` does the same for the test (main) site, using its Apache config and service.
- `change_binding_prod.sh [folder] [new-domain]` does the same for a site's production environment (read from `scripts/publish_prod.sh`), on this server or over SSH on the remote one, and updates `PROD_BINDING` in `publish_prod.sh`.
- C# sites get `publish.sh` (publish into the local publish folder and restart the site; `publish.sh Release` overrides the build configuration; the service is always started again, also if the publish fails), for Umbraco `clear_logs.sh`, and `create_prod.sh`.
- `scripts/create_prod.sh` adds a production target (remote over SSH, or a local folder). It asks for the settings, renames `publish.sh` and `clear_logs.sh` to `publish_local.sh` and `clear_logs_local.sh`, creates `publish_prod.sh`, `clear_logs_prod.sh` and `sync_prod.sh` (Umbraco) with the answers filled in (mode 700, as they may contain the SSH password), and deletes itself. It then automatically runs `publish_prod.sh`, followed by `sync_prod.sh` when available. A failed publish stops setup before sync; generated scripts remain available for retry. The admin hides Create production site once a production target exists. Production is always a Release build. Password login needs `sshpass`; the login must be root or have passwordless sudo, and `apache/init.sh` must have been run on the prod server. The settings block at the top of each prod script can be edited later (`PROD_IP`, `PROD_USER`, `PROD_PASSWORD` or an SSH key, `PROD_FOLDER`, `PROD_LOCAL_PATH`).
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

- Sites overview with one card per site in a consistent responsive grid; the last card never stretches across an entire row. The site name and type badges share a compact header. Each environment has its own row, with its badge on the left and Frontend and, for Umbraco, Backend links on the right, without Site/Source/Target captions. Python sites display Live. Sites with a production target add a second row, while single-environment cards stay shorter. Click the card to manage it. Open a site to see its environments side by side: the site itself and, if `scripts/` contains `publish_prod.sh`, its production target (read from the `PROD_*` settings; the password is never returned to the browser). Both local and remote production support state, start/stop/restart, logs, service checks, service/config editors, shell and file management. A server icon identifies remote production; SSH operations use the saved credentials and require root or passwordless sudo. Remote file management is confined to the production folder.
- Each environment card has buttons for its scripts: Publish, Clear logs, Issue certificate and Change environment (Publish and Clear logs on test need the script in `scripts/`; Issue certificate and Change environment always work, via `apache/test_action.sh` for test and `apache/prod_action.sh` for production, on this server or over SSH). Service cards also have Check service, Edit service and Edit config. Shell and Files stay in the site header for local sites; only remote environment cards have their own Shell and Files buttons. On C# sites, *Check outdated* sits in the NuGet packages panel alongside *Add package* and *Remove package*. *Sync to &lt;environment&gt;* sits at the top of the site next to *Delete everything*, using the target's reported environment (or *Sync to target* when unavailable). Scripts with a button are left out of the "Run script…" list. Publish, Sync, Clear logs, Issue certificate, Stop, Restart and Restart Apache ask for confirmation.
- Change environment opens a dialog with color-coded Development, Test, Staging and Production cards and the current environment. Applying a choice restarts the target service; the terminal shows progress without prompting for an environment and receives keyboard focus after the dialog closes. Local and remote badges and sync labels use systemd's effective environment settings, including quoted values and service overrides; `DOTNET_ENVIRONMENT` takes precedence over `ASPNETCORE_ENVIRONMENT` for the generated .NET applications. An unavailable environment is shown as unknown, never assumed to be Production.
- Issue certificate confirms once in the admin dialog and passes `--yes` to the action script. Direct CLI use still prompts unless `--yes` is supplied as the third argument.
- Buttons have icons and colour tones; Delete buttons are always alone at the far right.
- Fix permissions is available per environment on ordinary sites, including existing sites and local/remote production. It repairs the service WorkingDirectory (C# publish folder or Python app folder), or static HTML content, to www-data ownership with directories/executables 750 and files 640. It skips scripts, virtual environments, Git metadata, .env files and symbolic links. Admin sites are excluded. No service restart is needed. CLI: `bash apache/fix_permissions.sh <site> [test|prod]`.
- Error notifications stay visible until dismissed and appear above open dialogs in browsers supporting the Popover API. File save/apply errors also remain in the editor status and are logged to the browser console.
- A closed gold padlock marks HTTPS on overview links and environment cards; an open red padlock indicates HTTPS is not configured. Local and remote production require an HTTPS vhost and an existing certificate file. Remote status is read over SSH and cached for 15 seconds, so it does not depend on public DNS or TLS connectivity from the admin server. An unavailable remote status shows a neutral padlock and an explicit connection error, not a red padlock.
- Files opens a file-browser modal over the current site or overview, for both local and remote files. Opening a file stacks the Monaco editor on top (syntax colouring, Ctrl+S to save). Closing the editor returns to the same folder; closing the file browser returns to the original page without navigation.
- Non-editable files are hidden by default. Show all files / Hide non-editable files toggles binary/compiled assets, oversized files and symbolic links in the file browser. Filtered folders are shown only when they contain editable files, including in subfolders; symbolic links are not followed. Use Show all files to access empty folders. Non-editable filenames are muted and do not open the editor, but Download, Rename and Delete remain available. Editability checks use known binary extensions, the 2 MB editor limit and a binary-content sample for both local and remote listings. New file, New folder and Upload have colored icons; canceling the upload picker leaves the file-browser modal open.
- Edit service and Edit config use the same editor modal for local and remote environments, not a shell editor. Saving preserves file permissions and then asks whether to apply the change: daemon reload followed by service restart, or Apache configuration test followed by reload. Cancel keeps the saved file without applying it. A failed configuration test prevents Apache reload; errors remain visible in the editor. Only the selected site's service or Apache configuration file can be accessed by these actions.
- Apache configuration changes, site deletion and repeated initialization use configtest followed by reload (initialization starts Apache if stopped). Binding/certificate changes restart the site's application only when its Umbraco settings change; certificate renewal alone does not require an application restart. Application publishes/updates, environment changes and service-file changes still require restarting that application. Explicit Restart actions remain available for intentional full restarts.
- Successful mutation tasks (create/delete site, publish, environment/binding changes and similar actions), Check outdated and shells automatically close their terminal tab after exit code 0, unless output contains detected errors or warnings. Check outdated closes after any upgrade prompt has been answered and the script finishes, including when upgrades are declined. The terminal window closes when no tabs remain; other tabs are preserved. Check service, Apache config tests and unrecognized scripts keep their output open. This policy survives browser reloads; output detection is conservative and cannot recognize every possible tool-specific error format.
- Esc or Enter closes the terminal window after the selected process exits, including nonzero exits; running processes are not interrupted by these shortcuts.
- The ⚙ Settings menu in the header groups server-wide actions into Apache (Restart Apache, Test Apache config), Server (Browse /var/www, Server shell), and Administration (a link to the admin site, when available). The overview hides the admin site and the Apache default site (`/var/www/html`). The admin site's own binding is displayed as plain text rather than a link.
- Overview filters with zero sites are hidden. If the selected filter becomes empty after a refresh, the overview switches back to All.
- Overview filter counts follow the search text (site name, binding or remote host), independently of the selected category. No matches shows All with a count of zero; clearing the search restores the full counts.
- An empty overview displays a centered welcome message, a large icon and a New site button using the standard creation dialog. Searches without results show an invitation to adjust the search or create another site.
- The header search field is hidden until there are sites in the overview; it remains available when a search has no results.
- Deletion skips the certificate question when all environments being deleted report no HTTPS certificate. An unknown remote status keeps the question available.
- Umbraco sites show the installed Umbraco version (read from the `.csproj`) as a badge; the colour depends on the major version. Each environment card also has a *Backoffice* link to `<binding>/umbraco`.
- Terminals open in a modal window (button or Ctrl+`), and files open in an editor modal (Esc asks before discarding changes). The user icon opens the profile, where the username and password can be changed, and log out.
- Sites overview with start/stop/restart, binding and port; run `create_site.sh`, `change_binding.sh` (per environment) and `delete_site.sh`; run the scripts in a site's `scripts/` folder; open a shell in a site. C# site details list the NuGet package references from their `.csproj` files and provide *Add package* and *Remove package* dialogs. Click a package to copy its name (requires HTTPS or localhost); `Microsoft.ICU.ICU4C.Runtime` is hidden from the list without changing the project. Everything runs in interactive terminals that survive a browser reload.
- Delete is scoped: *Delete &lt;environment&gt; site* or *Delete everything*. Button labels and confirmation text use the current environment (for example, Test or Staging), while deletion scopes remain unchanged (`delete_site.sh <site> <y|n certs> [all|test|prod]`). Click the required confirmation text to copy it to the clipboard, then paste it into the confirmation field; copying alone never confirms deletion. If the Clipboard API is unavailable or denied, a selection-based copy is attempted inside the dialog. Copy status appears on the left of the dialog footer for 2.5 seconds without moving the input field; browsers may still require manual copying. Local production removes its service, Apache config and files. Remote production also removes its service, Apache config, logs, files and isolation root over SSH; deleting only the test site leaves production intact. Certificate removal is optional; locally and remotely, a certificate still used by another enabled site is kept. When removal is selected, the domain's certificate folders in `/etc/ssl/certs` and its `<domain>` / `<domain>_ecc` folders in `/root/.acme.sh` are removed. Other domains, ACME account settings and CA data are preserved. After deleting production, `create_prod.sh` can be run again. Scripts in the dropdown are shown with friendly names.
- File browser confined to `/var/www`: edit, upload, download, create, rename, delete.
- Security: **the site runs as root.** Login is rate limited per IP, sessions are HttpOnly/Secure/SameSite=Strict with a 2 hour idle timeout, and all POSTs require a CSRF token. Use a long password and preferably the IP allowlist.
- `apache/init.sh` offers to run the installer at the end. To remove the admin site again (service, Apache config, optional certificate, files and login) run `apache/uninstall_admin_site.sh`; other sites are not touched.
- Remote Shell uses an interactive login shell with `TERM=xterm-256color`, `COLORTERM=truecolor` and Ubuntu's `force_color_prompt=yes`. These settings apply only to the session; server-wide shell configuration is unchanged. Individual commands still control their own color output.
- To update an installed admin site after `update.sh`, run `apache/update_admin_site.sh`. It replaces the app code, updates the Python packages and restarts the site, and keeps the login, the virtual environment and the terminal library.
- Change the password with `scripts/set_password.sh` in the admin site (this also signs out all sessions). The xterm.js assets (jsdelivr) and the Monaco editor (npm registry, ~13 MB) are downloaded into `static/vendor` when the site is installed or updated; without Monaco the file editor falls back to a plain textarea.

## Requirements

- Ubuntu/Debian with `apt`
- A user with `sudo` privileges
- Internet access
