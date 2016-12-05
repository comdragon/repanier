# -*- coding: utf-8
from __future__ import unicode_literals

from os import sep as os_sep

from django import forms
from django.conf import settings
from django.contrib import admin
from django.core.checks import messages
from django.db.models import F
from django.shortcuts import render
from django.utils import translation
from django.utils.formats import number_format
from django.utils.translation import ugettext_lazy as _
from easy_select2 import apply_select2
from import_export import resources, fields
from import_export.admin import ImportExportMixin
from import_export.formats.base_formats import XLS
from import_export.widgets import ForeignKeyWidget
from parler.admin import TranslatableAdmin
from parler.forms import TranslatableModelForm

from repanier.admin.admin_filter import ProductFilterByDepartmentForThisProducer, ProductFilterByProducer
from repanier.const import *
from repanier.models import Product, \
    LUT_ProductionMode, LUT_DepartmentForCustomer, Producer
from repanier.task import task_product
from repanier.tools import sint, update_offer_item
from repanier.widget.select_admin_order_unit import SelectAdminOrderUnitWidget
from repanier.xlsx.widget import IdWidget, TranslatedForeignKeyWidget, \
    DecimalBooleanWidget, ChoiceWidget, ThreeDecimalsWidget, TranslatedManyToManyWidget, TwoMoneysWidget
from repanier.xlsx.extended_formats import XLSX_OPENPYXL_1_8_6

try:
    from urllib.parse import parse_qsl
except ImportError:
    from urlparse import parse_qsl


