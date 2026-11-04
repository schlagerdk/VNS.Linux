use [2dm.dk]
delete from stats where datecreated < Getdate() - 364
delete from smsnewsletter where datecreated < Getdate() - 120
delete from smslog where datecreated < Getdate() - 120
delete from smslogoutgoing where datecreated < Getdate() - 120
delete from smslogincoming where datecreated < Getdate() - 120