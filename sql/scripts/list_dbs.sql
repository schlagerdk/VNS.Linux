set nocount on;
select
	d.name as [DATABASE],
	cast(sum(f.size) * 8 / 1024 as varchar(20)) + ' MB' as [SIZE],
	d.state_desc as [STATE],
	d.recovery_model_desc as [RECOVERY]
from sys.databases d
join sys.master_files f on f.database_id = d.database_id
group by d.name, d.database_id, d.state_desc, d.recovery_model_desc
order by case when d.database_id > 4 then 0 else 1 end, d.name;
