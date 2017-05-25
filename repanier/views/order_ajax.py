# -*- coding: utf-8
from __future__ import unicode_literals

import json

from django.core.serializers.json import DjangoJSONEncoder
from django.http import Http404
from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from repanier.const import PERMANENCE_OPENED, DECIMAL_ZERO
from repanier.models import Customer, ProducerInvoice, CustomerInvoice, Purchase, OfferItem
from repanier.tools import create_or_update_one_cart_item, sint, sboolean, display_selected_value


@never_cache
@require_GET
def order_ajax(request):
    if not request.is_ajax():
        raise Http404
    user = request.user
    if not user.is_authenticated:
        raise Http404
    customer = Customer.objects.filter(
        user_id=user.id, is_active=True, may_order=True
    ).order_by('?').first()
    if customer is None:
        raise Http404
    offer_item_id = sint(request.GET.get('offer_item', 0))
    value_id = sint(request.GET.get('value', 0))
    basket = sboolean(request.GET.get('basket', False))
    qs = CustomerInvoice.objects.filter(
        permanence__offeritem=offer_item_id,
        customer_id=customer.id,
        status=PERMANENCE_OPENED).order_by('?')
    result = None
    if qs.exists():
        qs = ProducerInvoice.objects.filter(
            permanence__offeritem=offer_item_id,
            producer__offeritem=offer_item_id,
            status=PERMANENCE_OPENED
        ).order_by('?')
        if qs.exists():
            result = create_or_update_one_cart_item(
                customer=customer,
                offer_item_id=offer_item_id,
                value_id=value_id,
                basket=basket,
                batch_job=False
            )
    return HttpResponse(result, content_type="application/json")
