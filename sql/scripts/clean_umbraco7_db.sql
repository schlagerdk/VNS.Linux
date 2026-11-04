use [DB_NAME]
TRUNCATE TABLE umbracoLog 
GO
TRUNCATE TABLE umbracoUserLogin
GO

-- Create a temporary table for all documents which are published and not in the recycle bin
CREATE TABLE #Nodes (id int)
GO
-- Delete all rows if the table exists before
TRUNCATE TABLE #Nodes
GO

-- Insert all nodeIds from all documents which are published and not in the recycle bin
INSERT INTO #Nodes 
    SELECT N.id 
    FROM umbracoNode N
        INNER JOIN cmsDocument D ON N.ID = D.NodeId
    WHERE nodeObjectType = 'C66BA18E-EAF3-4CFF-8A22-41B16D66A972'
        AND [path] NOT LIKE '%-20%'
        AND D.Published = 1
GO

-- Create a temporary table for all versionId's to delete
CREATE TABLE #Versions (id UniqueIdentifier)
GO
-- Delete all rows if it exists before
TRUNCATE TABLE #Versions
GO

-- Insert all versionId's from all nodeIds in the #Nodes table 
-- and where published is set to false and newest is set to false
INSERT INTO #Versions
    SELECT versionId 
    FROM cmsDocument 
    WHERE nodeId IN (SELECT id FROM #Nodes) AND published = 0 AND newest = 0
GO

-- DELETE all versions from cmsPreviewXml, cmsPropertyData, cmsContentVersion, cmsDocument
-- from the nodes which are published and which are not in the recycle bin 
-- and which are not published and which are not the newest
DELETE FROM cmsPreviewXml WHERE versionId IN (SELECT id FROM #Versions)
GO
DELETE FROM cmsPropertyData WHERE VersionId IN (SELECT id FROM #Versions)
GO
DELETE FROM cmsContentVersion WHERE VersionId IN (SELECT id FROM #Versions)
GO
DELETE FROM cmsDocument WHERE VersionId IN (SELECT id FROM #Versions)
GO

-- Drop temp tables
DROP TABLE #Versions
GO
DROP TABLE #Nodes
GO

-- Reindex tables  
DBCC DBREINDEX (cmsPropertyData)
DBCC DBREINDEX (cmsPreviewXml)
DBCC DBREINDEX (cmsContentVersion)
DBCC DBREINDEX (cmsDocument)
DBCC DBREINDEX (cmsContentXml)
DBCC DBREINDEX (umbracoDomains)
DBCC DBREINDEX (umbracoNode)
DBCC DBREINDEX (cmsContent)