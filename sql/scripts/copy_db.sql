backup database [OLD_NAME] to disk='/var/opt/mssql/backup/OLD_NAME.bak';

restore database [NEW_NAME] from disk='/var/opt/mssql/backup/OLD_NAME.bak'
with move 'OLD_NAME' to  '/var/opt/mssql/data/NEW_NAME.mdf', move 'OLD_NAME_log' to  '/var/opt/mssql/data/NEW_NAME_log.ldf';
GO


use [NEW_NAME]
drop user [OLD_NAME]
GO

create login [NEW_NAME] with password = 'NEW_NAME12qw34', check_policy = off, check_expiration = off, default_database = [NEW_NAME]
GO

use [NEW_NAME]
create user [NEW_NAME] for login [NEW_NAME]
exec sp_addrolemember 'db_owner', [NEW_NAME]
GO