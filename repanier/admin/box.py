# -*- coding: utf-8
from __future__ import unicode_literals

from os import sep as os_sep

from django import forms
from django.contrib import admin
from django.contrib import messages
from django.contrib.admin import TabularInline
from django.db.models import Sum
from django.forms import ModelForm, BaseInlineFormSet
from django.forms.formsets import DELETION_FIELD_NAME
from django.shortcuts import render
from django.utils import translation
from django.utils.translation import ugettext_lazy as _
from easy_select2 import Select2
from parler.admin import TranslatableAdmin
from parler.forms import TranslatableModelForm

from repanier.admin.fkey_choice_cache_mixin import ForeignKeyCacheMixin
from repanier.const import DECIMAL_ZERO, ORDER_GROUP, INVOICE_GROUP, \
    COORDINATION_GROUP
from repanier.models.box import BoxContent, Box
from repanier.models.product import Product
from repanier.task import task_box
from repanier.tools import update_offer_item

try:
    from urllib.parse import parse_qsl
except ImportError:
    from urlparse import parse_qsl


class BoxContentInlineFormSet(BaseInlineFormSet):
    def clean(self):
        products = set()
        for form in self.forms:
            if form.cleaned_data and not form.cleaned_data.get('DELETE'):
                # This is not an empty form or a "to be deleted" form
                product = form.cleaned_data.get('product', None)
                if product is not None:
                    if product in products:
                        raise forms.ValidationError(_('Duplicate product are not allowed.'))
                    else:
                        products.add(product)

    def get_queryset(self):
        return self.queryset.filter(
            product__translations__language_code=translation.get_language()
        ).order_by(
            "product__producer__short_profile_name",
            "product__translations__long_name",
            "product__order_average_weight",
        )



class BoxContentInlineForm(ModelForm):
    is_into_offer = forms.BooleanField(
        label=_("is_into_offer"), required=False, initial=True)
    stock = forms.DecimalField(
        label=_("Current stock"), max_digits=9, decimal_places=3, required=False, initial=DECIMAL_ZERO)
    limit_order_quantity_to_stock = forms.BooleanField(
        label=_("limit maximum order qty of the group to stock qty"), required=False, initial=True)
    previous_product = forms.ModelChoiceField(
        Product.objects.none(), required=False)

    def __init__(self, *args, **kwargs):
        super(BoxContentInlineForm, self).__init__(*args, **kwargs)
        self.fields["product"].widget.can_add_related = False
        self.fields["product"].widget.can_delete_related = False
        if self.instance.id is not None:
            self.fields["is_into_offer"].initial = self.instance.product.is_into_offer
            self.fields["stock"].initial = self.instance.product.stock
            self.fields["limit_order_quantity_to_stock"].initial = self.instance.product.limit_order_quantity_to_stock
            self.fields["previous_product"].initial = self.instance.product

        self.fields["is_into_offer"].disabled = True
        self.fields["stock"].disabled = True
        self.fields["limit_order_quantity_to_stock"].disabled = True

    class Meta:
        widgets = {
            'product': Select2(select2attrs={'width': '450px'})
        }


class BoxContentInline(ForeignKeyCacheMixin, TabularInline):
    form = BoxContentInlineForm
    formset = BoxContentInlineFormSet
    model = BoxContent
    ordering = ("product",)
    fields = ['product', 'is_into_offer', 'content_quantity', 'stock', 'limit_order_quantity_to_stock',
              'get_calculated_customer_content_price']
    extra = 0
    fk_name = 'box'
    # The stock and limit_order_quantity_to_stock are read only to have only one place to update it : the product.
    readonly_fields = [
        'get_calculated_customer_content_price'
    ]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "product":
            kwargs["queryset"] = Product.objects.filter(
                is_active=True,
                # A box may not include another box
                is_box=False,
                # We can't make any composition with producer preparing baskets on basis of our order.
                producer__invoice_by_basket=False,
                translations__language_code=translation.get_language()
            ).order_by(
                "producer__short_profile_name",
                "translations__long_name",
                "order_average_weight",
            )
        return super(BoxContentInline, self).formfield_for_foreignkey(db_field, request, **kwargs)


class BoxForm(TranslatableModelForm):
    calculated_stock = forms.DecimalField(
        label=_("Current stock"), max_digits=9, decimal_places=3, required=False, initial=DECIMAL_ZERO)
    calculated_customer_box_price = forms.DecimalField(
        label=_("calculated customer box price"), max_digits=8, decimal_places=2, required=False, initial=DECIMAL_ZERO)
    calculated_box_deposit = forms.DecimalField(
        label=_("calculated box deposit"), max_digits=8, decimal_places=2, required=False, initial=DECIMAL_ZERO)

    def __init__(self, *args, **kwargs):
        super(BoxForm, self).__init__(*args, **kwargs)
        if self.instance.id is not None:
            result_set = BoxContent.objects.filter(box_id=self.instance.id).aggregate(
                Sum('calculated_customer_content_price'),
                Sum('calculated_content_deposit')
            )
            calculated_customer_box_price = result_set["calculated_customer_content_price__sum"] \
                if result_set["calculated_customer_content_price__sum"] is not None else DECIMAL_ZERO
            calculated_box_deposit = result_set["calculated_content_deposit__sum"] \
                if result_set["calculated_content_deposit__sum"] is not None else DECIMAL_ZERO

            self.fields["calculated_stock"].initial = self.instance.stock
            self.fields["calculated_customer_box_price"].initial = calculated_customer_box_price
            self.fields["calculated_box_deposit"].initial = calculated_box_deposit

        self.fields["calculated_customer_box_price"].disabled = True
        self.fields["calculated_stock"].disabled = True
        self.fields["calculated_box_deposit"].disabled = True


