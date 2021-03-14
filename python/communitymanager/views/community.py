# =========================================================================================
#  Copyright 2015 Community Information Online Consortium (CIOC) and KCL Software Solutions
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# =========================================================================================

# std lib
import xml.etree.cElementTree as ET

# 3rd party
from pyramid.view import view_config
from pyramid.httpexceptions import HTTPNotFound, HTTPFound
from pyramid.security import Allow, DENY_ALL, NO_PERMISSION_REQUIRED
from markupsafe import Markup

from formencode.variabledecode import variable_decode

# this app
from communitymanager.views.base import ViewBase, xml_to_dict_list
from communitymanager.lib import validators

import logging
log = logging.getLogger('communitymanager.views.community')


class CommunityRoot(object):
    def __init__(self, request):
        cm_id = request.matchdict.get('cmid')

        if cm_id == 'new':
            if request.matched_route.name == 'community_delete':
                raise HTTPNotFound()

            parents = [('manager',)]
            self.community = None
            self.descriptions = {}
            self.alt_names = []
            self.alt_areas = []

        else:
            validator = validators.IntID(not_empty=True)
            try:
                cm_id = validator.to_python(cm_id)
            except validators.Invalid:
                raise HTTPNotFound()

            parents = []
            with request.connmgr.get_connection() as conn:
                cursor = conn.execute('EXEC sp_Community_s ?', cm_id)

                community = self.community = cursor.fetchone()

                if community is None:
                    raise HTTPNotFound()

                cursor.nextset()

                parents = cursor.fetchall()

                cursor.nextset()

                self.descriptions = {x.Culture.replace('-', '_'): x for x in cursor.fetchall()}

                cursor.nextset()

                self.alt_names = cursor.fetchall()

                cursor.nextset()

                self.alt_areas = cursor.fetchall()

                cursor.close()

            # and this one
            parents.append((cm_id,))

        self.__acl__ = [(Allow, 'area:' + str(x[0]), 'edit') for x in parents]
        self.__acl__.append((Allow, 'area:admin', 'edit'))
        self.__acl__.append(DENY_ALL)


def cannot_save_without_parent(value_dict, state):
    user = state.request.user
    return not (user and user.Admin)


class CommunityBaseSchema(validators.Schema):

    ParentCommunity = validators.IntID()
    ParentCommunityName = validators.UnicodeString()
    ProvinceState = validators.IntID(not_empty=True)

    chained_validators = [validators.RequireIfPredicate(cannot_save_without_parent, ['ParentCommunity'])]


class CommunityDescriptionSchema(validators.Schema):
    Name = validators.UnicodeString(not_empty=True, max=200)


class AltNameSchema(validators.Schema):
    if_key_missing = None

    Delete = validators.Bool()
    Culture = validators.ActiveCulture()
    AltName = validators.UnicodeString(max=200)

    # chained_validators = [validators.RequireIfPredicate(lambda x,y: not x.get('Delete'), ['AltName', 'LangID'])]


class CommunitySchema(validators.Schema):
    allow_extra_fields = True
    filter_extra_fields = True

    community = CommunityBaseSchema()
    descriptions = validators.CultureDictSchema(CommunityDescriptionSchema(),
                                               pre_validators=[validators.DeleteKeyIfEmpty()],
                                               chained_validators=[validators.FlagRequiredIfNoCulture(CommunityDescriptionSchema)])

    ReasonForChange = validators.UnicodeString(not_empty=True)
    alt_names = validators.ForEach(AltNameSchema())


class AltAreasSchema(CommunitySchema):

    alt_areas = validators.ForEach(validators.IntID(), not_emtpy=True)

    chained_validators = [validators.ForceRequire('alt_areas')]


class DeleteCommunitySchema(validators.Schema):
    allow_extra_fields = True
    filter_extra_fields = True

    ReasonForChange = validators.UnicodeString(not_empty=True)


