from tinymce.widgets import TinyMCE


class RichTextAdminMixin:
    """Gives the listed TextFields a rich text editor in the admin."""

    rich_fields: tuple[str, ...] = ()

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name in self.rich_fields:
            kwargs["widget"] = TinyMCE()
        return super().formfield_for_dbfield(db_field, request, **kwargs)
