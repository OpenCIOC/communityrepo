<%inherit file="master.mak"/>
<%! 
import json

from markupsafe import Markup 
from communitymanager.lib import syslanguage
%>


<%block name="title">${title_txt}</%block>


${renderer.error_notice()}
<p>${prompt}
<br>${_('Use your back button to return to the form if you do not want to delete.')}
<form method="post" action="${request.current_route_path(_form=True)}">
<div class="hidden">
${renderer.form_passvars()}
</div>
<input type="submit" name="Submit" value="${_('Delete')}">
</form>