class BoxAdmin(TranslatableAdmin):
    form = BoxForm
    model = Box

    list_display = (
        'is_into_offer', 'get_long_name', 'language_column',
    )
    list_display_links = ('get_long_name',)
    list_per_page = 16
    list_max_show_all = 16
    inlines = (BoxContentInline,)
    filter_horizontal = ('production_mode',)
    ordering = ('customer_unit_price',
                'unit_deposit',
                'translations__long_name',)
    search_fields = ('translations__long_name',)
    list_filter = ('is_active',
                   'is_into_offer')
    actions = [
        'flip_flop_select_for_offer_status',
        'duplicate_box'
    ]

    def has_delete_permission(self, request, box=None):
        if request.user.groups.filter(
                name__in=[ORDER_GROUP, INVOICE_GROUP, COORDINATION_GROUP]).exists() or request.user.is_superuser:
            return True
        return False

    def has_add_permission(self, request):
        return self.has_delete_permission(request)

    def has_change_permission(self, request, box=None):
        return self.has_delete_permission(request, box)

    def flip_flop_select_for_offer_status(self, request, queryset):
        task_box.flip_flop_is_into_offer(queryset)

    flip_flop_select_for_offer_status.short_description = _(
        'flip_flop_select_for_offer_status for offer')

    def duplicate_box(self, request, queryset):
        if 'cancel' in request.POST:
            user_message = _("Action canceled by the user.")
            user_message_level = messages.INFO
            self.message_user(request, user_message, user_message_level)
            return None
        box = queryset.order_by('?').first()
        if box is None:
            user_message = _("Action canceled by the system.")
            user_message_level = messages.ERROR
            self.message_user(request, user_message, user_message_level)
            return None
        if 'apply' in request.POST:
            user_message, user_message_level = task_box.admin_duplicate(queryset)
            self.message_user(request, user_message, user_message_level)
            return None
        return render(
            request,
            'repanier/confirm_admin_duplicate_box.html', {
                'sub_title'           : _("Please, confirm the action : duplicate box"),
                'action_checkbox_name': admin.ACTION_CHECKBOX_NAME,
                'action'              : 'duplicate_box',
                'product'             : box,
            })

    duplicate_box.short_description = _('duplicate box')

    def get_fieldsets(self, request, box=None):
        fields_basic = [
            ('long_name', 'picture2', 'calculated_stock'),
            ('calculated_customer_box_price', 'calculated_box_deposit'),
            ('customer_unit_price', 'unit_deposit'),
        ]
        fields_advanced_descriptions = [
            'placement',
            'offer_description',
            'production_mode',
        ]
        fields_advanced_options = [
            ('reference', 'vat_level'),
            ('is_into_offer', 'is_active', 'is_updated_on')
        ]
        fieldsets = (
            (None, {'fields': fields_basic}),
            (_('Advanced descriptions'), {'classes': ('collapse',), 'fields': fields_advanced_descriptions}),
            (_('Advanced options'), {'classes': ('collapse',), 'fields': fields_advanced_options})
        )
        return fieldsets

    def get_readonly_fields(self, request, customer=None):
        return ['stock', 'is_updated_on']

    def get_form(self, request, box=None, **kwargs):
        form = super(BoxAdmin, self).get_form(request, box, **kwargs)
        picture_field = form.base_fields["picture2"]
        if hasattr(picture_field.widget, 'upload_to'):
            picture_field.widget.upload_to = "%s%s%s" % ("product", os_sep, "box")
        return form

    def get_queryset(self, request):
        qs = super(BoxAdmin, self).get_queryset(request)
        qs = qs.filter(
            is_box=True,
            translations__language_code=translation.get_language()
        )
        return qs

    def save_model(self, request, box, form, change):
        super(BoxAdmin, self).save_model(request, box, form, change)
        update_offer_item(box)

    def save_related(self, request, form, formsets, change):
        for formset in formsets:
            # option.py -> construct_change_message doesn't test the presence of those array not created at form initialisation...
            if not hasattr(formset, 'new_objects'): formset.new_objects = []
            if not hasattr(formset, 'changed_objects'): formset.changed_objects = []
            if not hasattr(formset, 'deleted_objects'): formset.deleted_objects = []
        box = form.instance
        formset = formsets[0]
        for box_content_form in formset:
            box_content = box_content_form.instance
            previous_product = box_content_form.fields['previous_product'].initial
            if previous_product is not None and previous_product != box_content.product:
                # Delete the box_content because the product has changed
                box_content_form.instance.delete()
            if box_content.product is not None:
                if box_content.id is None:
                    box_content.box_id = box.id
                if box_content_form.cleaned_data.get(DELETION_FIELD_NAME, False):
                    box_content_form.instance.delete()
                elif box_content_form.has_changed():
                    box_content_form.instance.save()
        box.save_update_stock()
