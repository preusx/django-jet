from __future__ import unicode_literals
import json
import os
from django import template
try:
    from django.core.urlresolvers import reverse
except ImportError: # Django 1.11
    from django.urls import reverse

from django.db import models
from django.db import connection
from django.forms import CheckboxInput, ModelChoiceField, Select, ModelMultipleChoiceField, SelectMultiple
from django.contrib.admin.widgets import RelatedFieldWidgetWrapper
from django.utils.formats import get_format
from django.utils.safestring import mark_safe
from django.utils.encoding import smart_str
from jet import settings, VERSION
from jet.models import Bookmark
from jet.utils import get_model_instance_label, get_model_queryset, get_possible_language_codes, \
    get_admin_site, get_menu_items

try:
    from urllib.parse import parse_qsl
except ImportError:
    from urlparse import parse_qsl


register = template.Library()
assignment_tag = register.assignment_tag if hasattr(register, 'assignment_tag') else register.simple_tag
EMPTY = object()


@assignment_tag
def jet_get_date_format():
    return get_format('DATE_INPUT_FORMATS')[0]


@assignment_tag
def jet_get_time_format():
    return get_format('TIME_INPUT_FORMATS')[0]


@assignment_tag
def jet_get_datetime_format():
    return get_format('DATETIME_INPUT_FORMATS')[0]


@assignment_tag(takes_context=True)
def jet_get_menu(context):
    return get_menu_items(context)


@assignment_tag
def jet_get_bookmarks(user):
    if user is None:
        return None
    return Bookmark.objects.filter(user=user.pk)


@register.filter
def jet_is_checkbox(field):
    return field.field.widget.__class__.__name__ == CheckboxInput().__class__.__name__


@register.filter
def jet_select2_lookups(field):
    if hasattr(field, 'field') and \
            (isinstance(field.field, ModelChoiceField) or isinstance(field.field, ModelMultipleChoiceField)):
        qs = field.field.queryset
        model = qs.model

        if getattr(model, 'autocomplete_search_fields', None) and getattr(field.field, 'autocomplete', True):
            choices = []
            app_label = model._meta.app_label
            model_name = model._meta.object_name

            attrs = {
                'class': 'ajax',
                'data-app-label': app_label,
                'data-model': model_name,
                'data-ajax--url': reverse('jet:model_lookup')
            }

            initial_value = field.value()

            if hasattr(field, 'field') and isinstance(field.field, ModelMultipleChoiceField):
                if initial_value:
                    initial_objects = model.objects.filter(pk__in=initial_value)
                    choices.extend(
                        [(initial_object.pk, get_model_instance_label(initial_object))
                            for initial_object in initial_objects]
                    )

                if isinstance(field.field.widget, RelatedFieldWidgetWrapper):
                    field.field.widget.widget = SelectMultiple(attrs)
                else:
                    field.field.widget = SelectMultiple(attrs)
                field.field.choices = choices
            elif hasattr(field, 'field') and isinstance(field.field, ModelChoiceField):
                if initial_value:
                    try:
                        initial_object = model.objects.get(pk=initial_value)
                        attrs['data-object-id'] = initial_value
                        choices.append((initial_object.pk, get_model_instance_label(initial_object)))
                    except model.DoesNotExist:
                        pass

                if isinstance(field.field.widget, RelatedFieldWidgetWrapper):
                    field.field.widget.widget = Select(attrs)
                else:
                    field.field.widget = Select(attrs)
                field.field.choices = choices

    return field


@assignment_tag(takes_context=True)
def jet_get_current_theme(context):
    if 'request' in context and 'JET_THEME' in context['request'].COOKIES:
        theme = context['request'].COOKIES['JET_THEME']
        if isinstance(settings.JET_THEMES, list) and len(settings.JET_THEMES) > 0:
            for conf_theme in settings.JET_THEMES:
                if isinstance(conf_theme, dict) and conf_theme.get('theme') == theme:
                    return theme
    return settings.JET_DEFAULT_THEME


@assignment_tag
def jet_get_themes():
    return settings.JET_THEMES


@assignment_tag
def jet_get_current_version():
    return VERSION


@register.filter
def jet_append_version(url):
    if '?' in url:
        return '%s&v=%s' % (url, VERSION)
    else:
        return '%s?v=%s' % (url, VERSION)


@assignment_tag
def jet_get_side_menu_compact():
    return settings.JET_SIDE_MENU_COMPACT


def check_original_sibling_links_availability(context):
    original = context.get('original')

    if not original:
        return False, None

    if not settings.JET_CHANGE_FORM_SIBLING_LINKS:
        return False, original

    model = type(original)
    meta = model._meta

    if (
        '.'.join((meta.app_label, meta.object_name))
        in
        settings.JET_CHANGE_FORM_SIBLING_LINKS_RESTRICT_MODELS
        or
        '.'.join((meta.app_label, meta.model_name))
        in
        settings.JET_CHANGE_FORM_SIBLING_LINKS_RESTRICT_MODELS
    ):
        return False, original

    return True, original


@assignment_tag(takes_context=True)
def jet_change_form_sibling_links_enabled(context):
    can, original = check_original_sibling_links_availability(context)

    return can if original is not None else settings.JET_CHANGE_FORM_SIBLING_LINKS