class ProductResource(resources.ModelResource):
    id = fields.Field(attribute='id', widget=IdWidget(), readonly=True)
    producer_name = fields.Field(attribute='producer', widget=ForeignKeyWidget(Producer, field='short_profile_name'))
    long_name = fields.Field(attribute='long_name')
    department_for_customer = fields.Field(attribute='department_for_customer',
                                           widget=TranslatedForeignKeyWidget(LUT_DepartmentForCustomer,
                                                                             field='short_name'))
    order_unit = fields.Field(attribute='order_unit',
                              widget=ChoiceWidget(LUT_PRODUCT_ORDER_UNIT, LUT_PRODUCT_ORDER_UNIT_REVERSE))
    order_average_weight = fields.Field(attribute='order_average_weight', widget=ThreeDecimalsWidget())
    producer_unit_price = fields.Field(attribute='producer_unit_price', widget=TwoMoneysWidget())
    customer_unit_price = fields.Field(attribute='customer_unit_price', widget=TwoMoneysWidget())
    unit_deposit = fields.Field(attribute='unit_deposit', widget=TwoMoneysWidget())
    vat_level = fields.Field(attribute='vat_level', widget=ChoiceWidget(settings.LUT_VAT, settings.LUT_VAT_REVERSE))
    customer_minimum_order_quantity = fields.Field(attribute='customer_minimum_order_quantity',
                                                   widget=ThreeDecimalsWidget())
    customer_increment_order_quantity = fields.Field(attribute='customer_increment_order_quantity',
                                                     widget=ThreeDecimalsWidget())
    customer_alert_order_quantity = fields.Field(attribute='customer_alert_order_quantity',
                                                 widget=ThreeDecimalsWidget())
    wrapped = fields.Field(attribute='wrapped', widget=DecimalBooleanWidget())
    stock = fields.Field(attribute='stock', widget=ThreeDecimalsWidget())
    limit_order_quantity_to_stock = fields.Field(attribute='limit_order_quantity_to_stock', widget=DecimalBooleanWidget(), readonly=False)
    producer_order_by_quantity = fields.Field(attribute='producer_order_by_quantity', widget=ThreeDecimalsWidget())
    label = fields.Field(attribute='production_mode',
                         widget=TranslatedManyToManyWidget(LUT_ProductionMode, separator="; ", field='short_name'))
    picture = fields.Field(attribute='picture2', readonly=True)
    is_into_offer = fields.Field(attribute='is_into_offer', widget=DecimalBooleanWidget(), readonly=True)
    is_active = fields.Field(attribute='is_active', widget=DecimalBooleanWidget(), readonly=True)

    def before_save_instance(self, instance, using_transactions, dry_run):
        """
        Override to add additional logic.
        """
        if instance.wrapped is None:
            instance.wrapped = False
        if instance.producer_unit_price is None:
            instance.producer_unit_price = REPANIER_MONEY_ZERO
        if instance.customer_unit_price is None:
            instance.customer_unit_price = REPANIER_MONEY_ZERO
        if instance.unit_deposit is None:
            instance.unit_deposit = REPANIER_MONEY_ZERO
        if instance.customer_minimum_order_quantity is None:
            instance.customer_minimum_order_quantity = DECIMAL_ZERO
        if instance.customer_increment_order_quantity is None:
            instance.customer_increment_order_quantity = DECIMAL_ZERO
        if instance.customer_alert_order_quantity is None:
            instance.customer_alert_order_quantity = DECIMAL_ZERO
        if instance.stock is None:
            instance.stock = DECIMAL_ZERO
        if instance.producer_order_by_quantity is None:
            instance.producer_order_by_quantity = DECIMAL_ZERO
        if instance.order_unit is None:
            raise ValueError(_('The order unit must be set.'))
        if instance.order_unit != PRODUCT_ORDER_UNIT_DEPOSIT:
            if instance.producer_unit_price < DECIMAL_ZERO:
                raise ValueError(_('The price must be greater than or equal to zero.'))
            if instance.customer_unit_price < DECIMAL_ZERO:
                raise ValueError(_('The price must be greater than or equal to zero.'))
            if instance.order_unit in [
                PRODUCT_ORDER_UNIT_PC,
                PRODUCT_ORDER_UNIT_PC_PRICE_KG,
                PRODUCT_ORDER_UNIT_PC_PRICE_LT,
                PRODUCT_ORDER_UNIT_PC_PRICE_PC,
                PRODUCT_ORDER_UNIT_PC_KG
            ]:
                # Do not allow decimal value when the qty represents pieces.
                if instance.customer_minimum_order_quantity != instance.customer_minimum_order_quantity // 1:
                    raise ValueError(_('The minimum order quantity must be an integer.'))
                if instance.customer_increment_order_quantity != instance.customer_increment_order_quantity // 1:
                    raise ValueError(_('The increment must be an integer.'))
                if instance.stock != instance.stock // 1:
                    raise ValueError(_('The stock must be an integer.'))
                if instance.customer_alert_order_quantity != instance.customer_alert_order_quantity // 1:
                    raise ValueError(_('The alert quantity must be an integer.'))

        if instance.order_unit < PRODUCT_ORDER_UNIT_DEPOSIT:
            if instance.customer_minimum_order_quantity <= DECIMAL_ZERO:
                raise ValueError(_('The minimum order quantity must be greater than zero.'))

            if instance.customer_minimum_order_quantity != instance.customer_alert_order_quantity \
                    and instance.customer_increment_order_quantity <= DECIMAL_ZERO:
                raise ValueError(_('The increment must be greater than zero.'))

        qs = Product.objects.filter(reference=instance.reference, producer=instance.producer).order_by('?')
        if instance.id is not None:
            qs = qs.exclude(id=instance.id)
        if qs.exists():
            raise ValueError(_("The reference %(reference)s is already used by %(product)s") %
                             {'reference': instance.reference, 'product': qs.first()})

    class Meta:
        model = Product
        fields = (
            'id',
            'producer_name', 'reference', 'department_for_customer',
            'long_name',
            'order_unit',
            'wrapped', 'order_average_weight', 'producer_unit_price', 'customer_unit_price',
            'unit_deposit', 'vat_level', 'customer_minimum_order_quantity',
            'customer_increment_order_quantity', 'customer_alert_order_quantity',
            'stock', 'limit_order_quantity_to_stock', 'producer_order_by_quantity', 'label', 'picture',
            'is_into_offer', 'is_active'
        )
        export_order = fields
        import_id_fields = ('id',)
        skip_unchanged = True
        report_skipped = False
        use_transactions = False


