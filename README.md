# VNS.Linux

Scripts til at sætte en Ubuntu/Debian-server op til web-hosting og vedligeholde den bagefter: Apache med Let's Encrypt-certifikater, .NET 10, Python, valgfri Node.js samt SQL Server med backup-værktøjer.

Alt deployes til `/vns` på serveren.

## Installation

Kør som en bruger med `sudo`-rettigheder:

```bash
apt install git && git clone https://github.com/schlagerdk/VNS.Linux.git
```

Derefter:

```bash
cd VNS.Linux
./init.sh                  # basis-opsætning og deploy til /vns
/vns/apache/init.sh        # Apache, acme.sh, .NET 10, Python
/vns/install_nodejs.sh     # valgfrit: Node.js via nvm
```

`init.sh` gør følgende:

1. Sætter tidszone til `Europe/Copenhagen` og aktiverer NTP
2. Opdaterer alle pakker
3. Installerer `curl git net-tools nano unzip ca-certificates rsync`
4. Kopierer repoet til `/vns` via `update.sh` og sletter den klonede mappe igen

## Opdatering

```bash
/vns/update.sh
```

Henter seneste version fra GitHub og synkroniserer den til `/vns`. Lokale filer slettes ikke, og `apache/_portnumber.txt` overskrives aldrig, så eksisterende portnumre bevares.

## Indhold

| Mappe / fil | Beskrivelse |
|---|---|
| `init.sh` | Førstegangsopsætning af serveren |
| `update.sh` | Opdaterer `/vns` fra GitHub |
| `install_nodejs.sh` | Installerer Node.js 22 via nvm |
| `apache/` | Apache-opsætning og oprettelse/sletning af sites |
| `sql/` | SQL Server-installation, database- og backup-scripts |
| `misc/` | Noter: netværk, SSH, Proxmox, MailCow m.m. |

### Apache (`apache/`)

- `init.sh` installerer Apache (med `ssl rewrite headers proxy proxy_http proxy_wstunnel`), acme.sh (Let's Encrypt, auto-fornyelse via cron), .NET 10 SDK og ASP.NET Core runtime, Python 3 samt `dotnet-outdated`.
- `create_site.sh [site] [html|python|csharp] [umbraco|empty] [umbraco-version]` opretter et site med virtual host, certifikat og (for Python/C#) en systemd-service med automatisk tildelt port.
- `delete_site.sh [site] [y|n]` fjerner site, service og valgfrit certifikater.
- `solutionfiles/` indeholder skabeloner for `html`, `python` og `csharp` (inkl. en Umbraco-skabelon).

### SQL Server (`sql/`)

- `install_server_22_04.sh` / `install_server_25_04.sh` installerer SQL Server til den pågældende Ubuntu-version, og `install_tools.sh` installerer `sqlcmd`.
- Install-scripts spørger efter `sa`-adgangskoden og indsætter den i de øvrige scripts i `sql/` (pladsholderen `@SA_PASSWORD@`). Brug samme adgangskode i `mssql-conf setup`. Adgangskoden ligger kun lokalt på serveren, og `update.sh` bevarer den ved opdateringer.
- `create_db.sh`, `drop_db.sh`, `copy_db.sh`, `restore_db.sh`, `list_dbs.sh` og `list_backups.sh` til daglig administration.
- `cron/` indeholder planlagte jobs (backup, log-shrink m.fl.), som aktiveres med `sql/cron/add_to_crontab.sh`.

## Krav

- Ubuntu/Debian med `apt`
- Bruger med `sudo`
- Adgang til internettet
