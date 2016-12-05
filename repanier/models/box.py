# -*- coding: utf-8
from __future__ import unicode_literals

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.utils.encoding import python_2_unicode_compatible
from django.utils.translation import ugettext_lazy as _

from repanier.const import *
from repanier.fields.RepanierMoneyField import ModelMoneyField
from repanier.models.producer import Producer
from repanier.models.product import Product, product_pre_save


@python_2_unicode_compatible
class Box(Product):
    def save_update_stock(self):
        # stock : max_digits=9, decimal_places=3 => 1000000 > max(stock)
        stock = 1000000
        for box_content in BoxContent.objects.filter(
                box_id=self.id,
                product__limit_order_quantity_to_stock=True,
                content_quantity__gt=DECIMAL_ZERO,
                product__is_box=False  # Disallow recursivity
        ).prefetch_related(
            "product"
        ).only(
            "content_quantity", "product__stock", "product__limit_order_quantity_to_stock"
        ).order_by('?'):
            stock = min(stock, box_content.product.stock // box_content.content_quantity)
        if stock < 1000000:
            self.stock = stock
        else:
            self.stock = DECIMAL_ZERO
        self.limit_order_quantity_to_stock = True
        self.save(update_fields=['stock', 'limit_order_quantity_to_stock'])

    class Meta:
        proxy = True
        verbose_name = _("box")
        verbose_name_plural = _("boxes")
        # ordering = ("sort_order",)

    def __str__(self):
        return '%s' % self.long_name


@receiver(pre_save, sender=Box)
def box_pre_save(sender, **kwargs):
    box = kwargs["instance"]
    box.is_box = True
    box.producer_id = Producer.objects.filter(
        represent_this_buyinggroup=True
    ).order_by('?').only('id').first().id
    box.order_unit = PRODUCT_ORDER_UNIT_PC
    box.producer_unit_price = box.customer_unit_price
    box.producer_vat = box.customer_vat
    # ! Important to initialise all fields of the box. Remember : a box is a product.
    product_pre_save(sender, **kwargs)


@python_2_unicode_compatible
class BoxContent(models.Model):
    box = models.ForeignKey(
        'Box', verbose_name=_("box"),
        null=True, blank=True, db_index=True, on_delete=models.PROTECT)
    product = models.ForeignKey(
        'Product', verbose_name=_("product"), related_name='box_content',
        null=True, blank=True, db_index=True, on_delete=models.PROTECT)
    content_quantity = models.DecimalField(
        _("content quantity"),
        default=DECIMAL_ZERO, max_digits=6, decimal_places=3,
        validators=[MinValueValidator(0)])
    calculated_customer_content_price = ModelMoneyField(
        _("customer content price"),
        default=DECIMAL_ZERO, max_digits=8, decimal_places=2)
    calculated_content_deposit = ModelMoneyField(
        _("content deposit"),
        help_text=_('deposit to add to the original content price'),
        default=DECIMAL_ZERO, max_digits=8, decimal_places=2)

    def get_calculated_customer_content_price(self):
        # workaround for a display problem with Money field in the admin list_display
        return self.calculated_customer_content_price + self.calculated_content_deposit

    get_calculated_customer_content_price.short_description = (_("customer content price"))
    get_calculated_customer_content_price.allow_tags = False

    # def get_calculated_content_deposit(self):
    #     workaround for a display problem with Money field in the admin list_display
        # return self.calculated_content_deposit
    #
    # get_calculated_content_deposit.short_description = (_("content deposit"))
    # get_calculated_content_deposit.allow_tags = False

    class Meta:
        verbose_name = _("box content")
        verbose_name_plural = _("boxes content")
        unique_together = ("box", "product",)
        index_together = [
            # ["box", "product"],
            ["product", "box"],
        ]

    def __str__(self):
        return EMPTY_STRING


@receiver(pre_save, sender=BoxContent)
def box_content_pre_save(sender, **kwargs):
    box_content = kwargs["instance"]
    product_id = box_content.product_id
    if product_id is not None:
        product = Product.objects.filter(id=product_id).order_by('?').only(
            'customer_unit_price',
            'unit_deposit'
        ).first()
        if product is not None:
            box_content.calculated_customer_content_price.amount = box_content.content_quantity * product.customer_unit_price.amount
            box_content.calculated_content_deposit.amount = int(box_content.content_quantity) * product.unit_deposit.amount