class ProductDataForm(TranslatableModelForm):
    def __init__(self, *args, **kwargs):
        super(ProductDataForm, self).__init__(*args, **kwargs)

    def clean(self):
        if any(self.errors):
            # Don't bother validating the formset unless each form is valid on its own
            return
        order_unit = self.cleaned_data.get("order_unit", PRODUCT_ORDER_UNIT_PC)
        if order_unit != PRODUCT_ORDER_UNIT_DEPOSIT:
            producer_unit_price = self.cleaned_data["producer_unit_price"]
            if producer_unit_price < DECIMAL_ZERO:
                self.add_error(
                    'producer_unit_price',
                    _('The price must be greater than or equal to zero.'))

            customer_unit_price = self.cleaned_data.get("customer_unit_price", DECIMAL_ZERO)
            if customer_unit_price < DECIMAL_ZERO:
                self.add_error(
                    'customer_unit_price',
                    _('The price must be greater than or equal to zero.'))

        if order_unit < PRODUCT_ORDER_UNIT_DEPOSIT:
            customer_minimum_order_quantity = self.cleaned_data.get("customer_minimum_order_quantity", DECIMAL_ZERO)
            customer_increment_order_quantity = self.cleaned_data.get("customer_increment_order_quantity", DECIMAL_ZERO)
            field_customer_alert_order_quantity_is_present = "customer_alert_order_quantity" in self.cleaned_data
            customer_alert_order_quantity = self.cleaned_data.get("customer_alert_order_quantity", LIMIT_ORDER_QTY_ITEM)
            # Important, default for limit_order_quantity_to_stock is True, because this field is not displayed
            # if the pre-opening of offer is activated fro this producer.
            limit_order_quantity_to_stock = self.cleaned_data.get("limit_order_quantity_to_stock", True)

            if order_unit in [
                PRODUCT_ORDER_UNIT_PC,
                PRODUCT_ORDER_UNIT_PC_PRICE_KG,
                PRODUCT_ORDER_UNIT_PC_PRICE_LT,
                PRODUCT_ORDER_UNIT_PC_PRICE_PC,
                PRODUCT_ORDER_UNIT_PC_KG
            ]:
                # Do not allow decimal value when the qty represents pieces.
                if customer_minimum_order_quantity != customer_minimum_order_quantity // 1:
                    self.add_error(
                        'customer_minimum_order_quantity',
                        _('The minimum order quantity must be an integer.'))
                if customer_increment_order_quantity != customer_increment_order_quantity // 1:
                    self.add_error(
                        'customer_increment_order_quantity',
                        _('The increment must be an integer.'))
                if limit_order_quantity_to_stock:
                    stock = self.cleaned_data.get("stock", DECIMAL_ZERO)
                    if stock != stock // 1:
                        self.add_error(
                            'stock',
                            _('The stock must be an integer.'))
                elif customer_alert_order_quantity != customer_alert_order_quantity // 1:
                    self.add_error(
                        'customer_alert_order_quantity',
                        _('The alert quantity must be an integer.'))
            if customer_minimum_order_quantity <= DECIMAL_ZERO:
                self.add_error(
                    'customer_minimum_order_quantity',
                    _('The minimum order quantity must be greater than zero.'))

            if customer_minimum_order_quantity != customer_alert_order_quantity and customer_increment_order_quantity <= DECIMAL_ZERO:
                self.add_error(
                    'customer_increment_order_quantity',
                    _('The increment must be greater than zero.'))
            elif not limit_order_quantity_to_stock:
                if customer_increment_order_quantity <= customer_minimum_order_quantity:
                    if customer_minimum_order_quantity != customer_alert_order_quantity:
                        order_qty_item = (
                                         customer_alert_order_quantity - customer_minimum_order_quantity) / customer_increment_order_quantity
                        q_max = customer_minimum_order_quantity + int(
                            order_qty_item) * customer_increment_order_quantity
                        if order_qty_item > (LIMIT_ORDER_QTY_ITEM - 1):
                            q_max = customer_minimum_order_quantity + LIMIT_ORDER_QTY_ITEM * customer_increment_order_quantity
                    else:
                        order_qty_item = DECIMAL_ONE
                        q_max = customer_alert_order_quantity
                else:
                    order_qty_item = customer_alert_order_quantity / customer_increment_order_quantity
                    q_max = int(order_qty_item) * customer_increment_order_quantity
                    if order_qty_item > LIMIT_ORDER_QTY_ITEM:
                        q_max = customer_minimum_order_quantity + LIMIT_ORDER_QTY_ITEM * customer_increment_order_quantity
                if field_customer_alert_order_quantity_is_present:
                    if order_qty_item > LIMIT_ORDER_QTY_ITEM:
                        self.add_error(
                            'customer_alert_order_quantity',
                            _(
                                'This alert quantity will generate more than %(qty_item)d choices for the customer into the order form.') % {
                                'qty_item': LIMIT_ORDER_QTY_ITEM,})
                    elif customer_alert_order_quantity < customer_minimum_order_quantity:
                        self.add_error(
                            'customer_alert_order_quantity',
                            _('The alert quantity must be greater than or equal to the minimum order quantity.'))
                    if q_max != customer_alert_order_quantity and q_max > customer_minimum_order_quantity:
                        self.add_error(
                            'customer_alert_order_quantity',
                            _('This alert quantity is not reachable. %(q_max)s is the best lower choice.') % {
                                'q_max': number_format(q_max, 3)})
            reference = self.cleaned_data.get("reference", EMPTY_STRING)
            if reference.strip() != EMPTY_STRING:
                producer = self.cleaned_data.get("producer", None)
                if producer is None:
                    self.add_error(
                        'producer',
                        _('Please select first a producer in the filter of previous screen'))
                else:
                    qs = Product.objects.filter(reference=reference, producer_id=producer.id).order_by('?')
                    if self.instance.id is not None:
                        qs = qs.exclude(id=self.instance.id)
                    if qs.exists():
                        self.add_error(
                            "reference",
                            _('The reference is already used by %(product)s') % {'product': qs.first()})

    class Meta:
        model = Product
        fields = "__all__"
        widgets = {
            'order_unit'             : SelectAdminOrderUnitWidget(),
            'department_for_customer': apply_select2(forms.Select),
        }


