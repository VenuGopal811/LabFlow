def user_groups(request):
    """Context processor returning a list of user groups to template rendering context."""
    if request.user.is_authenticated:
        groups = list(request.user.groups.values_list('name', flat=True))
        return {
            'user_groups': groups,
        }
    return {
        'user_groups': [],
    }
