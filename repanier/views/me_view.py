# -*- coding: utf-8
from __future__ import unicode_literals

from os import sep as os_sep

from django.contrib.auth import (get_user_model)
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from repanier.const import DECIMAL_ZERO
from forms import CustomerForm


@login_required()
@csrf_protect
@never_cache
def me_view(request):
    if request.user.is_staff or request.user.is_superuser:
        raise Http404
    else:
        customer = request.user.customer
        from repanier.apps import REPANIER_SETTINGS_MEMBERSHIP_FEE
        if REPANIER_SETTINGS_MEMBERSHIP_FEE > DECIMAL_ZERO:
            membership_fee_valid_until = customer.membership_fee_valid_until
        else:
            membership_fee_valid_until = None
        if request.method == 'POST':  # If the form has been submitted...
            form = CustomerForm(request.POST, request=request)  # A form bound to the POST data
            if form.is_valid():  # All validation rules pass
                # Process the data in form.cleaned_data
                # ...
                if customer is not None:
                    customer.long_basket_name = form.cleaned_data.get('long_basket_name')
                    customer.phone1 = form.cleaned_data.get('phone1')
                    customer.phone2 = form.cleaned_data.get('phone2')
                    customer.accept_phone_call_from_members = form.cleaned_data.get('accept_phone_call_from_members')
                    customer.email2 = form.cleaned_data.get('email2').lower()
                    customer.accept_mails_from_members = form.cleaned_data.get('accept_mails_from_members')
                    customer.city = form.cleaned_data.get('city')
                    customer.address = form.cleaned_data.get('address')
                    customer.picture = form.cleaned_data.get('picture')
                    customer.about_me = form.cleaned_data.get('about_me')
                    customer.save()
                    # Important : place this code after because form = CustomerForm(data, request=request) delete form.cleaned_data
                    email = form.cleaned_data.get('email1')
                    user_model = get_user_model()
                    user = user_model.objects.filter(email=email).order_by('?').first()
                    if user is None or user.email != email:
                        # user.email != email for case unsensitive SQL query
                        customer.user.email = email.lower()
                        customer.user.save()
                    # User feed back : Display email in lower case.
                    data = form.data.copy()
                    data["email1"] = customer.user.email
                    data["email2"] = customer.email2
                    form = CustomerForm(data, request=request)
                return render(request, "repanier/me_form.html", {'form': form, 'membership_fee_valid_until': membership_fee_valid_until, 'update': 'Ok'})
            return render(request, "repanier/me_form.html", {'form': form, 'membership_fee_valid_until': membership_fee_valid_until, 'update': 'Nok'})
        else:
            form = CustomerForm()  # An unbound form
            field = form.fields["long_basket_name"]
            field.initial = customer.long_basket_name
            field = form.fields["phone1"]
            field.initial = customer.phone1
            field = form.fields["phone2"]
            field.initial = customer.phone2
            field = form.fields["accept_phone_call_from_members"]
            field.initial = customer.accept_phone_call_from_members
            field = form.fields["email1"]
            field.initial = request.user.email
            field = form.fields["email2"]
            field.initial = customer.email2
            field = form.fields["accept_mails_from_members"]
            field.initial = customer.accept_mails_from_members
            field = form.fields["city"]
            field.initial = customer.city
            field = form.fields["address"]
            field.initial = customer.address
            field = form.fields["picture"]
            field.initial = customer.picture
            if hasattr(field.widget, 'upload_to'):
                field.widget.upload_to = "%s%s%d" % ("customer", os_sep, customer.id)
            field = form.fields["about_me"]
            field.initial = customer.about_me

        return render(request, "repanier/me_form.html", {'form': form, 'membership_fee_valid_until': membership_fee_valid_until, 'update': None})