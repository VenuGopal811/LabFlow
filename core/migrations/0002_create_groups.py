"""
Data migration: Create default user groups (roles) for each lab station.
"""
from django.db import migrations


def create_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')

    groups_config = {
        'reception': {
            'description': 'Front desk — creates visits, enters patient info',
        },
        'chamber': {
            'description': 'Doctor chamber — confirms payment, approves visits, reviews results',
        },
        'collection': {
            'description': 'Sample collection — collects samples, enters container numbers',
        },
        'lab': {
            'description': 'Lab technicians — enters test results',
        },
    }

    for group_name, config in groups_config.items():
        Group.objects.get_or_create(name=group_name)


def remove_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name__in=['reception', 'chamber', 'collection', 'lab']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(create_groups, remove_groups),
    ]
