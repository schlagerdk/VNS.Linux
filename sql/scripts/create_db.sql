create database [NEW_NAME]
GO

create login [NEW_NAME] with password = 'NEW_NAME12qw34', check_policy = off, check_expiration = off, default_database = [NEW_NAME]
use [NEW_NAME]
create user [NEW_NAME] for login [NEW_NAME]
exec sp_addrolemember 'db_owner', [NEW_NAME]
GO