def jet_sibling_object(context, next):
    can, original = check_original_sibling_links_availability(context)

    if not can or original is None:
        return

    model = type(original)

    preserved_filters_plain = context.get('preserved_filters', '')
    preserved_filters = dict(parse_qsl(preserved_filters_plain))
    admin_site = get_admin_site(context)

    if admin_site is None:
        return

    request = context.get('request')
    queryset = get_model_queryset(admin_site, model, request, preserved_filters=preserved_filters)

    if queryset is None:
        return

    sibling_object = None

    sibling_ids = context.get('_object_sibling_ids', None)

    if sibling_ids is None:
        order_by = lambda: [
            models.F(field[1:]).desc()
            if field.startswith('-') else
            models.F(field).asc()
            for field in queryset.query.order_by
        ]
        next_fields = [
            (field[1:], 'lte') # desc
            if field.startswith('-') else
            (field, 'gte') # asc
            for field in queryset.query.order_by
            if '__' not in field
        ]

        if len(next_fields):
            next_fields += [('pk', next_fields[0][1])]
        else:
            next_fields += [('pk', 'gte')]

        next_values = [
            (field, dr, getattr(original, field, None))
            for field, dr in next_fields
        ]

        all_q = models.Q()
        eq_q = models.Q()

        for field, dr, value in next_values:
            if value is None:
                continue

            all_q |= models.Q(**{f'{field}__{dr}': value}) & eq_q
            eq_q &= models.Q(**{field: value})

        next_val = (
            queryset
            .filter(all_q)
            .order_by(*(
                ('-' if dr == 'lte' else '') + field
                for field, dr in next_fields
            ))
            .exclude(pk=original.pk)
            .values_list('pk', flat=True)
            .first()
        )

        all_q = models.Q()
        eq_q = models.Q()

        for field, dr, value in next_values:
            if value is None:
                continue

            dr = 'gte' if dr == 'lte' else 'lte'
            all_q |= models.Q(**{f'{field}__{dr}': value}) & eq_q
            eq_q &= models.Q(**{field: value})

        prev_val = (
            queryset
            .filter(all_q)
            .order_by(*(
                ('-' if dr == 'gte' else '') + field
                for field, dr in next_fields
            ))
            .exclude(pk=original.pk)
            .values_list('pk', flat=True)
            .first()
        )

        if prev_val is None and next_val is None:
            sibling_ids = None
        else:
            sibling_ids = (prev_val, original.pk, next_val)

        context['_object_sibling_ids'] = sibling_ids or ()

        # query = (
        #     queryset
        #     .annotate(
        #         _id=models.F('pk'),
        #         _prev=models.Window(
        #             expression=models.Aggregate('pk', function='lag'),
        #             order_by=order_by(),
        #         ),
        #         _next=models.Window(
        #             expression=models.Aggregate('pk', function='lead'),
        #             order_by=order_by(),
        #         ),
        #     )
        #     .values_list('_prev', '_id', '_next')
        # )

        # with connection.cursor() as cursor:
        #     sql = f'select _prev, _id, _next from ({str(query.query)}) x where %s = x._id;'
        #     cursor.execute(sql, [original.pk])
        #     sibling_ids = cursor.fetchone()
        #     context['_object_sibling_ids'] = sibling_ids or ()

        # print(sibling_ids, prev_val, next_val)

    if not sibling_ids:
        return

    sibling_index = 2 if next else 0
    sibling_id = sibling_ids[sibling_index]

    if sibling_id is None:
        return

    key = '_sibling_object_' + str(sibling_id)
    sibling_object = context.get(key, EMPTY)

    if sibling_object is EMPTY:
        sibling_object = queryset.get(pk=sibling_id)
        context[key] = sibling_object

    if sibling_object is None:
        return

    if sibling_object is None:
        return

    url = reverse('%s:%s_%s_change' % (
        admin_site.name,
        model._meta.app_label,
        model._meta.model_name
    ), args=(sibling_object.pk,))

    if preserved_filters_plain != '':
        url += '?' + preserved_filters_plain

    return {
        'label': str(sibling_object),
        'url': url
    }


@assignment_tag(takes_context=True)
def jet_previous_object(context):
    return jet_sibling_object(context, False)


@assignment_tag(takes_context=True)
def jet_next_object(context):
    return jet_sibling_object(context, True)


@assignment_tag(takes_context=True)
def jet_popup_response_data(context):
    if context.get('popup_response_data'):
        return context['popup_response_data']

    return json.dumps({
        'action': context.get('action'),
        'value': context.get('value') or context.get('pk_value'),
        'obj': smart_str(context.get('obj')),
        'new_value': context.get('new_value')
    })


@assignment_tag(takes_context=True)
def jet_delete_confirmation_context(context):
    if context.get('deletable_objects') is None and context.get('deleted_objects') is None:
        return ''
    return mark_safe('<div class="delete-confirmation-marker"></div>')


@assignment_tag
def jet_static_translation_urls():
    language_codes = get_possible_language_codes()

    urls = []
    url_templates = [
        'jet/js/i18n/jquery-ui/datepicker-__LANGUAGE_CODE__.js',
        'jet/js/i18n/jquery-ui-timepicker/jquery.ui.timepicker-__LANGUAGE_CODE__.js',
        'jet/js/i18n/select2/__LANGUAGE_CODE__.js'
    ]

    static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static')

    for tpl in url_templates:
        for language_code in language_codes:
            url = tpl.replace('__LANGUAGE_CODE__', language_code)
            path = os.path.join(static_dir, url)

            if os.path.exists(path):
                urls.append(url)
                break

    return urls
