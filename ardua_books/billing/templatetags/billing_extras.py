from django import template

register = template.Library()

@register.filter(name='add_class')
def add_class(field, css):
    """
    Usage: {{ field|add_class:"form-control" }}
    Appends CSS classes to a form widget.
    """
    existing = field.field.widget.attrs.get("class", "")
    new_class = f"{existing} {css}".strip()
    return field.as_widget(attrs={"class": new_class})
