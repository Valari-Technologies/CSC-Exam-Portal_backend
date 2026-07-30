"""Rename School.contact_email -> School.official_email.

Written by hand as a RenameField on purpose. Django's autodetector, run
non-interactively, sees this as "drop contact_email, add official_email" — which would
throw away every school's contact address. RenameField preserves the column's data: the
existing values move to the new name untouched, and the operation is reversible.

The field's meaning does not change; the new name states what was already true — it is a
contact address, never a credential.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('schools', '0001_initial'),
    ]

    operations = [
        migrations.RenameField(
            model_name='school',
            old_name='contact_email',
            new_name='official_email',
        ),
        migrations.AlterField(
            model_name='school',
            name='official_email',
            field=models.EmailField(
                help_text='Official contact email for the school. Not used for login or password reset.',
                max_length=254,
            ),
        ),
    ]
