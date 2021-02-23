import datetime

from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.contrib.admin.utils import (
    display_for_field, display_for_value, lookup_field,
)
from jet.dashboard.modules import DashboardModule


class ObjectsList(DashboardModule):
    """Widget for displaying list of objects from your models.
    It has almost the same API as internal django's admin changelist.
    To use widget:
    1. Inherit it in your class
    2. Override `get_queryset` method
    3. Override list_display
    """
    title = ''
    template = 'jet.dashboard/modules/object_list.html'
    limit = 5
    column = 0
    list_display = []

    def get_queryset(self):
        return None

    def init_with_context(self, context):
        queryset = self.get_queryset()

        if queryset is None:
            self.children.append({
                'title': 'You must provide a valid queryset',
                'warning': True,
            })
            return

        self.children = [
            items_for_result(obj, self)
            for obj in queryset[:self.limit]
        ]


def items_for_result(result, dashboard_module: ObjectsList):
    """
    Generate the actual list of data.
    """
    row_classes = []

    for field_index, field_name in enumerate(dashboard_module.list_display):
        try:
            f, attr, value = lookup_field(field_name, result, dashboard_module)
        except ObjectDoesNotExist:
            result_repr = None
        else:
            empty_value_display = None

            if f is None or f.auto_created:
                if field_name == 'action_checkbox':
                    row_classes = ['action-checkbox']
                boolean = getattr(attr, 'boolean', False)
                result_repr = display_for_value(value, empty_value_display, boolean)
                if isinstance(value, (datetime.date, datetime.time)):
                    row_classes.append('dim')
            else:
                if isinstance(f.remote_field, models.ManyToOneRel):
                    field_val = getattr(result, f.name)
                    if field_val is None:
                        result_repr = empty_value_display
                    else:
                        result_repr = field_val
                else:
                    result_repr = display_for_field(value, f, empty_value_display)
                if isinstance(f, (models.DateField, models.TimeField)):
                    row_classes.append('dim')

        row_class = mark_safe(' class="%s"' % ' '.join(row_classes))
        yield format_html('<span{}>{}</span>', row_class, result_repr)