class Community(ViewBase):
    @view_config(route_name="community", request_method="POST", renderer='community.mak', permission='edit')
    def post(self):
        request = self.request

        is_alt_area = not not request.params.get('altarea')

        cm_id = self._get_cmid()

        if cm_id != 'new' and request.params.get('Delete'):
            reason = request.params.get('ReasonForChange')
            if reason:
                request.session.flash((cm_id, reason), 'ReasonForChange')
            return HTTPFound(location=request.route_url('community_delete', cmid=cm_id))

        model_state = request.model_state
        model_state.form.variable_decode = True
        if is_alt_area:
            log.debug('alt area')
            model_state.schema = AltAreasSchema()
        else:
            log.debug('community')
            model_state.schema = CommunitySchema()

        if model_state.validate():
            data = model_state.form.data
            cm_data = data.get('community', {})
            args = [cm_id if cm_id != 'new' else None,
                    request.user.User_ID, is_alt_area, cm_data.get('ParentCommunity'),
                    cm_data.get('ProvinceState'), data.get('ReasonForChange')]

            root = ET.Element('DESCS')

            for culture, description in model_state.form.data['descriptions'].items():

                desc = ET.SubElement(root, 'DESC')
                ET.SubElement(desc, "Culture").text = culture.replace('_', '-')
                for name, value in description.items():
                    if value:
                        ET.SubElement(desc, name).text = value

            args.append(ET.tostring(root))

            root = ET.Element('NAMES')

            for name in model_state.form.data.get('alt_names') or []:
                if name.get('Delete') or not name.get('AltName'):
                    continue

                desc = ET.SubElement(root, 'Name')
                ET.SubElement(desc, 'Culture').text = name['Culture']
                ET.SubElement(desc, 'AltName').text = name['AltName']

            args.append(ET.tostring(root))

            if is_alt_area:
                root = ET.Element('ALTAREAS')
                for area in data.get('alt_areas') or []:
                    ET.SubElement(root, 'CM_ID').text = str(area)

                args.append(ET.tostring(root))

            else:
                args.append(None)

            sql = '''
                DECLARE @RC int, @CM_ID int, @ErrMsg nvarchar(500)

                SET @CM_ID = ?

                EXEC @RC = sp_Community_u @CM_ID OUTPUT, %s, @ErrMsg OUTPUT

                SELECT @RC AS [Return], @CM_ID AS CM_ID, @ErrMsg AS ErrMsg

                ''' % (', '.join('?' * (len(args) - 1)))

            with request.connmgr.get_connection() as conn:
                result = conn.execute(sql, args).fetchone()

            if not result.Return:
                _ = request.translate
                if is_alt_area:
                    msg = _('Alternate search area saved.')
                else:
                    msg = _('Community saved.')

                request.session.flash(msg)
                kw = {}
                if cm_id == 'new':
                    kw['cmid'] = result.CM_ID
                return HTTPFound(location=request.current_route_url(**kw))

            model_state.add_error_for('*', result.ErrMsg)

            alt_areas = data.get('alt_areas') or []
        else:
            data = model_state.data
            decoded = variable_decode(request.POST)
            alt_areas = decoded.get('alt_areas') or []

            data['alt_areas'] = alt_areas
            data['alt_names'] = decoded.get('alt_names') or []

        community = request.context.community
        if cm_id != 'new':
            is_alt_area = community.AlternativeArea

        prov_state = []
        alt_area_name_map = {}
        with request.connmgr.get_connection() as conn:
            prov_state = list(map(tuple, conn.execute('SELECT ProvID, ProvinceStateCountry FROM dbo.vw_ProvinceStateCountry ORDER BY DisplayOrder, ProvinceStateCountry').fetchall()))

            if is_alt_area:
                alt_area_name_map = {str(x[0]): x[1] for x in conn.execute('EXEC sp_Community_ls_Names ?', ','.join(str(x) for x in alt_areas)).fetchall()}

        if community:
            community.ChildCommunities = xml_to_dict_list(community.ChildCommunities)
            community.AltSearchArea = xml_to_dict_list(community.AltSearchArea)

        log.debug('errors:', model_state.form.errors)

        return {'community': community, 'alt_area_name_map': alt_area_name_map,
                'is_alt_area': is_alt_area, 'prov_state': prov_state}

    @view_config(route_name="community", renderer='community.mak', permission='edit')
    def get(self):
        request = self.request
        context = self.request.context

        cm_id = self._get_cmid()
        if cm_id == 'new':
            is_alt_area = not not request.params.get('altarea')

        community = context.community
        descriptions = context.descriptions
        alt_names = context.alt_names
        alt_areas = context.alt_areas

        if cm_id != 'new':
            is_alt_area = community.AlternativeArea

        prov_state = []
        with request.connmgr.get_connection() as conn:
            prov_state = list(map(tuple, conn.execute('SELECT ProvID, ProvinceStateCountry FROM dbo.vw_ProvinceStateCountry ORDER BY DisplayOrder, ProvinceStateCountry').fetchall()))

        if community:
            community.ChildCommunities = xml_to_dict_list(community.ChildCommunities)
            community.AltSearchArea = xml_to_dict_list(community.AltSearchArea)

        data = request.model_state.form.data
        data['community'] = community
        data['descriptions'] = descriptions
        data['alt_names'] = alt_names
        data['alt_areas'] = [str(x.Search_CM_ID) for x in alt_areas]

        alt_area_name_map = {str(x[0]): x[1] for x in alt_areas}

        return {'community': community, 'alt_area_name_map': alt_area_name_map, 'is_alt_area': is_alt_area, 'prov_state': prov_state}

    def _get_cmid(self):

        cm_id = self.request.matchdict.get('cmid')
        if cm_id != 'new':
            validator = validators.IntID(not_empty=True)
            try:
                cm_id = validator.to_python(cm_id)
            except validators.Invalid:
                raise HTTPNotFound()

        return cm_id

    @view_config(route_name='community_delete', renderer='confirmdelete.mak', request_method='POST', permission='edit')
    def confirm_delete(self):
        request = self.request

        cm_id = self._get_cmid()

        model_state = request.model_state
        model_state.form.variable_decode = True
        model_state.schema = DeleteCommunitySchema()

        if model_state.validate():
            sql = '''
                Declare @ErrMsg as nvarchar(500),
                @RC as int

                EXECUTE @RC = dbo.sp_Community_d ?, ?, ?, @ErrMsg=@ErrMsg OUTPUT

                SELECT @RC as [Return], @ErrMsg AS ErrMsg
            '''
            with request.connmgr.get_connection() as conn:
                result = conn.execute(sql, cm_id, request.user.UserName,
                                      model_state.value('ReasonForChange')).fetchone()

            _ = request.translate
            if not result.Return:
                request.session.flash(_('The Community was successfully deleted'))
                return HTTPFound(location=request.route_url('communities'))

            request.session.flash(_('Unable to delete Community: ') + result.ErrMsg, 'errorqueue')
            if result.Return == 3:
                # cmid does not exist
                return HTTPFound(location=request.route_url('communities'))

            return HTTPFound(location=request.route_url('community', cmid=cm_id))

        return self._get_delete_page_info()

    @view_config(route_name='community_delete', renderer='confirmdelete.mak', permission='edit')
    def delete(self):
        request = self.request
        model_state = request.model_state

        reason = request.session.pop_flash('ReasonForChange')
        log.debug('reason: %s', reason)
        if reason:
            cm_id, reason = reason[0]
            if cm_id == self._get_cmid() and reason:
                model_state.form.data['ReasonForChange'] = reason

        return self._get_delete_page_info()

    def _get_delete_page_info(self):
        request = self.request
        _ = request.translate

        cm_names = list(request.context.descriptions.values())
        cm_names.sort(key=lambda x: (x.LangID != request.language.LangID, x.LangID))
        cm_name = cm_names[0]
        if request.context.community.AlternativeArea:
            title = _('Delete Alternate Search Area')
            prompt = _('Are you sure you want to delete the alternate search area: <em>%s</em>?')
        else:
            title = _('Delete Community')
            prompt = _('Are you sure you want to delete the community: <em>%s</em>?')

        return {'title_text': title,
                'prompt': Markup(prompt) % cm_name.Name,
                'continue_prompt': _('Delete'), 'use_reason_for_change': True}

    @view_config(route_name='json_parents', renderer='json', permission='view')
    @view_config(route_name='json_search_areas', renderer='json', permission='view')
    def autocomplete(self):
        request = self.request

        if not request.user or not (request.user.Admin or request.user.ManageAreaList):
            return []

        term_validator = validators.UnicodeString(not_empty=True)
        try:
            terms = term_validator.to_python(request.params.get('term'))
        except validators.Invalid:
            return []

        cm_id_validator = validators.IntID()
        try:
            cur_parent = cm_id_validator.to_python(request.params.get('parent'))
        except validators.Invalid:
            cur_parent = None

        cur_cm_id = None
        if request.matched_route.name == 'json_search_areas':
            try:
                cur_cm_id = cm_id_validator.to_python(request.params.get('cmid'))
            except validators.Invalid:
                pass

        retval = []
        search_areas = request.matched_route.name == 'json_search_areas'
        with request.connmgr.get_connection() as conn:
            if search_areas:
                cursor = conn.execute('EXEC sp_Community_ls_SearchAreaSelector ?, ?, ?, ?',
                                      request.user.User_ID, cur_cm_id, cur_parent, terms)
            else:
                cursor = conn.execute('EXEC sp_Community_ls_ParentSelector ?, ?, ?',
                                      request.user.User_ID, cur_parent, terms)

            cols = ['chkid', 'value', 'label']

            retval = [dict(zip(cols, x)) for x in cursor.fetchall()]

            cursor.close()

        return retval

    @view_config(context='pyramid.httpexceptions.HTTPForbidden', route_name="community", renderer='not_authorized.mak', permission=NO_PERMISSION_REQUIRED, custom_predicates=[lambda context, request: not not request.user])
    def not_authorized(self):
        return {}
