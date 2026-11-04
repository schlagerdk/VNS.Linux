DECLARE @name VARCHAR(50) -- database name  
DECLARE @logname VARCHAR(256) -- filename for logfile  
DECLARE @SQL VARCHAR(256) -- filename for logfile  


DECLARE db_cursor CURSOR READ_ONLY FOR  
SELECT name 
FROM master.sys.databases 
WHERE name NOT IN ('master','model','msdb','tempdb')  -- exclude these databases
AND state = 0 -- database is online
AND is_in_standby = 0 -- database is not read only for log shipping
 
OPEN db_cursor   
FETCH NEXT FROM db_cursor INTO @name   
 
WHILE @@FETCH_STATUS = 0   
BEGIN   
	SET @logName = @name + '_log'
   
     -- Used Dynamic SQL for all databases.
	Set @SQL ='Use ['+ @name + '] '+Char(10)
	Set @SQL += 'Alter Database ['+ @name +'] Set Recovery Simple' +Char(10)
	Set @SQL += 'Alter Database ['+ @name +'] Set Recovery Full'+Char(10)
	Set @SQL += 'DBCC SHRINKFILE ("'+ @logName +'", 1)'+Char(10)
	
	Exec (@SQL)
 
	FETCH NEXT FROM db_cursor INTO @name
END   

CLOSE db_cursor   
DEALLOCATE db_cursor