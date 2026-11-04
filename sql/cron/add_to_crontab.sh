#!/bin/bash
(crontab -l 2>/dev/null; echo "0 */4 * * * /vns/sql/cron/shrink_logs.sh >> /var/log/sql_shrink_logs.log 2>&1") | crontab -
(crontab -l 2>/dev/null; echo "15 */4 * * * /vns/sql/cron/backup_everything.sh >> /var/log/sql_backup_everything.log 2>&1") | crontab -

#(crontab -l 2>/dev/null; echo "0 0 * * * /vns/sql/cron/delete_old_2dm_data.sh >> /var/log/sql_backup_everything.log 2>&1") | crontab -
