SET QUOTED_IDENTIFIER ON
GO
SET ANSI_NULLS ON
GO

CREATE PROCEDURE [dbo].[sp_ExternalSystem_l] 
AS
BEGIN
	SET NOCOUNT ON

	SELECT SystemCode, SystemName FROM External_System ORDER BY SystemName

	SET NOCOUNT OFF
END




GO
GRANT EXECUTE ON  [dbo].[sp_ExternalSystem_l] TO [web_user]
GO
