restore database [NEW_NAME] from disk='/var/opt/mssql/backup/NEW_NAME.bak'
with move 'NEW_NAME' to '/var/opt/mssql/data/NEW_NAME.mdf', move 'NEW_NAME_log' to '/var/opt/mssql/data/NEW_NAME_log.ldf'
GO

use [NEW_NAME]
drop user [NEW_NAME]
GO

create login [NEW_NAME] with password = 'NEW_NAME12qw34', check_policy = off, check_expiration = off, default_database = [NEW_NAME]
GO

use [NEW_NAME]
create user [NEW_NAME] for login [NEW_NAME]
exec sp_addrolemember 'db_owner', [NEW_NAME]
GO
