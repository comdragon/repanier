# -*- coding: utf-8 -*-
from django.core.files.storage import default_storage
from django.db.models import Field, NOT_PROVIDED
from django.db.models.fields.files import FileDescriptor, FieldFile

from repanier.const import EMPTY_STRING
from repanier.widget.picture import AjaxPictureWidget


class AjaxPictureField(Field):
    storage = default_storage
    attr_class = FieldFile
    descriptor_class = FileDescriptor

    def __init__(self, *args, **kwargs):
        upload_to = kwargs.pop('upload_to', EMPTY_STRING)
        size = kwargs.pop('size', EMPTY_STRING)
        bootstrap = kwargs.pop('bootstrap', EMPTY_STRING)

        self.widget = AjaxPictureWidget(
            upload_to=upload_to,
            size=size,
            bootstrap=bootstrap
        )
        super(AjaxPictureField, self).__init__(*args, **kwargs)

    def contribute_to_class(self, cls, name, private_only=False, virtual_only=NOT_PROVIDED):
        super(AjaxPictureField, self).contribute_to_class(cls, name, private_only=private_only, virtual_only=virtual_only)
        setattr(cls, self.name, self.descriptor_class(self))

    def get_prep_value(self, value):
        """Returns field's value prepared for saving into a database."""
        # Need to convert File objects provided via a form to unicode for database insertion
        if value is None:
            return
        return str(value)

    def get_internal_type(self):
        return "TextField"

    def formfield(self, **kwargs):
        defaults = {'widget': self.widget}
        defaults.update(kwargs)
        return super(AjaxPictureField, self).formfield(**defaults)
