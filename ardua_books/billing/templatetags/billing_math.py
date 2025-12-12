from django import template

register = template.Library()

@register.filter
def mul(a, b):
    try:
        return (a or 0) * (b or 0)
    except:
        return 0
