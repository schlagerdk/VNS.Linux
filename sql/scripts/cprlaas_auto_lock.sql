use [cprlaas.dk]
insert into [history] (user_guid, action, ip) select guid, 17, '127.0.0.1' from [user] where islocked = 0 and autolock = 1 and datemodified < DateADD(mi, -15, Current_TimeStamp)
update [user] set islocked = 1, datemodified = GETDATE() where islocked = 0 and autolock = 1 and datemodified < DateADD(mi, -15, Current_TimeStamp)
GO