class ProductAdmin(ImportExportMixin, TranslatableAdmin):
    form = ProductDataForm
    resource_class = ProductResource
    list_display = ('is_into_offer', 'producer', 'department_for_customer', 'get_long_name', 'producer_unit_price',
                    'get_customer_alert_order_quantity', 'stock')
    list_display_links = ('get_long_name',)
    readonly_fields = ('is_updated_on',)
    list_select_related = ('producer', 'department_for_customer')
    list_per_page = 16
    list_max_show_all = 16
    filter_horizontal = ('production_mode',)
    ordering = ('producer',
                'translations__long_name',)
    search_fields = ('translations__long_name',)
    list_filter = ('is_active',
                   'is_into_offer',
                   'wrapped',
                   ProductFilterByProducer,
                   ProductFilterByDepartmentForThisProducer,
                   'production_mode',
                   'placement',
                   'limit_order_quantity_to_stock',
                   'vat_level')
    actions = [
        'flip_flop_select_for_offer_status',
        'duplicate_product'
    ]

    def has_delete_permission(self, request, obj=None):
        if request.user.groups.filter(
                name__in=[ORDER_GROUP, INVOICE_GROUP, COORDINATION_GROUP]).exists() or request.user.is_superuser:
            return True
        return False

    def flip_flop_select_for_offer_status(self, request, queryset):
        task_product.flip_flop_is_into_offer(queryset)

    flip_flop_select_for_offer_status.short_description = _(
        'flip_flop_select_for_offer_status for offer')

    def duplicate_product(self, request, queryset):
        if 'cancel' in request.POST:
            user_message = _("Action canceled by the user.")
            user_message_level = messages.INFO
            self.message_user(request, user_message, user_message_level)
            return None
        product = queryset.order_by('?').first()
        if product is None or product.is_box:
            user_message = _("Action canceled by the system.")
            user_message_level = messages.ERROR
            self.message_user(request, user_message, user_message_level)
            return None
        if 'apply' in request.POST:
            if "producers" in request.POST:
                producers = request.POST.getlist("producers")
                if len(producers) == 1:
                    producer = Producer.objects.filter(id=producers[0]).order_by('?').first()
                    if producer is not None:
                        user_message, user_message_level = task_product.admin_duplicate(queryset, producer)
                        self.message_user(request, user_message, user_message_level)
                    return None
            user_message = _("You must select one and only one producer.")
            user_message_level = messages.ERROR
            self.message_user(request, user_message, user_message_level)
            return None
        return render(
            request,
            'repanier/confirm_admin_duplicate_product.html', {
                'sub_title'           : _("Please, confirm the action : duplicate product"),
                'action_checkbox_name': admin.ACTION_CHECKBOX_NAME,
                'action'              : 'duplicate_product',
                'product'             : product,
                'producers'           : Producer.objects.filter(is_active=True)
            })

    duplicate_product.short_description = _('duplicate product')

    def get_list_display(self, request):
        producer_id = sint(request.GET.get('producer', 0))
        if producer_id != 0:
            producer_queryset = Producer.objects.filter(id=producer_id).order_by('?')
            producer = producer_queryset.first()
        else:
            producer = None
        if producer is not None:
            if producer.producer_pre_opening:
                self.list_editable = ('producer_unit_price', 'stock')
                return ('is_into_offer', 'producer', 'department_for_customer', 'get_long_name', 'all_languages_column',
                        'producer_unit_price',
                        'stock')
            elif producer.manage_replenishment or producer.manage_production:
                self.list_editable = ('producer_unit_price', 'stock')
                return ('is_into_offer', 'producer', 'department_for_customer', 'get_long_name', 'all_languages_column',
                        'producer_unit_price',
                        'get_customer_alert_order_quantity', 'stock')
            else:
                self.list_editable = ('producer_unit_price',)
                return ('is_into_offer', 'producer', 'department_for_customer', 'get_long_name', 'all_languages_column',
                        'producer_unit_price',
                        'get_customer_alert_order_quantity')
        else:
            self.list_editable = ('producer_unit_price', 'stock')
            return ('is_into_offer', 'producer', 'department_for_customer', 'get_long_name', 'all_languages_column',
                    'producer_unit_price',
                    'get_customer_alert_order_quantity', 'stock')

    def get_form(self, request, product=None, **kwargs):
        department_for_customer_id = None
        is_active_value = None
        is_into_offer_value = None
        producer_queryset = Producer.objects.none()
        if product is not None:
            producer_queryset = Producer.objects.filter(id=product.producer_id).order_by('?')
        else:
            preserved_filters = request.GET.get('_changelist_filters', None)
            if preserved_filters:
                param = dict(parse_qsl(preserved_filters))
                if 'producer' in param:
                    producer_id = param['producer']
                    if producer_id:
                        producer_queryset = Producer.objects.filter(id=producer_id).order_by('?')
                if 'department_for_customer' in param:
                    department_for_customer_id = param['department_for_customer']
                if 'is_active__exact' in param:
                    is_active_value = param['is_active__exact']
                if 'is_into_offer__exact' in param:
                    is_into_offer_value = param['is_into_offer__exact']
        producer = producer_queryset.first()
        producer_manage_replenishment = producer is not None and producer.manage_replenishment
        producer_manage_production = producer is not None and producer.manage_production
        producer_pre_opening = producer is not None and producer.producer_pre_opening
        producer_resale_price_fixed = producer is not None and producer.is_resale_price_fixed
        limit_order_quantity_to_stock = product is not None and product.limit_order_quantity_to_stock
        fields_basic = [
            ('producer', 'long_name', 'picture2'),
            ('order_unit', 'wrapped'),
        ]
        if producer_resale_price_fixed:
            fields_basic += [
                ('producer_unit_price', 'customer_unit_price',),
                ('unit_deposit', 'order_average_weight',),
            ]
        else:
            fields_basic += [
                ('producer_unit_price', 'unit_deposit', 'order_average_weight'),
            ]
        if producer_pre_opening \
                or (limit_order_quantity_to_stock and producer_manage_replenishment) \
                or producer_manage_production:
            fields_basic += [
                ('customer_minimum_order_quantity', 'customer_increment_order_quantity', 'stock')
            ]
        else:
            fields_basic += [
                (
                'customer_minimum_order_quantity', 'customer_increment_order_quantity', 'customer_alert_order_quantity')
            ]
        fields_advanced_descriptions = [
            ('department_for_customer', 'placement'),
            'offer_description',
            'production_mode',
        ]
        if producer_pre_opening or producer_manage_production:
            fields_advanced_options = [
                ('reference', 'vat_level'),
                ('is_into_offer', 'is_active', 'is_updated_on')
            ]
        elif limit_order_quantity_to_stock:
            fields_advanced_options = [
                ('limit_order_quantity_to_stock', 'producer_order_by_quantity'),
                ('reference', 'vat_level'),
                ('is_into_offer', 'is_active', 'is_updated_on')
            ]
        elif producer_manage_replenishment:
            fields_advanced_options = [
                ('stock', 'limit_order_quantity_to_stock', 'producer_order_by_quantity'),
                ('reference', 'vat_level'),
                ('is_into_offer', 'is_active', 'is_updated_on')
            ]
        else:
            fields_advanced_options = [
                ('producer_order_by_quantity'),
                ('reference', 'vat_level'),
                ('is_into_offer', 'is_active', 'is_updated_on')
            ]

        self.fieldsets = (
            (None, {'fields': fields_basic}),
            (_('Advanced descriptions'), {'classes': ('collapse',), 'fields': fields_advanced_descriptions}),
            (_('Advanced options'), {'classes': ('collapse',), 'fields': fields_advanced_options})
        )

        form = super(ProductAdmin, self).get_form(request, product, **kwargs)

        producer_field = form.base_fields["producer"]
        department_for_customer_field = form.base_fields["department_for_customer"]
        production_mode_field = form.base_fields["production_mode"]
        picture_field = form.base_fields["picture2"]
        order_unit_field = form.base_fields["order_unit"]
        producer_field.widget.can_add_related = False
        producer_field.widget.can_delete_related = False
        producer_field.widget.attrs['readonly'] = True
        department_for_customer_field.widget.can_delete_related = False

        order_unit_choices = LUT_PRODUCT_ORDER_UNIT_WO_SUBSCRIPTION
        if producer is not None:
            # One folder by producer for clarity
            if hasattr(picture_field.widget, 'upload_to'):
                picture_field.widget.upload_to = "%s%s%d" % ("product", os_sep, producer.id)
            if producer.represent_this_buyinggroup:
                order_unit_choices = LUT_PRODUCT_ORDER_UNIT
        order_unit_field.choices = order_unit_choices

        if product is not None:
            producer_field.empty_label = None
            producer_field.queryset = producer_queryset
            department_for_customer_field.queryset = LUT_DepartmentForCustomer.objects.filter(
                rght=F('lft') + 1, is_active=True, translations__language_code=translation.get_language()).order_by(
                'translations__short_name')
            production_mode_field.empty_label = None
        else:
            if producer is not None:
                producer_field.empty_label = None
                producer_field.queryset = producer_queryset
            else:
                producer_field.choices = [('-1', _("Please select first a producer in the filter of previous screen"))]
                producer_field.disabled = True
            if department_for_customer_id is not None:
                department_for_customer_field.queryset = LUT_DepartmentForCustomer.objects.filter(
                    id=department_for_customer_id
                )
            else:
                department_for_customer_field.queryset = LUT_DepartmentForCustomer.objects.filter(
                    rght=F('lft') + 1, is_active=True, translations__language_code=translation.get_language()).order_by(
                    'translations__short_name')
            if is_active_value:
                is_active_field = form.base_fields["is_active"]
                if is_active_value == '0':
                    is_active_field.initial = False
                else:
                    is_active_field.initial = True
            if is_into_offer_value:
                is_into_offer_field = form.base_fields["is_into_offer"]
                if is_into_offer_value == '0':
                    is_into_offer_field.initial = False
                else:
                    is_into_offer_field.initial = True
        production_mode_field.queryset = LUT_ProductionMode.objects.filter(
            rght=F('lft') + 1, is_active=True, translations__language_code=translation.get_language()).order_by(
            'translations__short_name')
        return form

    def save_model(self, request, product, form, change):
        super(ProductAdmin, self).save_model(
            request, product, form, change)
        update_offer_item(product_id=product.id)

    def get_queryset(self, request):
        queryset = super(ProductAdmin, self).get_queryset(request)
        return queryset.filter(is_box=False, is_membership_fee=False,
                               translations__language_code=translation.get_language())

    def get_import_formats(self):
        """
        Returns available import formats.
        """
        return [f for f in (XLS, XLSX_OPENPYXL_1_8_6) if f().can_import()]